#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/token_broker.py.                                                #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : TokenBroker — southbound OAuth2 client-credentials broker for EWS: RS256            #
#                 RFC 7523 client assertion (joserfc, kid pinned), monotonic-clock token cache        #
#                 with a safety margin, asyncio single-flight refresh, and a CircuitBreaker that      #
#                 turns an auth-server outage into fast 503s instead of stacked coroutines.           #
# Dependencies  : httpx, joserfc, apis.config.zelle, apis.models.zelle.errors.                        #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from joserfc import jwt as jose_jwt
from joserfc.jwk import RSAKey

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamUnavailableError,
)

# Local variables

LOGGER = logging.getLogger(__name__)
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
GRANT_TYPE = "client_credentials"
# Assertion clock window: iat/nbf backdated for skew, short exp (architecture §3).
ASSERTION_BACKDATE_SECONDS = 30
ASSERTION_LIFETIME_SECONDS = 120
# Refresh margin: max(floor, fraction * last TTL) -> 360s for an 1800s TTL, wide enough that a
# full southbound retry chain cannot outlive the token.
MARGIN_FLOOR_SECONDS = 120.0
MARGIN_TTL_FRACTION = 0.2
# /token retry policy: at most two attempts under a hard deadline inside the lock.
TOKEN_MAX_ATTEMPTS = 2
TOKEN_DEADLINE_SECONDS = 15.0
# A Retry-After on 429 is honored once, capped; absent/unparseable falls back to the default.
RETRY_AFTER_CAP_SECONDS = 10.0
DEFAULT_RETRY_AFTER_SECONDS = 1.0


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def parse_retry_after(response: httpx.Response) -> float:

    """
    Read a 429 response's ``Retry-After`` header as a delay in seconds, capped and defaulted.

    :param response: The upstream 429 response.
    :type response: httpx.Response
    :return: Delay in seconds — the header value capped at 10s, or 1s when absent/unparseable.
    :rtype: float
    """

    raw = response.headers.get("Retry-After")
    if raw is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    # endIf
    try:
        value = float(raw)
    except ValueError:
        # HTTP-date form (or garbage) — not worth parsing for a single capped retry.
        return DEFAULT_RETRY_AFTER_SECONDS
    # endTryExcept
    return min(max(value, 0.0), RETRY_AFTER_CAP_SECONDS)
# endDef


class CircuitBreaker:

    """
    Consecutive-failure circuit breaker on the monotonic clock. Opens after
    ``failure_threshold`` consecutive failures; after ``reset_seconds`` it half-opens, letting
    one probe through — a probe failure re-opens the window, a success closes it.
    """

    def __init__(
        self,
        failure_threshold: int,
        reset_seconds: float,
        ) -> None:

        """
        Configure the breaker.

        :param failure_threshold: Consecutive failures required to open.
        :type failure_threshold: int
        :param reset_seconds: Seconds after opening before a half-open probe is allowed.
        :type reset_seconds: float
        """

        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None
    # endDef

    def check_or_raise(self) -> None:

        """
        Fail fast while the breaker is open; allow the call through when closed or half-open.

        :return: None.
        :rtype: None
        :raises UpstreamUnavailableError: While the breaker is open, carrying the remaining
            open time as a Retry-After hint.
        """

        if self._opened_at is None:
            return
        # endIf
        remaining = self._reset_seconds - (time.monotonic() - self._opened_at)
        if remaining > 0.0:
            raise UpstreamUnavailableError(
                "EWS authentication is temporarily unavailable (circuit open).",
                retry_after_seconds=remaining,
            )
        # endIf
        # Past the reset window: half-open — let the caller probe.
    # endDef

    def record_success(self) -> None:

        """
        Close the breaker after a successful call.

        :return: None.
        :rtype: None
        """

        self._consecutive_failures = 0
        self._opened_at = None
    # endDef

    def record_failure(self) -> None:

        """
        Count a failure; at the threshold the breaker (re-)opens from now.

        :return: None.
        :rtype: None
        """

        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._opened_at = time.monotonic()
        # endIf
    # endDef
# endClass


class TokenBroker:

    """
    Southbound OAuth2 token broker: signs the RFC 7523 client assertion (RS256, pinned kid),
    exchanges it at the EWS token endpoint, and caches the access token against the monotonic
    clock with a refresh margin. Refreshes are single-flight behind an ``asyncio.Lock``; the
    circuit breaker is checked BEFORE the lock so an outage returns fast 503s instead of
    stacking coroutines. The signing key is loaded and parsed at construction — fail fast.
    """

    def __init__(
        self,
        settings: ZelleSettings,
        client: httpx.AsyncClient,
        ) -> None:

        """
        Wire the broker and load the signing key (fail fast on a bad key or path).

        :param settings: Zelle facade settings (token endpoint, aud, scope, kid, key path).
        :type settings: ZelleSettings
        :param client: The injected async HTTP client used for the token exchange.
        :type client: httpx.AsyncClient
        :raises OSError: If the signing key file cannot be read.
        :raises ValueError: If the signing key cannot be parsed as an RSA private key.
        """

        self._settings = settings
        self._client = client
        self._signing_key = RSAKey.import_key(settings.signing_key_path.read_bytes())
        self._timeout = httpx.Timeout(
            settings.token_read_timeout_seconds,
            connect=settings.token_connect_timeout_seconds,
        )
        self._breaker = CircuitBreaker(
            settings.breaker_failure_threshold,
            settings.breaker_reset_seconds,
        )
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._margin = MARGIN_FLOOR_SECONDS
    # endDef

    async def get(self) -> str:

        """
        Return a token that is fresh enough to survive a full southbound retry chain,
        refreshing it single-flight when needed.

        :return: The bearer access token.
        :rtype: str
        :raises UpstreamUnavailableError: When the breaker is open or the token endpoint stays
            unreachable across the retry budget.
        :raises RateLimitedError: When the token endpoint throttles past the single honored
            Retry-After.
        :raises AuthConfigError: On 400/401 from the token endpoint — a key/config incident,
            never retried.
        """

        if self._fresh() and self._token is not None:
            return self._token
        # endIf
        # Checked BEFORE the lock: during an outage waiters fail fast instead of queueing.
        self._breaker.check_or_raise()
        async with self._lock:
            if self._fresh() and self._token is not None:
                return self._token
            # endIf
            # Anchor lifetime at SEND on the monotonic clock — no NTP steps, no RTT inflation.
            sent_at = time.monotonic()
            payload = await self._post_token()
            token = str(payload["access_token"])
            ttl = float(payload["expires_in"])
            self._token = token
            self._margin = max(MARGIN_FLOOR_SECONDS, MARGIN_TTL_FRACTION * ttl)
            self._expires_at = sent_at + ttl
            # Metadata only — the token itself is never logged.
            LOGGER.info(
                "token refreshed: ttl=%.0fs margin=%.0fs kid=%s",
                ttl,
                self._margin,
                self._settings.signing_kid,
            )
            return token
        # endWith
    # endDef

    def invalidate(self, used: str) -> None:

        """
        Evict the cached token, but only if ``used`` is still the cached one — a concurrent
        refresh must never lose a newer token to a stale eviction.

        :param used: The token the caller saw rejected.
        :type used: str
        :return: None.
        :rtype: None
        """

        if self._token == used:
            self._token = None
            self._expires_at = 0.0
        # endIf
    # endDef

    def _fresh(self) -> bool:

        """
        Report whether the cached token exists AND is inside the refresh margin.

        :return: True when the cached token is safe to hand out.
        :rtype: bool
        """

        return self._token is not None and \
            time.monotonic() < self._expires_at - self._margin
    # endDef

    def _build_assertion(self) -> str:

        """
        Sign a fresh RFC 7523 client assertion (fresh ``jti`` per call).

        :return: The compact JWS client assertion.
        :rtype: str
        """

        # SecretStr unwrapped only here, at the point of use; the value feeds claims, not logs.
        client_id = self._settings.client_id.get_secret_value()
        now = int(datetime.now(timezone.utc).timestamp())
        claims: dict[str, Any] = {
            "iss": client_id,
            "sub": client_id,
            "aud": self._settings.token_aud,
            "scope": self._settings.token_scope,
            "jti": str(uuid.uuid4()),
            "iat": now - ASSERTION_BACKDATE_SECONDS,
            "nbf": now - ASSERTION_BACKDATE_SECONDS,
            "exp": now + ASSERTION_LIFETIME_SECONDS,
        }
        header = {"alg": "RS256", "kid": self._settings.signing_kid}
        return jose_jwt.encode(header, claims, self._signing_key)
    # endDef

    async def _post_token(self) -> dict[str, Any]:

        """
        Exchange a client assertion for an access token: at most two attempts under a hard 15s
        deadline, fresh ``jti`` each attempt, one honored Retry-After on 429, and 400/401
        raised immediately as a config incident.

        :return: The parsed token response (``access_token``, ``expires_in``, ...).
        :rtype: dict[str, Any]
        :raises UpstreamUnavailableError: When the endpoint stays unreachable/5xx across the
            retry budget or the deadline expires.
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises AuthConfigError: On 400/401 — never retried, never fed to the breaker (it is a
            distinct alert channel from transient failures).
        """

        deadline = time.monotonic() + TOKEN_DEADLINE_SECONDS
        last_error = "no attempt completed"
        rate_limit_honored = False
        for _attempt in range(TOKEN_MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            # endIf
            form = {
                "grant_type": GRANT_TYPE,
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": self._build_assertion(),
                "scope": self._settings.token_scope,
            }
            try:
                async with asyncio.timeout(remaining):
                    response = await self._client.post(
                        self._settings.token_url,
                        data=form,
                        timeout=self._timeout,
                    )
                # endWith
            except (httpx.TransportError, TimeoutError) as exc:
                last_error = type(exc).__name__
                LOGGER.warning("token attempt failed pre-response: %s", last_error)
                continue
            # endTryExcept
            status = response.status_code
            if status == 200:
                try:
                    payload: dict[str, Any] = response.json()
                except ValueError:
                    last_error = "unparseable 200 body"
                    continue
                # endTryExcept
                self._breaker.record_success()
                return payload
            # endIf
            if status in (400, 401):
                # Key/config incident: never retried and deliberately NOT fed to the breaker —
                # AuthConfigError must keep alerting on its own channel, not mutate into 503s.
                raise AuthConfigError(
                    "EWS token endpoint rejected the client assertion; "
                    "check client registration, kid, and signing key.",
                )
            # endIf
            if status == 429:
                delay = parse_retry_after(response)
                if not rate_limit_honored and delay < deadline - time.monotonic():
                    rate_limit_honored = True
                    await asyncio.sleep(delay)
                    continue
                # endIf
                self._breaker.record_failure()
                raise RateLimitedError(
                    "EWS token endpoint is rate limiting; retry later.",
                    retry_after_seconds=delay,
                )
            # endIf
            # 5xx and anything unexpected: transient — retry within the budget.
            last_error = f"HTTP {status}"
            LOGGER.warning("token attempt failed: %s", last_error)
        # endFor
        self._breaker.record_failure()
        raise UpstreamUnavailableError(
            f"EWS token endpoint unavailable ({last_error}).",
        )
    # endDef
# endClass


# end_apis/services/zelle/token_broker.py
