#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/service.py.                                                     #
# Date of birth : 2026-07-26.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : ZelleService — the bounded-context facade service, built via the get_service         #
#                 classmethod factory (host-app convention): it fetches its repositories from the      #
#                 injected Motor client, owns the southbound mTLS httpx client, and holds the event    #
#                 orchestration and stuck-event watchdog. start_watchdog/aclose manage the             #
#                 background task and client lifecycle from the host lifespan.                        #
# Dependencies  : httpx, motor, apis.config.zelle, apis.repositories.zelle.*,                          #
#                 apis.services.zelle.{event_service,token_broker,watchdog,zoms_client}.               #
# Modifications : 2026-07-26 Shane Reddy — Initial version.                                            #
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
import ssl
from typing import Any, Self

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

# Internal imports

from src.apis.config.zelle import ZelleSettings, get_zelle_settings
from src.apis.repositories.zelle.audit import AuditRepository, get_audit_repository
from src.apis.repositories.zelle.events import EventsRepository, get_events_repository
from src.apis.repositories.zelle.idempotency import (
    IdempotencyRepository,
    get_idempotency_repository,
)
from src.apis.repositories.zelle.leases import LeaseRepository, get_leases_repository
from src.apis.services.zelle.event_service import EventService
from src.apis.services.zelle.token_broker import TokenBroker
from src.apis.services.zelle.watchdog import AlertSender, Watchdog
from src.apis.services.zelle.zoms_client import ZomsClient
from src.common.logger import logger

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def _build_ssl_context(settings: ZelleSettings) -> ssl.SSLContext | bool:

    """
    Build the southbound TLS verification for the zelle-owned httpx client: a private-CA-aware
    context with optional mTLS client cert/key, mirroring the host's SSL setup. Returns ``False``
    when verification is disabled (non-prod only).

    :param settings: Zelle facade settings carrying the TLS material paths.
    :type settings: ZelleSettings
    :return: An SSL context to pass as httpx ``verify``, or False to disable verification.
    :rtype: ssl.SSLContext | bool
    """

    if not settings.verify_ssl:
        logger.warning("southbound TLS verification DISABLED (verify_ssl=False) — non-prod only")
        return False
    # endIf
    context = ssl.create_default_context()
    if settings.ca_certificate_path is not None:
        logger.debug("loading southbound CA bundle: %s", settings.ca_certificate_path)
        context.load_verify_locations(cafile=str(settings.ca_certificate_path))
    else:
        logger.debug("no ca_certificate_path set; using system CA store for southbound TLS")
    # endIfElse
    if settings.client_certificate_path is not None and settings.client_key_path is not None:
        # mTLS: present the EWS client certificate. The pair is validated together in settings.
        logger.info(
            "southbound mTLS enabled: client cert=%s key=%s",
            settings.client_certificate_path,
            settings.client_key_path,
        )
        context.load_cert_chain(
            certfile=str(settings.client_certificate_path),
            keyfile=str(settings.client_key_path),
        )
    else:
        logger.debug("southbound mTLS not configured (no client cert/key)")
    # endIfElse
    return context
# endDef


def _build_http_client(settings: ZelleSettings) -> httpx.AsyncClient:

    """
    Construct the single zelle-owned async HTTP client, with TLS/mTLS from settings. Per-request
    timeouts are applied by the token broker and ZOMS client, so none is set here.

    :param settings: Zelle facade settings.
    :type settings: ZelleSettings
    :return: The async HTTP client used for all southbound calls.
    :rtype: httpx.AsyncClient
    """

    logger.debug(
        "building southbound httpx.AsyncClient (verify_ssl=%s, mtls=%s)",
        settings.verify_ssl,
        settings.client_certificate_path is not None,
    )
    return httpx.AsyncClient(verify=_build_ssl_context(settings))
# endDef


class ZelleService:

    """
    The zelle bounded-context service, constructed by :meth:`get_service` (host-app factory
    convention). Owns the southbound mTLS HTTP client, the token broker and ZOMS client, the four
    Mongo repositories, the event orchestration service, and the optional watchdog. Reached from
    request handlers via ``request.app.state.zelle_service``.
    """

    def __init__(
        self,
        settings: ZelleSettings,
        http_client: httpx.AsyncClient,
        owns_http_client: bool,
        broker: TokenBroker,
        zoms_client: ZomsClient,
        events: EventsRepository,
        idempotency: IdempotencyRepository,
        audit: AuditRepository,
        leases: LeaseRepository,
        event_service: EventService,
        watchdog: Watchdog | None,
        ) -> None:

        """
        Store the wired collaborators. Prefer :meth:`get_service` over calling this directly.

        :param settings: Zelle facade settings.
        :type settings: ZelleSettings
        :param http_client: The southbound async HTTP client.
        :type http_client: httpx.AsyncClient
        :param owns_http_client: True when this service built the client and must close it.
        :type owns_http_client: bool
        :param broker: The OAuth2 token broker.
        :type broker: TokenBroker
        :param zoms_client: The southbound ZOMS client.
        :type zoms_client: ZomsClient
        :param events: The events repository.
        :type events: EventsRepository
        :param idempotency: The idempotency ledger repository.
        :type idempotency: IdempotencyRepository
        :param audit: The append-only audit repository.
        :type audit: AuditRepository
        :param leases: The lease repository (watchdog singleton).
        :type leases: LeaseRepository
        :param event_service: The northbound event orchestration service.
        :type event_service: EventService
        :param watchdog: The stuck-event watchdog, or None when disabled.
        :type watchdog: Watchdog | None
        """

        self.settings = settings
        self.http_client = http_client
        self.owns_http_client = owns_http_client
        self.broker = broker
        self.zoms_client = zoms_client
        self.events = events
        self.idempotency = idempotency
        self.audit = audit
        self.leases = leases
        self.event_service = event_service
        self.watchdog = watchdog
        self._watchdog_task: asyncio.Task[None] | None = None
    # endDef

    @classmethod
    async def get_service(
        cls,
        mongo_client: AsyncIOMotorClient[dict[str, Any]],
        email_service: AlertSender | None = None,
        settings: ZelleSettings | None = None,
        http_client: httpx.AsyncClient | None = None,
        ) -> Self:

        """
        Factory: build the full zelle object graph from the injected Motor client. Settings default
        to the module-level :func:`get_zelle_settings` (like the host's ``environment_settings``) —
        inject them only to override (tests, or to pass ``is_production`` directly). The database is
        selected by ``settings.mongo_database_name``; the southbound mTLS HTTP client is built from
        settings unless one is injected (tests pass a fake-EWS client, production omits it).

        :param mongo_client: The Motor client backing the repositories.
        :type mongo_client: AsyncIOMotorClient[dict[str, Any]]
        :param email_service: The host EmailService (AlertSender) for watchdog alerts, or None.
        :type email_service: AlertSender | None
        :param settings: Zelle facade settings, or None to read the module-level accessor.
        :type settings: ZelleSettings | None
        :param http_client: An HTTP client to use; None (production) builds and owns an mTLS one.
        :type http_client: httpx.AsyncClient | None
        :return: The wired service instance.
        :rtype: Self
        """

        if settings is None:
            settings = get_zelle_settings()
        # endIf
        owns_http_client = http_client is None
        if http_client is None:
            http_client = _build_http_client(settings)
        # endIf
        broker = TokenBroker(settings, http_client)
        zoms_client = ZomsClient(settings, http_client, broker)
        events = await get_events_repository(mongo_client)
        idempotency = await get_idempotency_repository(mongo_client)
        audit = await get_audit_repository(mongo_client)
        leases = await get_leases_repository(mongo_client)
        event_service = EventService(settings, events, idempotency, audit, zoms_client)
        watchdog = (
            Watchdog(settings, events, leases, email_service)
            if settings.watchdog_enabled
            else None
        )
        logger.info(
            "zelle service built (is_production=%s watchdog_enabled=%s owns_http_client=%s)",
            settings.is_production,
            settings.watchdog_enabled,
            owns_http_client,
        )
        return cls(
            settings=settings,
            http_client=http_client,
            owns_http_client=owns_http_client,
            broker=broker,
            zoms_client=zoms_client,
            events=events,
            idempotency=idempotency,
            audit=audit,
            leases=leases,
            event_service=event_service,
            watchdog=watchdog,
        )
    # endDef

    async def startup_sweep(self) -> int:

        """
        Run the startup PENDING -> UNCERTAIN sweep (a schedule idempotency-id is never blind-
        replayed after a crash). Call once from the host lifespan after construction.

        :return: The number of events swept.
        :rtype: int
        """

        return await self.event_service.startup_sweep()
    # endDef

    def start_watchdog(self) -> None:

        """
        Launch the watchdog background task when the watchdog is enabled. Call once from the host
        lifespan (mirrors the host's ``asyncio.create_task`` pattern for its monitor task).

        :return: None.
        :rtype: None
        """

        if self.watchdog is not None:
            logger.info("starting zelle watchdog task")
            self._watchdog_task = asyncio.create_task(self.watchdog.run_forever())
        # endIf
    # endDef

    async def aclose(self) -> None:

        """
        Tear down the service: stop the watchdog (releasing its Mongo lease) and close the
        zelle-owned HTTP client. Safe to call once from the host lifespan's finally block.

        :return: None.
        :rtype: None
        """

        logger.info("closing zelle service (owns_http_client=%s)", self.owns_http_client)
        if self.watchdog is not None and self._watchdog_task is not None:
            self.watchdog.stop()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            # endTryExcept
        # endIf
        if self.owns_http_client:
            await self.http_client.aclose()
        # endIf
    # endDef
# endClass


# end_apis/services/zelle/service.py
