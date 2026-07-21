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
# Explanation   : Full-ASGI route tests: register_zelle on a fresh FastAPI app, southbound            #
#                 traffic served by the fake EWS over ASGITransport (real TokenBroker included),      #
#                 end-to-end schedule -> start -> complete, envelope shape on errors, header          #
#                 enforcement, correlation echo, and the admin resolve route.                         #
# Dependencies  : fastapi, httpx, pytest, mongomock_motor, fake_ews.app,                              #
#                 apis.dependencies.zelle.                                                            #
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
from src.apis.dependencies.zelle import register_zelle
from src.apis.repositories.zelle.indexes import create_zelle_indexes
from src.fake_ews.app import create_fake_ews_app

# Local variables

LOGGER = logging.getLogger(__name__)
CLIENT_ID = "ops-portal"
TICKET = "CHG0012345"
EVENTS_PATH = "/v1/maintenance-events"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


async def _build_consumer(
    settings: ZelleSettings,
    database: AsyncMongoMockClient,
    ) -> tuple[httpx.AsyncClient, httpx.AsyncClient]:

    """
    Wire a facade app whose southbound client talks to the fake EWS over ASGI, and return the
    (northbound consumer client, southbound client) pair for cleanup.
    """

    southbound = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_fake_ews_app()),
        base_url="http://fake-ews",
    )
    app = FastAPI()
    await register_zelle(app, settings, southbound, database)
    # register_zelle no longer creates indexes; provision them as production would before serving.
    await create_zelle_indexes(database, settings.mongo_collection_prefix)
    consumer = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://facade",
    )
    return consumer, southbound
# endDef


@pytest.fixture
async def consumer(
    settings: ZelleSettings,
    database: AsyncMongoMockClient,
    ) -> AsyncIterator[httpx.AsyncClient]:

    """
    A northbound consumer client against a fully-wired facade backed by the fake EWS.
    """

    north, south = await _build_consumer(settings, database)
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
    north, south = await _build_consumer(restricted, AsyncMongoMockClient()["zelle_tests"])
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


async def test_host_style_wiring_without_double_registration(
    settings: ZelleSettings,
    database: AsyncMongoMockClient,
    ) -> None:

    """
    The host-app pattern — include the re-exported routers in main.py, then
    register_zelle(include_routers=False) — serves traffic with each route registered
    exactly once.
    """

    from src.apis.routes import zelle_admin_router, zelle_events_router

    southbound = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_fake_ews_app()),
        base_url="http://fake-ews",
    )
    app = FastAPI()
    # Mirrors the host main.py: routers included at module scope, wiring in the lifespan.
    app.include_router(zelle_events_router)
    app.include_router(zelle_admin_router)
    routes_before = len(app.routes)
    await register_zelle(app, settings, southbound, database, include_routers=False)
    await create_zelle_indexes(database, settings.mongo_collection_prefix)
    # include_routers=False must not add any routes on top of the host's own includes.
    assert len(app.routes) == routes_before
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


# end_tests/unit/zelle/test_routes.py
