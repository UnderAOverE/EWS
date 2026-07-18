#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/routes/zelle/events.py.                                                        #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Consumer-facing maintenance-event router: schedule (idempotent), the three          #
#                 lifecycle verbs (start/complete/cancel with typed ticket confirmation and           #
#                 dry-run), and local reads. Handlers stay thin — resolve dependencies, call          #
#                 the service, serialize; every response carries X-Correlation-Id.                    #
# Dependencies  : fastapi, apis.dependencies.zelle, apis.models.zelle.enums,                          #
#                 apis.models.zelle.errors, apis.models.zelle.northbound,                             #
#                 apis.services.zelle.event_service.                                                  #
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

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

# Internal imports

from apis.dependencies.zelle import get_correlation_id, get_service, require_client_id
from apis.models.zelle.enums import EventStatus, LifecycleAction
from apis.models.zelle.errors import ValidationFailedError
from apis.models.zelle.northbound import ScheduleEventRequest
from apis.services.zelle.event_service import EventService

# Local variables

LOGGER = logging.getLogger(__name__)
LIFECYCLE_SUCCESS_STATUS = 200
events_router = APIRouter(
    prefix="/v1/maintenance-events",
    tags=["zelle-maintenance-events"],
)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


async def _run_lifecycle(
    event_id: str,
    action: LifecycleAction,
    *,
    correlation_id: str,
    client_id: str,
    service: EventService,
    x_confirm_ticket: str | None,
    dry_run: bool,
    ) -> JSONResponse:

    """
    Shared thin body for the three lifecycle handlers: enforce the confirmation header, call
    the service, serialize.

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param action: The lifecycle verb.
    :type action: LifecycleAction
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param x_confirm_ticket: The typed confirmation header value.
    :type x_confirm_ticket: str | None
    :param dry_run: When True the service audits without calling EWS or transitioning.
    :type dry_run: bool
    :return: The 200 response with the consumer event view.
    :rtype: JSONResponse
    :raises ValidationFailedError: When the confirmation header is missing or blank.
    """

    if x_confirm_ticket is None or not x_confirm_ticket.strip():
        raise ValidationFailedError("X-Confirm-Ticket header is required.")
    # endIf
    response = await service.lifecycle(
        event_id,
        action,
        client_id=client_id,
        confirm_ticket=x_confirm_ticket,
        correlation_id=correlation_id,
        dry_run=dry_run,
    )
    return JSONResponse(
        status_code=LIFECYCLE_SUCCESS_STATUS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.post("")
async def schedule_event(
    payload: ScheduleEventRequest,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    idempotency_key: str | None = Header(None),
    ) -> JSONResponse:

    """
    Schedule a maintenance window (201; 202 when the upstream id is pending; the stored status
    on idempotent replay).

    :param payload: The northbound schedule request.
    :type payload: ScheduleEventRequest
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param idempotency_key: Optional consumer ``Idempotency-Key`` enabling safe replay.
    :type idempotency_key: str | None
    :return: The consumer event view with the service-decided status code.
    :rtype: JSONResponse
    """

    result = await service.schedule(
        payload,
        client_id=client_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.post("/{event_id}/start")
async def start_event(
    event_id: str,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    ) -> JSONResponse:

    """
    Start a scheduled maintenance event (200).

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param x_confirm_ticket: Typed confirmation — must equal the event's ticket number.
    :type x_confirm_ticket: str | None
    :param dry_run: When true, audit the attempt without calling EWS or transitioning.
    :type dry_run: bool
    :return: The consumer event view.
    :rtype: JSONResponse
    """

    return await _run_lifecycle(
        event_id,
        LifecycleAction.START,
        correlation_id=correlation_id,
        client_id=client_id,
        service=service,
        x_confirm_ticket=x_confirm_ticket,
        dry_run=dry_run,
    )
# endDef


@events_router.post("/{event_id}/complete")
async def complete_event(
    event_id: str,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    ) -> JSONResponse:

    """
    Complete an in-progress maintenance event (200).

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param x_confirm_ticket: Typed confirmation — must equal the event's ticket number.
    :type x_confirm_ticket: str | None
    :param dry_run: When true, audit the attempt without calling EWS or transitioning.
    :type dry_run: bool
    :return: The consumer event view.
    :rtype: JSONResponse
    """

    return await _run_lifecycle(
        event_id,
        LifecycleAction.COMPLETE,
        correlation_id=correlation_id,
        client_id=client_id,
        service=service,
        x_confirm_ticket=x_confirm_ticket,
        dry_run=dry_run,
    )
# endDef


@events_router.post("/{event_id}/cancel")
async def cancel_event(
    event_id: str,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    ) -> JSONResponse:

    """
    Cancel a scheduled maintenance event that has not started (200).

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param x_confirm_ticket: Typed confirmation — must equal the event's ticket number.
    :type x_confirm_ticket: str | None
    :param dry_run: When true, audit the attempt without calling EWS or transitioning.
    :type dry_run: bool
    :return: The consumer event view.
    :rtype: JSONResponse
    """

    return await _run_lifecycle(
        event_id,
        LifecycleAction.CANCEL,
        correlation_id=correlation_id,
        client_id=client_id,
        service=service,
        x_confirm_ticket=x_confirm_ticket,
        dry_run=dry_run,
    )
# endDef


@events_router.get("")
async def list_events(
    status: EventStatus | None = None,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    ) -> JSONResponse:

    """
    List events from local state (200), optionally filtered by status.

    :param status: Optional status filter.
    :type status: EventStatus | None
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :return: The event list envelope.
    :rtype: JSONResponse
    """

    envelope = await service.list_events(status, correlation_id=correlation_id)
    return JSONResponse(
        status_code=200,
        content=envelope.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.get("/{event_id}")
async def get_event(
    event_id: str,
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
    ) -> JSONResponse:

    """
    Read one event from local state (200) — last known intent, never upstream authority.

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :return: The consumer event view.
    :rtype: JSONResponse
    """

    response = await service.get_event(event_id, correlation_id=correlation_id)
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/routes/zelle/events.py
