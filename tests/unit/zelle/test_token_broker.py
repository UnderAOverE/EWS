#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_token_broker.py.                                              #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Unit tests for CircuitBreaker and TokenBroker: the _fresh() truth table, margin     #
#                 math, single-flight refresh, invalidate-only-if-same, breaker transitions,          #
#                 no-retry on 400/401 (AuthConfigError), one honored 429, and fresh jti per           #
#                 attempt. The token endpoint is faked with respx.                                    #
# Dependencies  : httpx, pytest, respx, apis.services.zelle.token_broker,                             #
#                 apis.models.zelle.errors.                                                           #
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
import base64
import json
import logging
import time
import urllib.parse
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

# Internal imports

from apis.config.zelle import ZelleSettings
from apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamUnavailableError,
)
from apis.services.zelle.token_broker import CircuitBreaker, TokenBroker

# Local variables

LOGGER = logging.getLogger(__name__)
TOKEN_URL = "http://fake-ews/token"
TOKEN_BODY = {"access_token": "tok-abc", "token_type": "Bearer", "expires_in": 1800}


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:

    """
    An async HTTP client for the broker (respx intercepts its transport).
    """

    async with httpx.AsyncClient() as instance:
        yield instance
    # endWith
# endDef


def _decode_assertion_claims(request: httpx.Request) -> dict[str, object]:

    """
    Extract the client_assertion JWT claims from a captured /token form body (no signature
    verification — the test only inspects claim values).
    """

    form = urllib.parse.parse_qs(request.read().decode("utf-8"))
    assertion = form["client_assertion"][0]
    payload = assertion.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims: dict[str, object] = json.loads(base64.urlsafe_b64decode(payload))
    return claims
# endDef


async def test_fresh_truth_table(settings: ZelleSettings, client: httpx.AsyncClient) -> None:

    """
    _fresh() requires BOTH a cached token AND being inside the margin.
    """

    broker = TokenBroker(settings, client)
    # No token yet -> stale regardless of the clock.
    assert broker._fresh() is False
    broker._token = "tok"
    broker._margin = 120.0
    # Well inside the margin -> fresh.
    broker._expires_at = time.monotonic() + 1000.0
    assert broker._fresh() is True
    # Inside the margin window -> stale.
    broker._expires_at = time.monotonic() + 100.0
    assert broker._fresh() is False
    # Token present but expired long ago -> stale.
    broker._expires_at = 0.0
    assert broker._fresh() is False
# endDef


@respx.mock
async def test_margin_math(settings: ZelleSettings, client: httpx.AsyncClient) -> None:

    """
    margin = max(120, 0.2 * ttl): 360 for an 1800s TTL, floor 120 for a short TTL.
    """

    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    broker = TokenBroker(settings, client)
    await broker.get()
    assert broker._margin == pytest.approx(360.0)
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={**TOKEN_BODY, "expires_in": 300}),
    )
    short = TokenBroker(settings, client)
    await short.get()
    assert short._margin == pytest.approx(120.0)
# endDef


@respx.mock
async def test_single_flight(settings: ZelleSettings, client: httpx.AsyncClient) -> None:

    """
    Fifty concurrent get() calls produce exactly one /token call and share one token.
    """

    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    broker = TokenBroker(settings, client)
    tokens = await asyncio.gather(*(broker.get() for _ in range(50)))
    assert route.call_count == 1
    assert set(tokens) == {"tok-abc"}
# endDef


async def test_invalidate_only_if_same(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    ) -> None:

    """
    invalidate() must never evict a newer token than the one the caller saw rejected.
    """

    broker = TokenBroker(settings, client)
    broker._token = "tok-new"
    broker._expires_at = time.monotonic() + 1000.0
    broker.invalidate("tok-old")
    assert broker._token == "tok-new"
    broker.invalidate("tok-new")
    assert broker._token is None
    assert broker._expires_at == 0.0
# endDef


def test_breaker_transitions(monkeypatch: pytest.MonkeyPatch) -> None:

    """
    Breaker opens at the threshold, fails fast while open, half-opens after reset, re-opens on
    a probe failure, and closes on success.
    """

    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    breaker = CircuitBreaker(failure_threshold=3, reset_seconds=30.0)
    breaker.check_or_raise()
    breaker.record_failure()
    breaker.record_failure()
    breaker.check_or_raise()
    breaker.record_failure()
    with pytest.raises(UpstreamUnavailableError) as excinfo:
        breaker.check_or_raise()
    # endWith
    assert excinfo.value.retry_after_seconds == pytest.approx(30.0)
    # Half-open after the reset window: the probe is allowed through.
    clock["now"] += 31.0
    breaker.check_or_raise()
    # Probe failure re-opens from now.
    breaker.record_failure()
    with pytest.raises(UpstreamUnavailableError):
        breaker.check_or_raise()
    # endWith
    # Probe success closes fully.
    clock["now"] += 31.0
    breaker.check_or_raise()
    breaker.record_success()
    breaker.check_or_raise()
# endDef


@respx.mock
async def test_breaker_opens_after_consecutive_failures(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    ) -> None:

    """
    Repeated /token 5xx opens the breaker; the next get() fails fast with no HTTP call.
    """

    tuned = settings.model_copy(update={"breaker_failure_threshold": 2})
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(500))
    broker = TokenBroker(tuned, client)
    with pytest.raises(UpstreamUnavailableError):
        await broker.get()
    # endWith
    with pytest.raises(UpstreamUnavailableError):
        await broker.get()
    # endWith
    calls_before = route.call_count
    with pytest.raises(UpstreamUnavailableError):
        await broker.get()
    # endWith
    # Breaker open: no additional HTTP attempt was made.
    assert route.call_count == calls_before
# endDef


@respx.mock
@pytest.mark.parametrize("status", [400, 401])
async def test_no_retry_on_auth_config_status(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    status: int,
    ) -> None:

    """
    400/401 from /token raise AuthConfigError after exactly one attempt — never retried.
    """

    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(status))
    broker = TokenBroker(settings, client)
    with pytest.raises(AuthConfigError):
        await broker.get()
    # endWith
    assert route.call_count == 1
# endDef


@respx.mock
async def test_429_honored_once_with_fresh_jti(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    ) -> None:

    """
    A 429 Retry-After is honored once, the retry succeeds, and each attempt carries a fresh
    jti in its client assertion.
    """

    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=TOKEN_BODY),
        ],
    )
    broker = TokenBroker(settings, client)
    token = await broker.get()
    assert token == "tok-abc"
    assert route.call_count == 2
    first = _decode_assertion_claims(route.calls[0].request)
    second = _decode_assertion_claims(route.calls[1].request)
    assert first["jti"] != second["jti"]
    assert first["iss"] == first["sub"] == "test-client-id"
    assert first["aud"] == "http://fake-ews"
    assert first["scope"] == "maintenance-event"
# endDef


@respx.mock
async def test_second_429_raises_rate_limited(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    ) -> None:

    """
    A second consecutive 429 surfaces as RateLimitedError.
    """

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"}),
    )
    broker = TokenBroker(settings, client)
    with pytest.raises(RateLimitedError):
        await broker.get()
    # endWith
# endDef


@respx.mock
async def test_token_never_logged(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    The refresh log line carries metadata only — never the token value.
    """

    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_BODY))
    broker = TokenBroker(settings, client)
    with caplog.at_level(logging.DEBUG):
        await broker.get()
    # endWith
    assert "tok-abc" not in caplog.text
# endDef


# end_tests/unit/zelle/test_token_broker.py
