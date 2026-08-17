#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_routes.py.                                                    #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Full-ASGI route tests: ZelleService.get_service on a fresh FastAPI app,             #
#                 traffic served by the fake EWS over ASGITransport (real TokenBroker included),      #
#                 end-to-end schedule -> start -> complete, envelope shape on errors, header          #
#                 enforcement, correlation echo, and the admin resolve route.                         #
# Dependencies  : fastapi, httpx, pytest, mongomock_motor, fake_ews.app,                              #
#                 apis.dependencies.services.zelle.                                                   #
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
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.dependencies.services.zelle import add_zelle_exception_handlers
from src.apis.routes import zelle_admin_router, zelle_events_router
from src.apis.services.zelle.service import ZelleService
from src.fake_ews.app import create_fake_ews_app

# Local variables

LOGGER = logging.getLogger(__name__)
CLIENT_ID = "ops-portal"
TICKET = "CHG0012345"
EVENTS_PATH = "/v1/maintenance-events"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


async def _ensure_zelle_indexes(service: ZelleService) -> None:

    """
    Create the indexes a provisioned database would have; the service never creates them, so the
    tests set them up the way the ops runbook does.

    :param service: The wired ZelleService.
    :type service: ZelleService
    :return: None.
    :rtype: None
    """

    await service.events.ensure_indexes()
    await service.idempotency.ensure_indexes()
    await service.audit.ensure_indexes()
    await service.leases.ensure_indexes()
# endDef


async def _wire_app(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    southbound: httpx.AsyncClient,
    ) -> FastAPI:

    """
    Wire a FastAPI app the host-app way: include the routers, register the exception handlers,
    build the service via ZelleService.get_service, run the startup sweep, and provision indexes.

    :param settings: Zelle facade settings.
    :type settings: ZelleSettings
    :param mongo_client: The mock Motor client.
    :type mongo_client: AsyncMongoMockClient
    :param southbound: The injected fake-EWS HTTP client.
    :type southbound: httpx.AsyncClient
    :return: The wired application.
    :rtype: FastAPI
    """

    app = FastAPI()
    app.include_router(zelle_events_router)
    app.include_router(zelle_admin_router)
    # Opt into the app-global validation handler so test_body_validation_is_422_envelope
    # exercises the zelle 422 envelope; the host chooses whether to enable it in production.
    add_zelle_exception_handlers(app, include_validation_handler=True)
    service = await ZelleService.get_service(
        mongo_client=mongo_client,
        settings=settings,
        http_client=southbound,
    )
    app.state.zelle_service = service
    await service.startup_sweep()
    await _ensure_zelle_indexes(service)
    return app
# endDef


async def _build_consumer(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:

    """
    Wire a facade app whose southbound client talks to the fake EWS over ASGI, and return the
    (northbound consumer client, southbound client) pair for cleanup.
    """

    southbound = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_fake_ews_app()),
        base_url="http://fake-ews",
    )
    app = await _wire_app(settings, mongo_client, southbound)
    consumer = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://facade",
    )
    return consumer, southbound
# endDef


@pytest.fixture
async def consumer(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> AsyncIterator[httpx.AsyncClient]:

    """
    A northbound consumer client against a fully-wired facade backed by the fake EWS.
    """

    north, south = await _build_consumer(settings, mongo_client)
    yield north
    await north.aclose()
    await south.aclose()
# endDef


def _schedule_body(hours_from_now: float = 1.0) -> dict[str, Any]:

    """
    A valid consumer schedule body with a future window.
    """

    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    end = start + timedelta(hours=2)
    return {
        "startTime": start.isoformat().replace("+00:00", "Z"),
        "endTime": end.isoformat().replace("+00:00", "Z"),
        "ticketNumber": TICKET,
        "reason": "core banking patch",
    }
# endDef


async def test_schedule_start_complete_end_to_end(consumer: httpx.AsyncClient) -> None:

    """
    The full happy path against the fake EWS: 201 SCHEDULED, then start and complete through
    the real broker, client, service, and state machine.
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert created.status_code == 201, created.text
    event = created.json()
    assert event["status"] == "SCHEDULED"
    assert event["ticketNumber"] == TICKET
    assert created.headers["X-Correlation-Id"] == event["correlationId"]
    event_id = event["eventId"]
    started = await consumer.post(
        f"{EVENTS_PATH}/{event_id}/start",
        headers={"X-Client-Id": CLIENT_ID, "X-Confirm-Ticket": TICKET},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_PROGRESS"
    completed = await consumer.post(
        f"{EVENTS_PATH}/{event_id}/complete",
        headers={"X-Client-Id": CLIENT_ID, "X-Confirm-Ticket": TICKET},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETE"
# endDef


async def test_schedule_accepts_offset_datetimes(consumer: httpx.AsyncClient) -> None:

    """
    Regression (AMP frontend): startTime/endTime with a numeric UTC offset (e.g. ``-05:00``)
    are accepted exactly like the ``Z`` form — both are valid ISO-8601 aware datetimes.
    """

    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(hours=2)
    body = {
        "startTime": start.astimezone(timezone(timedelta(hours=-5))).isoformat(),
        "endTime": end.astimezone(timezone(timedelta(hours=-5))).isoformat(),
        "ticketNumber": TICKET,
        "reason": "offset-form window",
    }
    response = await consumer.post(
        EVENTS_PATH,
        json=body,
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "SCHEDULED"
# endDef


async def test_schedule_accepts_sm_user_header(consumer: httpx.AsyncClient) -> None:

    """
    The Sm-User header threads through schedule without a configured directory: the request
    succeeds on the configured default contact block.
    """

    response = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID, "Sm-User": "sreddy"},
    )
    assert response.status_code == 201, response.text
# endDef


async def test_correlation_id_echoed(consumer: httpx.AsyncClient) -> None:

    """
    A consumer-supplied X-Correlation-Id is echoed in the header and the body.
    """

    response = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID, "X-Correlation-Id": "c-test-123"},
    )
    assert response.status_code == 201
    assert response.headers["X-Correlation-Id"] == "c-test-123"
    assert response.json()["correlationId"] == "c-test-123"
# endDef


async def test_missing_client_id_is_400_envelope(consumer: httpx.AsyncClient) -> None:

    """
    A missing X-Client-Id returns the 400 VALIDATION_FAILED envelope.
    """

    response = await consumer.post(EVENTS_PATH, json=_schedule_body())
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["retryable"] is False
    assert "correlationId" in error
# endDef


async def test_allowlist_rejects_unknown_client(
    settings: ZelleSettings,
    ) -> None:

    """
    A non-empty allowlist rejects unknown clients with the 403 FORBIDDEN_ACTION envelope.
    """

    restricted = settings.model_copy(update={"client_allowlist": ["allowed-app"]})
    north, south = await _build_consumer(restricted, AsyncMongoMockClient())
    try:
        response = await north.post(
            EVENTS_PATH,
            json=_schedule_body(),
            headers={"X-Client-Id": "stranger"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN_ACTION"
    finally:
        await north.aclose()
        await south.aclose()
    # endTryFinally
# endDef


async def test_ticket_mismatch_envelope(consumer: httpx.AsyncClient) -> None:

    """
    A wrong X-Confirm-Ticket surfaces the full 409 CONFLICT envelope shape.
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    event_id = created.json()["eventId"]
    response = await consumer.post(
        f"{EVENTS_PATH}/{event_id}/start",
        headers={"X-Client-Id": CLIENT_ID, "X-Confirm-Ticket": "CHG0000000"},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert set(error) == {"code", "message", "correlationId", "retryable"}
    assert error["code"] == "CONFLICT"
    assert error["retryable"] is False
# endDef


async def test_missing_confirm_ticket_is_400(consumer: httpx.AsyncClient) -> None:

    """
    A missing X-Confirm-Ticket on a lifecycle verb is a 400 VALIDATION_FAILED envelope.
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    event_id = created.json()["eventId"]
    response = await consumer.post(
        f"{EVENTS_PATH}/{event_id}/start",
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
# endDef


async def test_body_validation_is_422_envelope(consumer: httpx.AsyncClient) -> None:

    """
    An invalid body (naive datetime) returns the 422 VALIDATION_FAILED envelope via the
    RequestValidationError override.
    """

    body = _schedule_body()
    body["endTime"] = body["startTime"]
    response = await consumer.post(
        EVENTS_PATH,
        json=body,
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert "endTime" in error["message"]
# endDef


async def test_reads_and_status_filter(consumer: httpx.AsyncClient) -> None:

    """
    GET by id and the status-filtered list both serve from local state.
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    event_id = created.json()["eventId"]
    single = await consumer.get(
        f"{EVENTS_PATH}/{event_id}",
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert single.status_code == 200
    assert single.json()["eventId"] == event_id
    listed = await consumer.get(
        EVENTS_PATH,
        params={"status": "SCHEDULED"},
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert listed.status_code == 200
    assert [item["eventId"] for item in listed.json()["events"]] == [event_id]
    empty = await consumer.get(
        EVENTS_PATH,
        params={"status": "COMPLETE"},
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert empty.json()["events"] == []
# endDef


async def test_admin_resolve_route(consumer: httpx.AsyncClient) -> None:

    """
    The admin resolve route rejects events that are not UNCERTAIN/PENDING_UPSTREAM_ID with the
    409 envelope (deeper resolve flows are covered at the service layer).
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    event_id = created.json()["eventId"]
    response = await consumer.post(
        f"/v1/admin/maintenance-events/{event_id}/resolve",
        json={"actualStatus": "COMPLETE", "attestation": "EWS NOC ref 4471"},
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"
# endDef


async def test_unknown_event_is_404_envelope(consumer: httpx.AsyncClient) -> None:

    """
    Reading an unknown event id returns the 404 NOT_FOUND envelope.
    """

    response = await consumer.get(
        f"{EVENTS_PATH}/does-not-exist",
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
# endDef


def test_package_reexports_match_router_objects() -> None:

    """
    apis.routes re-exports the zelle routers under host-app naming, pointing at the same
    router objects the register path uses.
    """

    from src.apis.routes import zelle_admin_router, zelle_events_router
    from src.apis.routes.zelle.admin import admin_router
    from src.apis.routes.zelle.events import events_router

    assert zelle_events_router is events_router
    assert zelle_admin_router is admin_router
# endDef


async def test_host_style_wiring_serves_traffic(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> None:

    """
    The host-app pattern — routers included in main.py, exception handlers registered, and the
    service created via ZelleService.get_service in the lifespan — serves traffic end to end.
    """

    southbound = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_fake_ews_app()),
        base_url="http://fake-ews",
    )
    app = await _wire_app(settings, mongo_client, southbound)
    consumer = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://facade",
    )
    try:
        response = await consumer.post(
            EVENTS_PATH,
            json=_schedule_body(),
            headers={"X-Client-Id": CLIENT_ID},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "SCHEDULED"
    finally:
        await consumer.aclose()
        await southbound.aclose()
    # endTryFinally
# endDef


async def test_upstream_status_end_to_end(consumer: httpx.AsyncClient) -> None:

    """
    The live status read against the fake EWS: after schedule the facade says SCHEDULED while
    the upstream vocabulary says NOT_STARTED (surfaced verbatim); after start both sides read
    IN_PROGRESS. Correlation id is echoed.
    """

    created = await consumer.post(
        EVENTS_PATH,
        json=_schedule_body(),
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["eventId"]
    checked = await consumer.get(
        f"{EVENTS_PATH}/{event_id}/upstream-status",
        headers={"X-Client-Id": CLIENT_ID, "X-Correlation-Id": "c-live-1"},
    )
    assert checked.status_code == 200, checked.text
    view = checked.json()
    assert view["eventId"] == event_id
    assert view["localStatus"] == "SCHEDULED"
    assert view["upstreamStatus"] == "NOT_STARTED"
    assert view["correlationId"] == "c-live-1"
    assert checked.headers["X-Correlation-Id"] == "c-live-1"
    started = await consumer.post(
        f"{EVENTS_PATH}/{event_id}/start",
        headers={"X-Client-Id": CLIENT_ID, "X-Confirm-Ticket": TICKET},
    )
    assert started.status_code == 200, started.text
    rechecked = await consumer.get(
        f"{EVENTS_PATH}/{event_id}/upstream-status",
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert rechecked.status_code == 200, rechecked.text
    assert rechecked.json()["localStatus"] == "IN_PROGRESS"
    assert rechecked.json()["upstreamStatus"] == "IN_PROGRESS"
# endDef


async def test_queue_depths_end_to_end(consumer: httpx.AsyncClient) -> None:

    """
    The live queue-depth read against the fake EWS: 200 with the fake's fixed queueDepths
    sample, and the correlation id echoed. The literal path must not be shadowed by the
    ``/{event_id}`` route.
    """

    response = await consumer.get(
        f"{EVENTS_PATH}/queue-depths",
        headers={"X-Client-Id": CLIENT_ID, "X-Correlation-Id": "c-depth-1"},
    )
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["queueDepths"] == [
        {"name": "rejected-payment", "count": 3},
        {"name": "create-payment-request", "count": 7},
    ]
    assert view["correlationId"] == "c-depth-1"
    assert response.headers["X-Correlation-Id"] == "c-depth-1"
# endDef


async def test_upstream_status_unknown_event_is_404_envelope(
    consumer: httpx.AsyncClient,
    ) -> None:

    """
    The live status read for an unknown facade event id returns the 404 NOT_FOUND envelope.
    """

    response = await consumer.get(
        f"{EVENTS_PATH}/does-not-exist/upstream-status",
        headers={"X-Client-Id": CLIENT_ID},
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["retryable"] is False
# endDef


# end_tests/unit/zelle/test_routes.py
