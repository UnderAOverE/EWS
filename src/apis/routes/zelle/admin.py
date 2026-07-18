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
# Dependencies  : fastapi, apis.dependencies.zelle, apis.models.zelle.northbound,                     #
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

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# Internal imports

from src.apis.dependencies.zelle import get_correlation_id, get_service, require_client_id
from src.apis.models.zelle.northbound import ResolveRequest
from src.apis.services.zelle.event_service import EventService

# Local variables

LOGGER = logging.getLogger(__name__)
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
    correlation_id: str = Depends(get_correlation_id),
    client_id: str = Depends(require_client_id),
    service: EventService = Depends(get_service),
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

    response = await service.resolve(
        event_id,
        payload,
        client_id=client_id,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=200,
        content=response.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/routes/zelle/admin.py
