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
#                 dry-run), local reads, and a live upstream status read. Handlers stay thin —        #
#                 resolve dependencies, call the service, serialize; every response carries           #
#                 X-Correlation-Id.                                                                   #
# Dependencies  : fastapi, apis.dependencies.types, apis.models.zelle.enums,                          #
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

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

# Internal imports

from src.apis.dependencies.types import (
    ZelleClientIdDependency,
    ZelleCorrelationIdDependency,
    ZelleEventServiceDependency,
)
from src.apis.models.zelle.enums import EventStatus, LifecycleAction
from src.apis.models.zelle.errors import ValidationFailedError
from src.apis.models.zelle.northbound import ScheduleEventRequest
from src.apis.services.zelle.event_service import EventService
from src.common.constants import HTTPCodes
from src.common.logger import logger

# Local variables

LIFECYCLE_SUCCESS_STATUS = HTTPCodes.SUCCESS
events_router = APIRouter(
    prefix="/v1/maintenance-events",
    tags=["zelle-maintenance-events"],
)

# OpenAPI/ReDoc descriptions (consumer-facing markdown). These are deliberately separate from the
# reST docstrings, which document the code for developers; these document the HTTP contract for
# callers and render as rich markdown in ReDoc.

_SCHEDULE_DESCRIPTION = """
Schedule (create) a maintenance window. The facade validates the window, runs its guardrails
(overlap, allowlist), enriches the request with **your organisation's constants and contact block
from config**, and calls EWS exactly once.

**Idempotency** — send an `Idempotency-Key` header to make retries safe. A replay with the same key
**and** the same body returns the original result instead of booking a second window.

**Headers**
- `X-Client-Id` **(required)** — your caller identity; used for attribution and the allowlist.
- `Idempotency-Key` *(optional)* — safe-replay key for this schedule.
- `X-Correlation-Id` *(optional)* — request trace id; the facade mints `c-<uuid4>` if you omit it.

**Body** — you send only what a change ticket knows: `startTime`, `endTime`, `ticketNumber`,
`reason`, and optionally `holdMode` / `allowOverlap` / `suppressDuplicatePayments` /
`networkNotificationId` / `emergencyImmediateStart`. You do **not** send your org id,
participant/submitted names, or the contact block — the facade injects those.

**Allowed hours** — EWS only permits maintenance windows between 11:00 PM and 5:00 AM CST
(12:00 AM to 6:00 AM CDT). Off-window requests are rejected with `422` before any EWS call.
`emergencyImmediateStart: true` (an incident starting within ~15 minutes) is exempt.

**Responses**
- `201` — scheduled; the body carries the facade `eventId` you use for every later call.
- `202` — accepted, but the upstream id is still pending (rare; awaits an operator resolve).
- `422` — request validation failed (inverted window, past start, missing field).
- `409` / `403` / `502` / `503` — see the standard error envelope.
"""

_LIFECYCLE_DESCRIPTION = """
{intro}

This drives a real EWS call and moves the event through the facade state machine.

**Guardrails**
- `X-Confirm-Ticket` **(required)** must exactly equal the event's `ticketNumber` — a typed
  "are you sure" confirmation. A mismatch is rejected with `409`.
- The event must currently be **{precondition}**; any other state is a `409`.

**Headers**
- `X-Client-Id` **(required)** — caller identity (allowlist-checked).
- `X-Confirm-Ticket` **(required)** — see above.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Query**
- `dry_run` *(default `false`)* — audit the attempt **without** calling EWS or changing state; use
  it to pre-flight a call safely.

**Path**
- `event_id` — the facade `eventId` returned by schedule.

**Responses**: `200` success · `409` state/ticket conflict · `404` unknown event ·
`502` / `503` upstream problem.
"""

_START_DESCRIPTION = _LIFECYCLE_DESCRIPTION.format(
    intro="**Start** a scheduled maintenance window — this begins the EWS message holds on live "
    "Zelle payment traffic.",
    precondition="SCHEDULED",
)
_COMPLETE_DESCRIPTION = _LIFECYCLE_DESCRIPTION.format(
    intro="**Complete** an in-progress maintenance window — this releases the EWS holds and lets "
    "payment traffic flow again.",
    precondition="IN_PROGRESS",
)
_CANCEL_DESCRIPTION = _LIFECYCLE_DESCRIPTION.format(
    intro="**Cancel** a scheduled maintenance window that has not started yet.",
    precondition="SCHEDULED",
)

_LIST_DESCRIPTION = """
List maintenance events **from the facade's local MongoDB state** — this read does **not** call
EWS. Optionally filter by `status` (e.g. `SCHEDULED`, `IN_PROGRESS`, `COMPLETE`).

**Headers**
- `X-Client-Id` **(required)** — caller identity.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Query**
- `status` *(optional)* — return only events in this status; omit for all.

**Response**: `200` with an envelope of consumer event views (the facade's last known intent).
"""

_GET_DESCRIPTION = """
Read one maintenance event **from the facade's local MongoDB state** by its facade `eventId` — this
read does **not** call EWS. The returned view is the facade's last known intent, not live upstream
authority.

**Headers**
- `X-Client-Id` **(required)** — caller identity.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Path**
- `event_id` — the facade `eventId` returned by schedule.

**Responses**: `200` with the consumer event view · `404` when no such event exists locally.
"""

_UPSTREAM_STATUS_DESCRIPTION = """
Read the **live upstream status** of one event. Unlike every other read, this **does** call EWS —
synchronously — and returns what the upstream reports right now, side by side with the facade's own
stored status. Nothing is persisted and no state changes: this is a pure look, not a reconcile.

Use it deliberately (each call is a real southbound request): pre-flighting a start, checking an
`UNCERTAIN` event for drift before an operator resolve, or a UI "refresh from source" action.
Routine polling should use the plain `GET /{event_id}` local read instead.

**Headers**
- `X-Client-Id` **(required)** — caller identity.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Path**
- `event_id` — the facade `eventId` returned by schedule.

**Response body**: `eventId`, `localStatus` (the facade's stored status), `upstreamStatus` (what the
upstream reported, upper-cased; may be `null` if it omitted one), `checkedAt`, `correlationId`.

**Responses**: `200` live view · `404` unknown event · `409` the event has no upstream id yet
(e.g. `PENDING_UPSTREAM_ID`) · `502` / `503` upstream problem.
"""

_QUEUE_DEPTHS_DESCRIPTION = """
Read the **live held-notification counts** for your organisation, by queue. This **does** call EWS
synchronously (vendor count API) and reports how many notifications EWS is currently holding —
useful to verify holds are accumulating during a window and drained after completion. Callable
whether or not a maintenance event is in progress; nothing is persisted.

**Headers**
- `X-Client-Id` **(required)** — caller identity.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Response body**: `queueDepths` (array of `{name, count}`), `checkedAt`, `correlationId`.

**Responses**: `200` live view · `502` / `503` upstream problem.
"""


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
    sm_user: str | None,
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
    :param sm_user: The SSO username (``Sm-User``); drives the notification recipient.
    :type sm_user: str | None
    :return: The 200 response with the consumer event view.
    :rtype: JSONResponse
    :raises ValidationFailedError: When the confirmation header is missing or blank.
    """

    if x_confirm_ticket is None or not x_confirm_ticket.strip():
        raise ValidationFailedError("X-Confirm-Ticket header is required.")
    # endIf
    logger.info(
        "lifecycle %s event_id=%s client_id=%s sm_user=%s dry_run=%s",
        action.value,
        event_id,
        client_id,
        sm_user,
        dry_run,
    )
    response = await service.lifecycle(
        event_id,
        action,
        client_id=client_id,
        confirm_ticket=x_confirm_ticket,
        correlation_id=correlation_id,
        dry_run=dry_run,
        sm_user=sm_user,
    )
    return JSONResponse(
        status_code=LIFECYCLE_SUCCESS_STATUS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.post(
    "",
    status_code=HTTPCodes.CREATED,
    summary="Schedule a maintenance window",
    response_description="The consumer event view (camelCase), including the facade eventId.",
    description=_SCHEDULE_DESCRIPTION,
)
async def schedule_event(
    payload: ScheduleEventRequest,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    idempotency_key: str | None = Header(None),
    sm_user: str | None = Header(None),
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
    :param sm_user: The SSO username set by the AMP gateway (``Sm-User``); drives contact
        enrichment and the notification recipient.
    :type sm_user: str | None
    :return: The consumer event view with the service-decided status code.
    :rtype: JSONResponse
    """

    logger.info(
        "schedule request client_id=%s sm_user=%s idempotency_key=%s correlation_id=%s",
        client_id,
        sm_user,
        idempotency_key,
        correlation_id,
    )
    result = await service.schedule(
        payload,
        client_id=client_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        sm_user=sm_user,
    )
    return JSONResponse(
        status_code=result.status_code,
        content=result.response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.post(
    "/{event_id}/start",
    summary="Start a scheduled event (begins EWS holds)",
    response_description="The consumer event view after the start transition.",
    description=_START_DESCRIPTION,
)
async def start_event(
    event_id: str,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    sm_user: str | None = Header(None),
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
    :param sm_user: The SSO username (``Sm-User``); drives the notification recipient.
    :type sm_user: str | None
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
        sm_user=sm_user,
    )
# endDef


@events_router.post(
    "/{event_id}/complete",
    summary="Complete an in-progress event (releases EWS holds)",
    response_description="The consumer event view after the complete transition.",
    description=_COMPLETE_DESCRIPTION,
)
async def complete_event(
    event_id: str,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    sm_user: str | None = Header(None),
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
    :param sm_user: The SSO username (``Sm-User``); drives the notification recipient.
    :type sm_user: str | None
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
        sm_user=sm_user,
    )
# endDef


@events_router.post(
    "/{event_id}/cancel",
    summary="Cancel a scheduled event (before it starts)",
    response_description="The consumer event view after the cancel transition.",
    description=_CANCEL_DESCRIPTION,
)
async def cancel_event(
    event_id: str,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    x_confirm_ticket: str | None = Header(None),
    dry_run: bool = False,
    sm_user: str | None = Header(None),
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
    :param sm_user: The SSO username (``Sm-User``); drives the notification recipient.
    :type sm_user: str | None
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
        sm_user=sm_user,
    )
# endDef


@events_router.get(
    "",
    summary="List events (local state, no EWS call)",
    response_description="An envelope of consumer event views.",
    description=_LIST_DESCRIPTION,
)
async def list_events(
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    status: EventStatus | None = None,
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

    logger.debug(
        "list events status=%s client_id=%s",
        status.value if status is not None else None,
        client_id,
    )
    envelope = await service.list_events(status, correlation_id=correlation_id)
    return JSONResponse(
        status_code=HTTPCodes.SUCCESS,
        content=envelope.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# Registered BEFORE the dynamic "/{event_id}" route — Starlette matches routes in registration
# order, so the literal path must come first or "queue-depths" would be read as an event id.
@events_router.get(
    "/queue-depths",
    summary="Get the org's live held-notification counts (calls EWS)",
    response_description="The live queue-depth view.",
    description=_QUEUE_DEPTHS_DESCRIPTION,
)
async def get_queue_depths(
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    ) -> JSONResponse:

    """
    Read the org's live held-notification counts by queue (200) — a real southbound read;
    no state change.

    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :return: The live queue-depth view.
    :rtype: JSONResponse
    """

    logger.debug("queue depths client_id=%s", client_id)
    response = await service.queue_depths(
        client_id=client_id,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=HTTPCodes.SUCCESS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.get(
    "/{event_id}",
    summary="Get one event (local state, no EWS call)",
    response_description="The consumer event view.",
    description=_GET_DESCRIPTION,
)
async def get_event(
    event_id: str,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
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

    logger.debug("get event event_id=%s client_id=%s", event_id, client_id)
    response = await service.get_event(event_id, correlation_id=correlation_id)
    return JSONResponse(
        status_code=HTTPCodes.SUCCESS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


@events_router.get(
    "/{event_id}/upstream-status",
    summary="Get one event's live upstream status (calls EWS)",
    response_description="The live upstream status view.",
    description=_UPSTREAM_STATUS_DESCRIPTION,
)
async def get_upstream_status(
    event_id: str,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    ) -> JSONResponse:

    """
    Read one event's live upstream status (200) — a real southbound read; no state change.

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed caller identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :return: The live upstream status view.
    :rtype: JSONResponse
    """

    logger.debug("upstream status event_id=%s client_id=%s", event_id, client_id)
    response = await service.upstream_status(
        event_id,
        client_id=client_id,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=HTTPCodes.SUCCESS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/routes/zelle/events.py
