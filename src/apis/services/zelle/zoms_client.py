#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/zoms_client.py.                                                 #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : ZomsClient — the sole southbound HTTP adapter for the five ZOMS operations.         #
#                 Owns the response-mapping/retry matrix (401-refresh-once, one honored 429,          #
#                 transient retries for safe ops only, post-send ambiguity -> UNCERTAIN), mints       #
#                 a fresh request-id per attempt, and returns every request-id used for audit.        #
# Dependencies  : httpx, pydantic, apis.config.zelle, apis.models.zelle.errors,                       #
#                 apis.models.zelle.southbound, apis.services.zelle.token_broker.                     #
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
import re
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
)
from src.apis.models.zelle.southbound import (
    EwsEventStatusResponse,
    EwsLifecycleRequest,
    EwsQueueDepth,
    EwsScheduleRequest,
    EwsScheduleResponse,
)
from src.apis.services.zelle.token_broker import TokenBroker, parse_retry_after
from src.common.logger import logger

# Local variables

# Transient causes (connect failure / 5xx on schedule) get at most this many HTTP attempts.
MAX_TRANSIENT_ATTEMPTS = 2
SCHEDULE_OPERATION = "schedule"
START_OPERATION = "start"
COMPLETE_OPERATION = "complete"
CANCEL_OPERATION = "cancel"
# Failure classes whose request never left the facade — safe to report retryable for any verb.
PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)
# Failure classes after the request may have been sent — the outcome is unknowable here.
POST_SEND_ERRORS = (
    httpx.ReadTimeout,
    httpx.ReadError,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
)
# 4xx error bodies are logged masked + truncated (the secrets policy sanctions masked EWS error
# bodies at the client layer); success bodies are never logged except a masked snippet when a
# schedule 2xx carries no maintenanceEventId. Masking strips email-shaped and long-digit runs.
LOG_BODY_MAX_CHARS = 500
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
LONG_DIGIT_PATTERN = re.compile(r"\d{7,}")


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def _mask_body_for_log(text: str) -> str:

    """
    Mask email addresses and long digit runs (phones, account-shaped numbers) in a response
    body and truncate it for logging — enough to diagnose an EWS rejection without moving PII
    into the logs.

    :param text: The raw response body text.
    :type text: str
    :return: The masked, truncated body.
    :rtype: str
    """

    masked = EMAIL_PATTERN.sub("***@***", text)
    masked = LONG_DIGIT_PATTERN.sub("***", masked)
    if len(masked) > LOG_BODY_MAX_CHARS:
        return masked[:LOG_BODY_MAX_CHARS] + "...[truncated]"
    # endIf
    return masked
# endDef


class ZomsClient:

    """
    Typed southbound adapter for the ZOMS maintenance-event operations. Writes go through
    :meth:`_post` and the status read through :meth:`_get`; each owns its retry/response-mapping
    matrix, and each operation returns the list of EWS ``request-id`` values used (one per HTTP
    attempt) so the audit trail can bind correlation ids to upstream attempts. Request/response
    bodies are never logged in full — they carry PII and tokens; only method, URL, status,
    request-id, and elapsed time are, plus a masked/truncated body snippet on 4xx rejections
    (and on a schedule 2xx that carries no maintenanceEventId) for diagnosability.
    """

    def __init__(
        self,
        settings: ZelleSettings,
        client: httpx.AsyncClient,
        broker: TokenBroker,
        ) -> None:

        """
        Wire the client.

        :param settings: Zelle facade settings (base URL, southbound timeouts).
        :type settings: ZelleSettings
        :param client: The injected async HTTP client for API calls.
        :type client: httpx.AsyncClient
        :param broker: The token broker supplying and invalidating bearer tokens.
        :type broker: TokenBroker
        """

        self._settings = settings
        self._client = client
        self._broker = broker
        self._base_url = settings.api_base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            settings.api_read_timeout_seconds,
            connect=settings.api_connect_timeout_seconds,
        )
    # endDef

    async def schedule(
        self,
        payload: EwsScheduleRequest,
        idempotency_id: str,
        ) -> tuple[EwsScheduleResponse, list[str]]:

        """
        Schedule a maintenance event upstream. Transient failures retry once under the SAME
        ``idempotency-id`` (that is what the header is for) with a fresh ``request-id``.

        :param payload: The southbound schedule body.
        :type payload: EwsScheduleRequest
        :param idempotency_id: The persisted EWS idempotency id, constant across retries.
        :type idempotency_id: str
        :return: The leniently-parsed 201 body and the request-ids used.
        :rtype: tuple[EwsScheduleResponse, list[str]]
        :raises UpstreamUnavailableError: After exhausted transient retries (clean failure).
        :raises UpstreamUncertainError: On a post-send failure — the outcome is unknown.
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        # Optional fields are omitted, not sent as nulls — the vendor spec marks them optional.
        body = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        response, request_ids = await self._post(
            SCHEDULE_OPERATION,
            body,
            idempotency_id=idempotency_id,
            allow_transient_retry=True,
        )
        return self._parse_schedule_response(response), request_ids
    # endDef

    async def start(self, ews_event_id: str) -> list[str]:

        """
        Activate a scheduled maintenance event (EWS status -> IN_PROGRESS, MQ hold begins).

        :param ews_event_id: The EWS maintenance event id.
        :type ews_event_id: str
        :return: The request-ids used.
        :rtype: list[str]
        :raises UpstreamUnavailableError: On a clean pre-send failure (never sent, no retry).
        :raises UpstreamUncertainError: On a post-send failure or lifecycle 5xx.
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        return await self._lifecycle(START_OPERATION, ews_event_id)
    # endDef

    async def complete(self, ews_event_id: str) -> list[str]:

        """
        Complete an in-progress maintenance event (EWS releases any held MQ messages).

        :param ews_event_id: The EWS maintenance event id.
        :type ews_event_id: str
        :return: The request-ids used.
        :rtype: list[str]
        :raises UpstreamUnavailableError: On a clean pre-send failure (never sent, no retry).
        :raises UpstreamUncertainError: On a post-send failure or lifecycle 5xx.
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        return await self._lifecycle(COMPLETE_OPERATION, ews_event_id)
    # endDef

    async def cancel(self, ews_event_id: str) -> list[str]:

        """
        Cancel a scheduled maintenance event that has not started.

        :param ews_event_id: The EWS maintenance event id.
        :type ews_event_id: str
        :return: The request-ids used.
        :rtype: list[str]
        :raises UpstreamUnavailableError: On a clean pre-send failure (never sent, no retry).
        :raises UpstreamUncertainError: On a post-send failure or lifecycle 5xx.
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        return await self._lifecycle(CANCEL_OPERATION, ews_event_id)
    # endDef

    async def get_status(
        self,
        ews_event_id: str,
        ) -> tuple[EwsEventStatusResponse, list[str]]:

        """
        Read the live upstream view of one maintenance event via the org-scoped list read
        (``GET /v1/events?orgId={orgId}`` — the only read the vendor spec defines; there is no
        per-id GET), filtered client-side for ``ews_event_id``. A read is side-effect free, so
        unlike the lifecycle POSTs every transport failure and 5xx is plainly retryable and
        nothing maps to UNCERTAIN.

        :param ews_event_id: The EWS maintenance event id.
        :type ews_event_id: str
        :return: The matching leniently-parsed entry and the request-ids used.
        :rtype: tuple[EwsEventStatusResponse, list[str]]
        :raises UpstreamUnavailableError: After exhausted transient retries (connect/read/5xx).
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx, or when the org's list does not
            contain ``ews_event_id``.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        entries, request_ids = await self.list_events()
        for entry in entries:
            if entry.maintenance_event_id == ews_event_id:
                return entry, request_ids
            # endIf
        # endFor
        logger.warning(
            "upstream list (%d events) does not contain maintenanceEventId=%s",
            len(entries),
            ews_event_id,
        )
        raise UpstreamRejectedError(
            "EWS does not list the requested maintenance event for this org.",
        )
    # endDef

    async def list_events(self) -> tuple[list[EwsEventStatusResponse], list[str]]:

        """
        List the org's maintenance events upstream (``GET /v1/events?orgId={orgId}``, vendor
        spec pp. 50-52). The 200 body is ``{"maintenanceEvents": [...]}``; entries parse
        leniently and an unparseable entry is skipped with a warning, never a crash.

        :return: The parsed entries and the request-ids used.
        :rtype: tuple[list[EwsEventStatusResponse], list[str]]
        :raises UpstreamUnavailableError: After exhausted transient retries (connect/read/5xx).
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        query = urlencode({"orgId": self._settings.org_id})
        response, request_ids = await self._get(f"/v1/events?{query}")
        return self._parse_events_list(response), request_ids
    # endDef

    async def get_queue_depths(self) -> tuple[list[EwsQueueDepth], list[str]]:

        """
        Read the org's held-notification counts by queue (``GET /v1/count?orgId={orgId}``,
        vendor spec pp. 56-57). Callable whether or not a maintenance event is in progress;
        a pure read under the same retry matrix as the events list.

        :return: The parsed queue-depth entries and the request-ids used.
        :rtype: tuple[list[EwsQueueDepth], list[str]]
        :raises UpstreamUnavailableError: After exhausted transient retries (connect/read/5xx).
        :raises RateLimitedError: On a second 429 after the single honored Retry-After.
        :raises UpstreamRejectedError: On a definite EWS 4xx rejection.
        :raises AuthConfigError: When two consecutive tokens are rejected.
        """

        query = urlencode({"orgId": self._settings.org_id})
        response, request_ids = await self._get(f"/v1/count?{query}")
        return self._parse_queue_depths(response), request_ids
    # endDef

    async def _lifecycle(
        self,
        operation: str,
        ews_event_id: str,
        ) -> list[str]:

        """
        Drive one lifecycle verb. Lifecycle idempotency semantics are unconfirmed, so there is
        NO automatic transient retry here — a doubled start manipulates live MQ holds.

        :param operation: The path segment (``start`` / ``complete`` / ``cancel``).
        :type operation: str
        :param ews_event_id: The EWS maintenance event id.
        :type ews_event_id: str
        :return: The request-ids used.
        :rtype: list[str]
        """

        body = EwsLifecycleRequest(maintenance_event_id=ews_event_id).model_dump(
            mode="json",
            by_alias=True,
        )
        _, request_ids = await self._post(
            operation,
            body,
            idempotency_id=None,
            allow_transient_retry=False,
        )
        return request_ids
    # endDef

    async def _post(
        self,
        operation: str,
        body: dict[str, Any],
        *,
        idempotency_id: str | None,
        allow_transient_retry: bool,
        ) -> tuple[httpx.Response, list[str]]:

        """
        Execute one ZOMS POST under the response-mapping matrix: definite 401 refreshes the
        token and retries exactly once; one 429 Retry-After is honored; transient causes
        (connect failure, 5xx) retry only when ``allow_transient_retry``; post-send failures
        and lifecycle 5xx map to UNCERTAIN. A fresh ``request-id`` is minted per attempt.

        :param operation: The ``/v1/events/{operation}`` path segment.
        :type operation: str
        :param body: The JSON body to send.
        :type body: dict[str, Any]
        :param idempotency_id: The ``idempotency-id`` header (schedule only); None omits it.
        :type idempotency_id: str | None
        :param allow_transient_retry: Whether connect/5xx failures may retry (schedule only).
        :type allow_transient_retry: bool
        :return: The 2xx response and every request-id used.
        :rtype: tuple[httpx.Response, list[str]]
        :raises UpstreamUnavailableError: On exhausted/clean pre-send failures or schedule 5xx.
        :raises UpstreamUncertainError: On post-send failures or lifecycle 5xx.
        :raises RateLimitedError: On a second 429.
        :raises UpstreamRejectedError: On any other definite 4xx.
        :raises AuthConfigError: On a second consecutive 401.
        """

        url = f"{self._base_url}/v1/events/{operation}"
        request_ids: list[str] = []
        auth_retried = False
        rate_retried = False
        transient_failures = 0
        while True:
            token = await self._broker.get()
            request_id = str(uuid.uuid4())
            request_ids.append(request_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
                "content-type": "application/json",
                "request-id": request_id,
            }
            if idempotency_id is not None:
                headers["idempotency-id"] = idempotency_id
            # endIf
            started = time.monotonic()
            logger.debug(
                "southbound POST %s request-id=%s attempt=%d",
                url,
                request_id,
                len(request_ids),
            )
            try:
                response = await self._client.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=self._timeout,
                )
            except PRE_SEND_ERRORS as exc:
                # The request never left the facade — clean and retryable for every verb.
                transient_failures += 1
                if allow_transient_retry and transient_failures < MAX_TRANSIENT_ATTEMPTS:
                    logger.warning(
                        "POST %s pre-send failure (%s); retrying request_id=%s",
                        url,
                        type(exc).__name__,
                        request_id,
                    )
                    continue
                # endIf
                raise UpstreamUnavailableError(
                    "EWS is unreachable; the request was not sent.",
                ) from exc
            except POST_SEND_ERRORS as exc:
                # The request may have executed upstream; never guess, never resend.
                raise UpstreamUncertainError(
                    f"EWS {operation} outcome unknown ({type(exc).__name__} after send).",
                ) from exc
            except httpx.TransportError as exc:
                # Unclassified transport failure: conservative — treat as ambiguous.
                raise UpstreamUncertainError(
                    f"EWS {operation} outcome unknown ({type(exc).__name__}).",
                ) from exc
            # endTryExcept
            elapsed = time.monotonic() - started
            status = response.status_code
            logger.info(
                "POST %s status=%s request_id=%s elapsed=%.3fs",
                url,
                status,
                request_id,
                elapsed,
            )
            if 200 <= status < 300:
                return response, request_ids
            # endIf
            if status == 401:
                if not auth_retried:
                    # A 401 is rejected by the gateway BEFORE execution — the one reconciled
                    # exception to the lifecycle no-retry rule: refresh and retry exactly once.
                    self._broker.invalidate(token)
                    auth_retried = True
                    continue
                # endIf
                raise AuthConfigError(
                    "ZOMS rejected two consecutive tokens; "
                    "check client registration and signing key.",
                )
            # endIf
            if status == 429:
                delay = parse_retry_after(response)
                if not rate_retried:
                    rate_retried = True
                    await asyncio.sleep(delay)
                    continue
                # endIf
                raise RateLimitedError(
                    "EWS is rate limiting; retry later.",
                    retry_after_seconds=delay,
                )
            # endIf
            if 400 <= status < 500:
                # The rejected fields were facade-enriched — this surfaces as a facade-owned
                # 502 northbound, never a consumer 4xx. The masked error body is logged here
                # (and only here) so the EWS-stated reason is diagnosable.
                logger.warning(
                    "POST %s rejected (HTTP %s) request_id=%s body=%s",
                    url,
                    status,
                    request_id,
                    _mask_body_for_log(response.text),
                )
                raise UpstreamRejectedError(
                    f"EWS rejected the {operation} request (HTTP {status}).",
                )
            # endIf
            # 5xx.
            if allow_transient_retry:
                transient_failures += 1
                if transient_failures < MAX_TRANSIENT_ATTEMPTS:
                    logger.warning(
                        "POST %s returned HTTP %s; retrying request_id=%s",
                        url,
                        status,
                        request_id,
                    )
                    continue
                # endIf
                raise UpstreamUnavailableError(
                    f"EWS is unavailable (HTTP {status}).",
                )
            # endIf
            # Lifecycle 5xx: a response arrived, so the verb may have executed — UNCERTAIN.
            raise UpstreamUncertainError(
                f"EWS returned HTTP {status} for {operation}; execution state unknown.",
            )
        # endWhile
    # endDef

    async def _get(self, path: str) -> tuple[httpx.Response, list[str]]:

        """
        Execute one ZOMS GET under the read retry matrix: a read never mutates upstream state,
        so ANY transport failure or 5xx is transient (bounded by ``MAX_TRANSIENT_ATTEMPTS``) and
        UNCERTAIN is never raised; a definite 401 refreshes the token and retries exactly once;
        one 429 Retry-After is honored. A fresh ``request-id`` is minted per attempt.

        :param path: The ZOMS path including any query string (e.g. ``/v1/events?orgId=BBO``).
        :type path: str
        :return: The 2xx response and every request-id used.
        :rtype: tuple[httpx.Response, list[str]]
        :raises UpstreamUnavailableError: On exhausted transport failures or 5xx responses.
        :raises RateLimitedError: On a second 429.
        :raises UpstreamRejectedError: On any other definite 4xx.
        :raises AuthConfigError: On a second consecutive 401.
        """

        url = f"{self._base_url}{path}"
        request_ids: list[str] = []
        auth_retried = False
        rate_retried = False
        transient_failures = 0
        while True:
            token = await self._broker.get()
            request_id = str(uuid.uuid4())
            request_ids.append(request_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "accept": "application/json",
                "content-type": "application/json",
                "request-id": request_id,
            }
            started = time.monotonic()
            logger.debug(
                "southbound GET %s request-id=%s attempt=%d",
                url,
                request_id,
                len(request_ids),
            )
            try:
                response = await self._client.get(url, headers=headers, timeout=self._timeout)
            except httpx.TransportError as exc:
                # Reads are side-effect free: even a post-send failure is safe to retry.
                transient_failures += 1
                if transient_failures < MAX_TRANSIENT_ATTEMPTS:
                    logger.warning(
                        "GET %s transport failure (%s); retrying request_id=%s",
                        url,
                        type(exc).__name__,
                        request_id,
                    )
                    continue
                # endIf
                raise UpstreamUnavailableError(
                    "EWS is unreachable; the status read failed cleanly.",
                ) from exc
            # endTryExcept
            elapsed = time.monotonic() - started
            status = response.status_code
            logger.info(
                "GET %s status=%s request_id=%s elapsed=%.3fs",
                url,
                status,
                request_id,
                elapsed,
            )
            if 200 <= status < 300:
                return response, request_ids
            # endIf
            if status == 401:
                if not auth_retried:
                    self._broker.invalidate(token)
                    auth_retried = True
                    continue
                # endIf
                raise AuthConfigError(
                    "ZOMS rejected two consecutive tokens; "
                    "check client registration and signing key.",
                )
            # endIf
            if status == 429:
                delay = parse_retry_after(response)
                if not rate_retried:
                    rate_retried = True
                    await asyncio.sleep(delay)
                    continue
                # endIf
                raise RateLimitedError(
                    "EWS is rate limiting; retry later.",
                    retry_after_seconds=delay,
                )
            # endIf
            if 400 <= status < 500:
                # Upstream drift the facade surfaces as its own 502, never as a consumer 4xx.
                # The masked error body is logged so the EWS-stated reason is diagnosable.
                logger.warning(
                    "GET %s rejected (HTTP %s) request_id=%s body=%s",
                    url,
                    status,
                    request_id,
                    _mask_body_for_log(response.text),
                )
                raise UpstreamRejectedError(
                    f"EWS rejected the read (HTTP {status}).",
                )
            # endIf
            # 5xx: a failed read changed nothing — plain transient.
            transient_failures += 1
            if transient_failures < MAX_TRANSIENT_ATTEMPTS:
                logger.warning(
                    "GET %s returned HTTP %s; retrying request_id=%s",
                    url,
                    status,
                    request_id,
                )
                continue
            # endIf
            raise UpstreamUnavailableError(
                f"EWS is unavailable (HTTP {status}).",
            )
        # endWhile
    # endDef

    def _parse_events_list(self, response: httpx.Response) -> list[EwsEventStatusResponse]:

        """
        Parse the list-read 200 body (``{"maintenanceEvents": [...]}``) leniently: an
        unparseable body degrades to an empty list and an unparseable entry is skipped —
        the vendor's success must not become a facade failure.

        :param response: The 2xx list response.
        :type response: httpx.Response
        :return: The parsed entries.
        :rtype: list[EwsEventStatusResponse]
        """

        try:
            data = response.json()
        except ValueError:
            logger.warning("events list body was not JSON; treating the list as empty")
            return []
        # endTryExcept
        entries = data.get("maintenanceEvents") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            logger.warning(
                "events list body had no maintenanceEvents array; masked body=%s",
                _mask_body_for_log(response.text),
            )
            return []
        # endIf
        parsed: list[EwsEventStatusResponse] = []
        for entry in entries:
            try:
                parsed.append(EwsEventStatusResponse.model_validate(entry))
            except ValidationError:
                logger.warning("skipping an unparseable maintenanceEvents entry")
            # endTryExcept
        # endFor
        return parsed
    # endDef

    def _parse_queue_depths(self, response: httpx.Response) -> list[EwsQueueDepth]:

        """
        Parse the count-read 200 body (``{"queueDepths": [...]}``) leniently: an unparseable
        body degrades to an empty list and an unparseable entry is skipped — the vendor's
        success must not become a facade failure.

        :param response: The 2xx count response.
        :type response: httpx.Response
        :return: The parsed queue-depth entries.
        :rtype: list[EwsQueueDepth]
        """

        try:
            data = response.json()
        except ValueError:
            logger.warning("queue-depth body was not JSON; treating the list as empty")
            return []
        # endTryExcept
        entries = data.get("queueDepths") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            logger.warning(
                "queue-depth body had no queueDepths array; masked body=%s",
                _mask_body_for_log(response.text),
            )
            return []
        # endIf
        parsed: list[EwsQueueDepth] = []
        for entry in entries:
            try:
                parsed.append(EwsQueueDepth.model_validate(entry))
            except ValidationError:
                logger.warning("skipping an unparseable queueDepths entry")
            # endTryExcept
        # endFor
        return parsed
    # endDef

    def _parse_schedule_response(self, response: httpx.Response) -> EwsScheduleResponse:

        """
        Parse the schedule 201 body leniently: an unparseable or unexpected body degrades to a
        missing ``maintenanceEventId`` (the 202 / PENDING_UPSTREAM_ID path), never a crash —
        the vendor's success must not become a facade failure.

        :param response: The 2xx schedule response.
        :type response: httpx.Response
        :return: The lenient schedule response model.
        :rtype: EwsScheduleResponse
        """

        try:
            data = response.json()
        except ValueError:
            logger.warning("schedule success body was not JSON; treating event id as absent")
            return EwsScheduleResponse()
        # endTryExcept
        try:
            parsed = EwsScheduleResponse.model_validate(data)
        except ValidationError:
            logger.warning("schedule success body had unexpected shape; event id treated absent")
            return EwsScheduleResponse()
        # endTryExcept
        if parsed.maintenance_event_id is None:
            # The one sanctioned success-body snippet: without it, an envelope drift silently
            # parks every event in PENDING_UPSTREAM_ID with nothing to diagnose from.
            logger.warning(
                "schedule 2xx body carried no maintenanceEventId; masked body=%s",
                _mask_body_for_log(response.text),
            )
        # endIf
        return parsed
    # endDef
# endClass


# end_apis/services/zelle/zoms_client.py
