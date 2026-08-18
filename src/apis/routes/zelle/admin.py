#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/routes/zelle/admin.py.                                                         #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Operator admin router: resolve an UNCERTAIN or PENDING_UPSTREAM_ID event after      #
#                 manual reconciliation with EWS. Fully audited with the operator attestation;        #
#                 this endpoint is the exit from the state-machine lock, so the state machine         #
#                 never becomes the outage.                                                           #
# Dependencies  : fastapi, apis.dependencies.types, apis.models.zelle.northbound,                     #
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
from src.apis.models.zelle.northbound import ResolveRequest
from src.common.constants import HTTPCodes
from src.common.logger import logger

# Local variables

admin_router = APIRouter(
    prefix="/v1/admin/maintenance-events",
    tags=["zelle-admin"],
)

# OpenAPI/ReDoc description (operator-facing markdown); see the note in routes/zelle/events.py.

_RESOLVE_DESCRIPTION = """
**Operator-only.** Manually resolve an event that the facade has locked in `UNCERTAIN` (an
ambiguous EWS outcome) or `PENDING_UPSTREAM_ID` (EWS did not return its event id synchronously),
**after** you have reconciled the true state with EWS out of band. This is the exit from the
state-machine lock, so the lock can never become the outage.

**Headers**
- `X-Client-Id` **(required)** — the operator's identity; recorded in the audit trail.
- `X-Correlation-Id` *(optional)* — trace id; minted if omitted.

**Path**
- `event_id` — the facade `eventId` to resolve.

**Body**
- `actualStatus` **(required)** — the true status you are attesting (a legal target for the event's
  current status).
- `attestation` **(required)** — free-text operator justification (e.g. "EWS NOC ref 4471"); stored
  in the audit trail.
- `ewsEventId` *(required only for `PENDING_UPSTREAM_ID`)* — the EWS maintenance event id you
  obtained from EWS, so later lifecycle calls have an id to use.

**Responses**: `200` with the resolved event view · `404` unknown event · `409` when the event is
not resolvable or the target status is not a legal edge.
"""


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@admin_router.post(
    "/{event_id}/resolve",
    summary="Operator: resolve an UNCERTAIN / PENDING_UPSTREAM_ID event",
    response_description="The consumer view of the resolved event.",
    description=_RESOLVE_DESCRIPTION,
)
async def resolve_event(
    event_id: str,
    payload: ResolveRequest,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
    sm_user: str | None = Header(None),
    ) -> JSONResponse:

    """
    Resolve an UNCERTAIN or PENDING_UPSTREAM_ID event to its operator-attested actual status
    (200).

    :param event_id: Facade event id from the route path.
    :type event_id: str
    :param payload: The resolve request (target status, attestation, optional EWS id).
    :type payload: ResolveRequest
    :param correlation_id: Correlation id bound to this request.
    :type correlation_id: str
    :param client_id: Attributed operator identity.
    :type client_id: str
    :param service: The event orchestration service.
    :type service: EventService
    :param sm_user: The SSO username (``Sm-User``); drives the notification recipient.
    :type sm_user: str | None
    :return: The consumer view of the resolved event.
    :rtype: JSONResponse
    """

    logger.info(
        "operator resolve event_id=%s actual_status=%s client_id=%s",
        event_id,
        payload.actual_status.value,
        client_id,
    )
    response = await service.resolve(
        event_id,
        payload,
        client_id=client_id,
        correlation_id=correlation_id,
        sm_user=sm_user,
    )
    return JSONResponse(
        status_code=HTTPCodes.SUCCESS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/routes/zelle/admin.py
