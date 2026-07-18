#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_zoms_client.py.                                               #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Contract tests for ZomsClient over respx: exact URLs and headers per operation,     #
#                 fresh request-id per attempt with a constant idempotency-id, the 401 refresh-       #
#                 once path, the 429 path, schedule-vs-lifecycle 5xx mapping, pre/post-send           #
#                 transport error mapping, and no token leakage into logs.                            #
# Dependencies  : httpx, pytest, respx, apis.services.zelle.zoms_client,                              #
#                 apis.models.zelle.errors, apis.models.zelle.southbound.                             #
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

import logging
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

# Internal imports

from apis.config.zelle import ZelleSettings
from apis.models.zelle.enums import HoldMode
from apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
    UpstreamUncertainError,
)
from apis.models.zelle.southbound import EwsScheduleRequest
from apis.services.zelle.zoms_client import ZomsClient

# Local variables

LOGGER = logging.getLogger(__name__)
SCHEDULE_URL = "http://fake-ews/zoms/v1/events/schedule"
START_URL = "http://fake-ews/zoms/v1/events/start"
EWS_EVENT_ID = "f879562c-b912-44e9-a592-71d3aef09afb"
SCHEDULE_201 = {"maintenanceEventId": EWS_EVENT_ID, "status": "SCHEDULED"}


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class _StubBroker:

    """
    Duck-typed TokenBroker stub: hands out the current token and rotates to the next one when
    the current token is invalidated, recording every invalidation.
    """

    def __init__(self) -> None:

        """
        Seed the token sequence.
        """

        self._queue = ["tok-2", "tok-3"]
        self.current = "tok-1"
        self.invalidated: list[str] = []
    # endDef

    async def get(self) -> str:

        """
        Return the current token.
        """

        return self.current
    # endDef

    def invalidate(self, used: str) -> None:

        """
        Record the invalidation and rotate when it names the current token.
        """

        self.invalidated.append(used)
        if used == self.current and self._queue:
            self.current = self._queue.pop(0)
        # endIf
    # endDef
# endClass


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:

    """
    An async HTTP client for the ZOMS calls (respx intercepts its transport).
    """

    async with httpx.AsyncClient() as instance:
        yield instance
    # endWith
# endDef


@pytest.fixture
def broker() -> _StubBroker:

    """
    A fresh stub broker per test.
    """

    return _StubBroker()
# endDef


@pytest.fixture
def zoms(
    settings: ZelleSettings,
    client: httpx.AsyncClient,
    broker: _StubBroker,
    ) -> ZomsClient:

    """
    The client under test, wired with the stub broker.
    """

    return ZomsClient(settings, client, broker)  # type: ignore[arg-type]
# endDef


@pytest.fixture
def payload(settings: ZelleSettings) -> EwsScheduleRequest:

    """
    A valid southbound schedule payload.
    """

    return EwsScheduleRequest(
        org_id=settings.org_id,
        participant_name=settings.participant_name,
        submitted_name=settings.submitted_name,
        contact_name=settings.contact_name,
        contact_phone=settings.contact_phone,
        contact_email=settings.contact_email,
        scheduled_start_date="2026-08-01T06:00:00.000Z",
        scheduled_end_date="2026-08-01T08:00:00.000Z",
        ews_hold=HoldMode.SELF_HOLD,
        ticket_number="CHG0012345",
    )
# endDef


@respx.mock
async def test_schedule_happy_path_headers(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    Schedule hits the exact URL with bearer token, accept/content-type, a request-id matching
    the returned audit list, and the given idempotency-id; the 201 body parses leniently.
    """

    route = respx.post(SCHEDULE_URL).mock(return_value=httpx.Response(201, json=SCHEDULE_201))
    idempotency_id = str(uuid.uuid4())
    parsed, request_ids = await zoms.schedule(payload, idempotency_id)
    assert parsed.maintenance_event_id == EWS_EVENT_ID
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer tok-1"
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["idempotency-id"] == idempotency_id
    assert request.headers["request-id"] == request_ids[0]
    assert len(request_ids) == 1
# endDef


@respx.mock
async def test_401_refreshes_and_retries_once(
    zoms: ZomsClient,
    broker: _StubBroker,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    A definite 401 invalidates the used token and retries exactly once with a fresh token and
    a fresh request-id.
    """

    route = respx.post(SCHEDULE_URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(201, json=SCHEDULE_201)],
    )
    _, request_ids = await zoms.schedule(payload, str(uuid.uuid4()))
    assert broker.invalidated == ["tok-1"]
    assert route.calls[0].request.headers["Authorization"] == "Bearer tok-1"
    assert route.calls[1].request.headers["Authorization"] == "Bearer tok-2"
    assert len(request_ids) == 2
    assert request_ids[0] != request_ids[1]
# endDef


@respx.mock
async def test_second_401_is_auth_config_error(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    Two consecutive 401s raise AuthConfigError — a registration/key incident, not a retry loop.
    """

    route = respx.post(SCHEDULE_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(AuthConfigError):
        await zoms.schedule(payload, str(uuid.uuid4()))
    # endWith
    assert route.call_count == 2
# endDef


@respx.mock
async def test_429_honored_once_then_rate_limited(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    One 429 Retry-After is honored; a second consecutive 429 raises RateLimitedError.
    """

    route = respx.post(SCHEDULE_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(201, json=SCHEDULE_201),
        ],
    )
    parsed, _ = await zoms.schedule(payload, str(uuid.uuid4()))
    assert parsed.maintenance_event_id == EWS_EVENT_ID
    assert route.call_count == 2
    respx.post(SCHEDULE_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"}),
    )
    with pytest.raises(RateLimitedError):
        await zoms.schedule(payload, str(uuid.uuid4()))
    # endWith
# endDef


@respx.mock
async def test_schedule_5xx_retries_same_idempotency_id(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    Schedule retries a 5xx once with the SAME idempotency-id and a fresh request-id; a second
    5xx maps to UpstreamUnavailableError.
    """

    route = respx.post(SCHEDULE_URL).mock(return_value=httpx.Response(503))
    idempotency_id = str(uuid.uuid4())
    with pytest.raises(UpstreamUnavailableError):
        await zoms.schedule(payload, idempotency_id)
    # endWith
    assert route.call_count == 2
    first = route.calls[0].request
    second = route.calls[1].request
    assert first.headers["idempotency-id"] == second.headers["idempotency-id"] == idempotency_id
    assert first.headers["request-id"] != second.headers["request-id"]
# endDef


@respx.mock
async def test_schedule_5xx_then_success(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    A single 5xx followed by a 201 succeeds within the two-attempt budget.
    """

    route = respx.post(SCHEDULE_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(201, json=SCHEDULE_201)],
    )
    parsed, request_ids = await zoms.schedule(payload, str(uuid.uuid4()))
    assert parsed.maintenance_event_id == EWS_EVENT_ID
    assert route.call_count == 2
    assert len(request_ids) == 2
# endDef


@respx.mock
async def test_lifecycle_5xx_is_uncertain(zoms: ZomsClient) -> None:

    """
    A lifecycle 5xx maps to UpstreamUncertainError immediately — the verb may have executed;
    exactly one attempt is made.
    """

    route = respx.post(START_URL).mock(return_value=httpx.Response(500))
    with pytest.raises(UpstreamUncertainError):
        await zoms.start(EWS_EVENT_ID)
    # endWith
    assert route.call_count == 1
# endDef


@respx.mock
async def test_connect_error_mapping(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    Pre-send failures: schedule retries once then raises UpstreamUnavailableError; lifecycle
    raises UpstreamUnavailableError after a single attempt (never sent, but no auto-retry).
    """

    schedule_route = respx.post(SCHEDULE_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(UpstreamUnavailableError):
        await zoms.schedule(payload, str(uuid.uuid4()))
    # endWith
    assert schedule_route.call_count == 2
    start_route = respx.post(START_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(UpstreamUnavailableError):
        await zoms.start(EWS_EVENT_ID)
    # endWith
    assert start_route.call_count == 1
# endDef


@respx.mock
async def test_read_timeout_is_uncertain(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    ) -> None:

    """
    Post-send failures map to UpstreamUncertainError for BOTH schedule and lifecycle — the
    request may have executed upstream.
    """

    respx.post(SCHEDULE_URL).mock(side_effect=httpx.ReadTimeout("late"))
    with pytest.raises(UpstreamUncertainError):
        await zoms.schedule(payload, str(uuid.uuid4()))
    # endWith
    respx.post(START_URL).mock(side_effect=httpx.ReadTimeout("late"))
    with pytest.raises(UpstreamUncertainError):
        await zoms.start(EWS_EVENT_ID)
    # endWith
# endDef


@respx.mock
async def test_other_4xx_is_rejected(zoms: ZomsClient) -> None:

    """
    Any other definite 4xx maps to UpstreamRejectedError (surfaced as a facade-owned 502).
    """

    respx.post(START_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(UpstreamRejectedError):
        await zoms.start(EWS_EVENT_ID)
    # endWith
# endDef


@respx.mock
async def test_no_token_or_body_in_logs(
    zoms: ZomsClient,
    payload: EwsScheduleRequest,
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    Client logs carry method/URL/status/request-id only — never the bearer token or the
    payload's contact PII.
    """

    respx.post(SCHEDULE_URL).mock(return_value=httpx.Response(201, json=SCHEDULE_201))
    with caplog.at_level(logging.DEBUG):
        await zoms.schedule(payload, str(uuid.uuid4()))
    # endWith
    assert "tok-1" not in caplog.text
    assert "TTechnology@BBO.com" not in caplog.text
    assert "9999999977" not in caplog.text
# endDef


# end_tests/unit/zelle/test_zoms_client.py
