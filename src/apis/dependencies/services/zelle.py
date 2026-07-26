#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/dependencies/services/zelle.py.                                                #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Zelle wiring and dependency providers, laid out per the host app's                  #
#                 dependencies/services convention. Holds the ZelleRuntime container built from        #
#                 the host's injected httpx client, Motor database, and optional EmailService;         #
#                 register_zelle (startup sweep, routers, exception handlers, watchdog task); and      #
#                 the request-time providers (runtime/service, correlation id, client attribution).    #
# Dependencies  : fastapi, httpx, motor, apis.config.zelle, apis.models.zelle.errors,                 #
#                 apis.repositories.zelle.*, apis.services.zelle.*.                                    #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                            #
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
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from motor.motor_asyncio import AsyncIOMotorDatabase

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.errors import (
    ForbiddenActionError,
    ValidationFailedError,
    ZelleFacadeError,
    validation_exception_handler,
    zelle_exception_handler,
)
from src.apis.repositories.zelle.audit import AuditRepository
from src.apis.repositories.zelle.events import EventsRepository
from src.apis.repositories.zelle.idempotency import IdempotencyRepository
from src.apis.repositories.zelle.leases import LeaseRepository
from src.apis.services.zelle.event_service import EventService
from src.apis.services.zelle.token_broker import TokenBroker
from src.apis.services.zelle.watchdog import AlertSender, Watchdog
from src.apis.services.zelle.zoms_client import ZomsClient

# Local variables

LOGGER = logging.getLogger(__name__)
CORRELATION_ID_PREFIX = "c-"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@dataclass
class ZelleRuntime:

    """
    App-level container for every shared zelle object, wired once from the host app's lifespan
    (constructor dependency injection — no module-level stateful singletons) and reached from
    request handlers via ``request.app.state.zelle_runtime``.
    """

    settings: ZelleSettings
    broker: TokenBroker
    zoms_client: ZomsClient
    events: EventsRepository
    idempotency: IdempotencyRepository
    audit: AuditRepository
    leases: LeaseRepository
    email_service: AlertSender | None
    service: EventService
    watchdog: Watchdog | None
# endClass


def build_zelle_runtime(
    settings: ZelleSettings,
    http_client: httpx.AsyncClient,
    database: AsyncIOMotorDatabase[dict[str, Any]],
    email_service: AlertSender | None = None,
    ) -> ZelleRuntime:

    """
    Construct the full zelle object graph from the host app's injected client, database, and
    optional EmailService.

    :param settings: Zelle facade settings.
    :type settings: ZelleSettings
    :param http_client: The host app's shared async HTTP client (zelle never constructs one).
    :type http_client: httpx.AsyncClient
    :param database: The host app's Motor database.
    :type database: AsyncIOMotorDatabase[dict[str, Any]]
    :param email_service: The host app's EmailService (or any AlertSender); when provided and the
        watchdog is enabled, stuck events are emailed in addition to the CRITICAL log. None
        disables email alerting.
    :type email_service: AlertSender | None
    :return: The wired runtime container.
    :rtype: ZelleRuntime
    """

    prefix = settings.mongo_collection_prefix
    broker = TokenBroker(settings, http_client)
    zoms_client = ZomsClient(settings, http_client, broker)
    events = EventsRepository(database, prefix)
    idempotency = IdempotencyRepository(database, prefix)
    audit = AuditRepository(database, prefix)
    leases = LeaseRepository(database, prefix)
    service = EventService(settings, events, idempotency, audit, zoms_client)
    watchdog = (
        Watchdog(settings, events, leases, email_service) if settings.watchdog_enabled else None
    )
    return ZelleRuntime(
        settings=settings,
        broker=broker,
        zoms_client=zoms_client,
        events=events,
        idempotency=idempotency,
        audit=audit,
        leases=leases,
        email_service=email_service,
        service=service,
        watchdog=watchdog,
    )
# endDef


async def register_zelle(
    app: FastAPI,
    settings: ZelleSettings,
    http_client: httpx.AsyncClient,
    database: AsyncIOMotorDatabase[dict[str, Any]],
    *,
    email_service: AlertSender | None = None,
    include_routers: bool = True,
    ) -> ZelleRuntime:

    """
    Mount the zelle bounded context on the host application: build the runtime, run the startup
    PENDING sweep, include the routers, register the exception handlers, and start the watchdog
    task when enabled. Indexes are provisioned out-of-band (see
    docs/database-collections-and-indexes.md), not here. The HOST APP calls this from its lifespan
    with its baked-in client, database, and EmailService.

    :param app: The host FastAPI application.
    :type app: FastAPI
    :param settings: Zelle facade settings.
    :type settings: ZelleSettings
    :param http_client: The host app's shared async HTTP client.
    :type http_client: httpx.AsyncClient
    :param database: The host app's Motor database.
    :type database: AsyncIOMotorDatabase[dict[str, Any]]
    :param email_service: The host app's EmailService for watchdog alerts, or None to disable
        email alerting.
    :type email_service: AlertSender | None
    :param include_routers: Pass False when the host main.py already includes
        ``zelle_events_router`` / ``zelle_admin_router`` itself (per its ose/saas pattern) —
        double inclusion would register duplicate routes.
    :type include_routers: bool
    :return: The wired runtime container (also stored on ``app.state.zelle_runtime``).
    :rtype: ZelleRuntime
    """

    # Deferred import: the routers depend on the providers below, so a top-level import here
    # would be circular. This is the single sanctioned exception to the import-block rule.
    from src.apis.routes.zelle.admin import admin_router
    from src.apis.routes.zelle.events import events_router

    runtime = build_zelle_runtime(settings, http_client, database, email_service)
    app.state.zelle_runtime = runtime
    # Indexes are NOT created here: pods must not run DDL on every restart (and may lack the
    # privilege). Provision the collections and indexes once, out-of-band, before serving — see
    # docs/database-collections-and-indexes.md.
    swept = await runtime.service.startup_sweep()
    if swept:
        LOGGER.warning("startup sweep moved %d PENDING event(s) to UNCERTAIN", swept)
    # endIf
    if include_routers:
        app.include_router(events_router)
        app.include_router(admin_router)
    # endIf
    # Starlette types handlers as taking bare Exception; the registration key guarantees the
    # narrower exception type at runtime, so the ignores are safe.
    app.add_exception_handler(ZelleFacadeError, zelle_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    if runtime.watchdog is not None:
        # The task handle lives on app.state so the host lifespan can stop/await it on shutdown.
        app.state.zelle_watchdog_task = asyncio.create_task(runtime.watchdog.run_forever())
    # endIf
    return runtime
# endDef


def get_zelle_runtime(request: Request) -> ZelleRuntime:

    """
    FastAPI provider: the app-level zelle runtime container.

    :param request: The active request.
    :type request: Request
    :return: The runtime stored by :func:`register_zelle`.
    :rtype: ZelleRuntime
    """

    runtime: ZelleRuntime = request.app.state.zelle_runtime
    return runtime
# endDef


def get_zelle_service(request: Request) -> EventService:

    """
    FastAPI provider: the event orchestration service.

    :param request: The active request.
    :type request: Request
    :return: The shared EventService instance.
    :rtype: EventService
    """

    return get_zelle_runtime(request).service
# endDef


async def get_zelle_correlation_id(
    request: Request,
    x_correlation_id: str | None = Header(None),
    ) -> str:

    """
    FastAPI provider: accept the consumer's ``X-Correlation-Id`` or mint ``c-<uuid4>``, and
    bind it to ``request.state.correlation_id`` for the exception handlers and audit trail.

    :param request: The active request.
    :type request: Request
    :param x_correlation_id: The consumer-supplied correlation id header; None mints one.
    :type x_correlation_id: str | None
    :return: The effective correlation id.
    :rtype: str
    """

    correlation_id = (
        x_correlation_id if x_correlation_id else f"{CORRELATION_ID_PREFIX}{uuid.uuid4()}"
    )
    request.state.correlation_id = correlation_id
    return correlation_id
# endDef


async def require_zelle_client_id(
    request: Request,
    x_client_id: str | None = Header(None),
    ) -> str:

    """
    FastAPI provider: require and attribute the caller's ``X-Client-Id``, enforcing the
    configured allowlist when non-empty.

    :param request: The active request.
    :type request: Request
    :param x_client_id: The consumer-supplied client id header.
    :type x_client_id: str | None
    :return: The attributed client id.
    :rtype: str
    :raises ValidationFailedError: When the header is missing or blank (400).
    :raises ForbiddenActionError: When a non-empty allowlist does not contain the caller (403).
    """

    if x_client_id is None or not x_client_id.strip():
        raise ValidationFailedError("X-Client-Id header is required.")
    # endIf
    allowlist = get_zelle_runtime(request).settings.client_allowlist
    if allowlist and x_client_id not in allowlist:
        raise ForbiddenActionError("Client is not allowed to use the zelle facade.")
    # endIf
    return x_client_id
# endDef


# end_apis/dependencies/services/zelle.py
