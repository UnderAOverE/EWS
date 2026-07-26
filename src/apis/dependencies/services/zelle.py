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
# Explanation   : Zelle request-time dependency providers (host-app dependencies/services            #
#                 convention): resolve the app-level ZelleService from app.state and expose the        #
#                 event service, correlation id, and client attribution. Also add_zelle_exception_     #
#                 handlers to register the consumer error handlers. The service itself is built by     #
#                 ZelleService.get_service in the host lifespan.                                      #
# Dependencies  : fastapi, apis.models.zelle.errors, apis.services.zelle.event_service,               #
#                 apis.services.zelle.service.                                                        #
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

import logging
import uuid

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError

# Internal imports

from src.apis.models.zelle.errors import (
    ForbiddenActionError,
    ValidationFailedError,
    ZelleFacadeError,
    validation_exception_handler,
    zelle_exception_handler,
)
from src.apis.services.zelle.event_service import EventService
from src.apis.services.zelle.service import ZelleService

# Local variables

LOGGER = logging.getLogger(__name__)
CORRELATION_ID_PREFIX = "c-"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def add_zelle_exception_handlers(app: FastAPI) -> None:

    """
    Register the zelle exception handlers on the host application — call once at app setup, the way
    the host registers its other handlers. Maps ``ZelleFacadeError`` subclasses to the consumer
    error envelope and overrides FastAPI request-validation errors.

    :param app: The host FastAPI application.
    :type app: FastAPI
    :return: None.
    :rtype: None
    """

    # Starlette types handlers as taking bare Exception; the registration key guarantees the
    # narrower exception type at runtime, so the ignores are safe.
    app.add_exception_handler(ZelleFacadeError, zelle_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
# endDef


def _zelle_service(request: Request) -> ZelleService:

    """
    Resolve the app-level ZelleService stored on ``app.state.zelle_service`` by the host lifespan
    (``application.state.zelle_service = await ZelleService.get_service(...)``).

    :param request: The active request.
    :type request: Request
    :return: The shared ZelleService instance.
    :rtype: ZelleService
    """

    service: ZelleService = request.app.state.zelle_service
    return service
# endDef


def get_zelle_service(request: Request) -> EventService:

    """
    FastAPI provider: the event orchestration service.

    :param request: The active request.
    :type request: Request
    :return: The shared EventService instance.
    :rtype: EventService
    """

    return _zelle_service(request).event_service
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
    allowlist = _zelle_service(request).settings.client_allowlist
    if allowlist and x_client_id not in allowlist:
        raise ForbiddenActionError("Client is not allowed to use the zelle facade.")
    # endIf
    return x_client_id
# endDef


# end_apis/dependencies/services/zelle.py
