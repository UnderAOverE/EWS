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
# Explanation   : ZomsClient — the sole southbound HTTP adapter for the four ZOMS operations.         #
#                 Owns the response-mapping/retry matrix (401-refresh-once, one honored 429,          #
#                 transient retries for schedule only, post-send ambiguity -> UNCERTAIN), mints       #
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
import logging
import time
import uuid
from typing import Any

import httpx
from pydantic import ValidationError

# Internal imports

from apis.config.zelle import ZelleSettings
from apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
)
from apis.models.zelle.southbound import (
    EwsLifecycleRequest,
    EwsScheduleRequest,
    EwsScheduleResponse,
)
from apis.services.zelle.token_broker import TokenBroker, parse_retry_after

# Local variables

LOGGER = logging.getLogger(__name__)
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


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class ZomsClient:

    """
    Typed southbound adapter for the ZOMS maintenance-event operations. Every call goes through
    :meth:`_post`, which owns the retry/response-mapping matrix; each operation returns the list
    of EWS ``request-id`` values used (one per HTTP attempt) so the audit trail can bind
    correlation ids to upstream attempts. Request/response bodies are never logged here — they
    carry PII and tokens; only method, URL, status, request-id, and elapsed time are.
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
                    LOGGER.warning(
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
            LOGGER.info(
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
                # 502 northbound, never a consumer 4xx. The body is not parsed or logged here.
                raise UpstreamRejectedError(
                    f"EWS rejected the {operation} request (HTTP {status}).",
                )
            # endIf
            # 5xx.
            if allow_transient_retry:
                transient_failures += 1
                if transient_failures < MAX_TRANSIENT_ATTEMPTS:
                    LOGGER.warning(
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
            LOGGER.warning("schedule success body was not JSON; treating event id as absent")
            return EwsScheduleResponse()
        # endTryExcept
        try:
            return EwsScheduleResponse.model_validate(data)
        except ValidationError:
            LOGGER.warning("schedule success body had unexpected shape; event id treated absent")
            return EwsScheduleResponse()
        # endTryExcept
    # endDef
# endClass


# end_apis/services/zelle/zoms_client.py
