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

from fastapi import APIRouter
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


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@admin_router.post("/{event_id}/resolve")
async def resolve_event(
    event_id: str,
    payload: ResolveRequest,
    correlation_id: ZelleCorrelationIdDependency,
    client_id: ZelleClientIdDependency,
    service: ZelleEventServiceDependency,
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
    )
    return JSONResponse(
        status_code=HTTPCodes.HTTP_SUCCESS,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/routes/zelle/admin.py
