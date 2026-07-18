#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : fake_ews/app.py.                                                                    #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : create_fake_ews_app — a self-contained FastAPI stub of the EWS /token endpoint      #
#                 and the four ZOMS operations: header enforcement, body validation via the real      #
#                 southbound models, in-memory lifecycle state, idempotency-id replay returning       #
#                 the SAME 201 body, and fault injection via the x-fake-fault header.                 #
# Dependencies  : fastapi, pydantic, apis.models.zelle.southbound.                                    #
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

import asyncio
import logging
import urllib.parse
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

# Internal imports

from apis.models.zelle.southbound import EwsScheduleRequest

# Local variables

LOGGER = logging.getLogger(__name__)
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TOKEN_TTL_SECONDS = 1800
FAULT_HEADER = "x-fake-fault"
SLOW_FAULT_SECONDS = 15.0
# ZOMS lifecycle vocabulary as the fake upstream sees it.
STATUS_SCHEDULED = "SCHEDULED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETE = "COMPLETE"
STATUS_CANCELLED = "CANCELLED"
# Headers every ZOMS operation requires (idempotency-id is schedule-only, checked separately).
REQUIRED_ZOMS_HEADERS = ("accept", "content-type", "request-id")


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class _FakeEwsState:

    """
    In-memory fake-upstream state: schedule bodies keyed by idempotency-id (replay returns the
    SAME 201 body), event lifecycle statuses, and the set of already-consumed injected faults.
    """

    def __init__(self) -> None:

        """
        Start with empty stores.
        """

        self.schedules_by_idempotency: dict[str, dict[str, Any]] = {}
        self.event_statuses: dict[str, str] = {}
        self.consumed_faults: set[str] = set()
    # endDef
# endClass


def create_fake_ews_app() -> FastAPI:

    """
    Build a self-contained fake EWS application: ``POST /token`` plus the four ZOMS operations
    under ``/zoms/v1/events``, with per-app in-memory state and fault injection via the
    ``x-fake-fault`` header (``500``, ``429``, ``401`` — injected once, then behaves — and
    ``slow``).

    :return: The fake EWS FastAPI application.
    :rtype: FastAPI
    """

    app = FastAPI(title="fake-ews")
    state = _FakeEwsState()

    async def _maybe_fault(request: Request) -> JSONResponse | None:

        """
        Apply an injected fault when the ``x-fake-fault`` header asks for one.

        The ``401`` fault fires once per app instance, then the endpoint behaves — the real
        client mints a fresh request-id per attempt, so a per-request-id rule could never test
        the refresh-and-retry-once success path (documented deviation from the contract note).

        :param request: The incoming request.
        :type request: Request
        :return: The fault response, or None to proceed normally.
        :rtype: JSONResponse | None
        """

        fault = request.headers.get(FAULT_HEADER)
        if fault is None:
            return None
        # endIf
        if fault == "500":
            return JSONResponse(status_code=500, content={"error": "injected server error"})
        # endIf
        if fault == "429":
            return JSONResponse(
                status_code=429,
                content={"error": "injected throttle"},
                headers={"Retry-After": "1"},
            )
        # endIf
        if fault == "401":
            if "401" not in state.consumed_faults:
                state.consumed_faults.add("401")
                return JSONResponse(status_code=401, content={"error": "injected unauthorized"})
            # endIf
            return None
        # endIf
        if fault == "slow":
            await asyncio.sleep(SLOW_FAULT_SECONDS)
            return None
        # endIf
        return None
    # endDef

    def _missing_zoms_headers(
        request: Request,
        *,
        require_idempotency: bool,
        ) -> JSONResponse | None:

        """
        Enforce the common ZOMS request headers.

        :param request: The incoming request.
        :type request: Request
        :param require_idempotency: Whether ``idempotency-id`` is also required (schedule only).
        :type require_idempotency: bool
        :return: A 401/400 response for missing headers, or None when all are present.
        :rtype: JSONResponse | None
        """

        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "missing bearer token"})
        # endIf
        missing = [name for name in REQUIRED_ZOMS_HEADERS if not request.headers.get(name)]
        if require_idempotency and not request.headers.get("idempotency-id"):
            missing.append("idempotency-id")
        # endIf
        if missing:
            return JSONResponse(
                status_code=400,
                content={"error": f"missing headers: {', '.join(missing)}"},
            )
        # endIf
        return None
    # endDef

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:

        """
        Fake token endpoint: validates the form fields are present WITHOUT verifying the
        assertion signature, then issues a throwaway bearer token.

        :param request: The incoming request.
        :type request: Request
        :return: A 200 token response, or 400 when the form is malformed.
        :rtype: JSONResponse
        """

        fault = await _maybe_fault(request)
        if fault is not None:
            return fault
        # endIf
        raw = (await request.body()).decode("utf-8")
        form = urllib.parse.parse_qs(raw)
        grant_type = form.get("grant_type", [""])[0]
        assertion_type = form.get("client_assertion_type", [""])[0]
        assertion = form.get("client_assertion", [""])[0]
        if grant_type != "client_credentials" or \
                assertion_type != CLIENT_ASSERTION_TYPE or not assertion:
            return JSONResponse(status_code=400, content={"error": "invalid_request"})
        # endIf
        return JSONResponse(
            status_code=200,
            content={
                "access_token": f"fake-token-{uuid.uuid4()}",
                "token_type": "Bearer",
                "expires_in": TOKEN_TTL_SECONDS,
            },
        )
    # endDef

    @app.post("/zoms/v1/events/schedule")
    async def schedule(request: Request) -> JSONResponse:

        """
        Fake schedule: enforce headers, validate the body against the real southbound model,
        and replay the SAME 201 body for a repeated idempotency-id.

        :param request: The incoming request.
        :type request: Request
        :return: 201 with ``maintenanceEventId``, or a 4xx on validation failure.
        :rtype: JSONResponse
        """

        fault = await _maybe_fault(request)
        if fault is not None:
            return fault
        # endIf
        header_error = _missing_zoms_headers(request, require_idempotency=True)
        if header_error is not None:
            return header_error
        # endIf
        try:
            body = await request.json()
            EwsScheduleRequest.model_validate(body)
        except (ValueError, ValidationError) as exc:
            return JSONResponse(status_code=400, content={"error": f"invalid body: {exc}"})
        # endTryExcept
        idempotency_id = request.headers["idempotency-id"]
        stored = state.schedules_by_idempotency.get(idempotency_id)
        if stored is not None:
            # Replay: the SAME 201 body, no new event.
            return JSONResponse(status_code=201, content=stored)
        # endIf
        event_id = str(uuid.uuid4())
        response_body: dict[str, Any] = {
            "maintenanceEventId": event_id,
            "status": STATUS_SCHEDULED,
        }
        state.schedules_by_idempotency[idempotency_id] = response_body
        state.event_statuses[event_id] = STATUS_SCHEDULED
        return JSONResponse(status_code=201, content=response_body)
    # endDef

    async def _lifecycle(
        request: Request,
        operation: str,
        required_status: str,
        target_status: str,
        ) -> JSONResponse:

        """
        Shared fake lifecycle body enforcing the real ZOMS state machine.

        :param request: The incoming request.
        :type request: Request
        :param operation: The verb name, for error messages.
        :type operation: str
        :param required_status: The status the event must currently hold.
        :type required_status: str
        :param target_status: The status a successful call produces.
        :type target_status: str
        :return: 200 with the new status, or 400/401/404/409 on failure.
        :rtype: JSONResponse
        """

        fault = await _maybe_fault(request)
        if fault is not None:
            return fault
        # endIf
        header_error = _missing_zoms_headers(request, require_idempotency=False)
        if header_error is not None:
            return header_error
        # endIf
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid JSON body"})
        # endTryExcept
        event_id = body.get("maintenanceEventId")
        if not isinstance(event_id, str) or len(event_id) != 36:
            return JSONResponse(
                status_code=400,
                content={"error": "maintenanceEventId must be a 36-character string"},
            )
        # endIf
        current = state.event_statuses.get(event_id)
        if current is None:
            return JSONResponse(status_code=404, content={"error": "unknown maintenanceEventId"})
        # endIf
        if current != required_status:
            return JSONResponse(
                status_code=409,
                content={"error": f"cannot {operation} an event in status {current}"},
            )
        # endIf
        state.event_statuses[event_id] = target_status
        return JSONResponse(status_code=200, content={"status": target_status})
    # endDef

    @app.post("/zoms/v1/events/start")
    async def start(request: Request) -> JSONResponse:

        """
        Fake start: SCHEDULED -> IN_PROGRESS only.

        :param request: The incoming request.
        :type request: Request
        :return: The lifecycle response.
        :rtype: JSONResponse
        """

        return await _lifecycle(request, "start", STATUS_SCHEDULED, STATUS_IN_PROGRESS)
    # endDef

    @app.post("/zoms/v1/events/complete")
    async def complete(request: Request) -> JSONResponse:

        """
        Fake complete: IN_PROGRESS -> COMPLETE only.

        :param request: The incoming request.
        :type request: Request
        :return: The lifecycle response.
        :rtype: JSONResponse
        """

        return await _lifecycle(request, "complete", STATUS_IN_PROGRESS, STATUS_COMPLETE)
    # endDef

    @app.post("/zoms/v1/events/cancel")
    async def cancel(request: Request) -> JSONResponse:

        """
        Fake cancel: SCHEDULED -> CANCELLED only.

        :param request: The incoming request.
        :type request: Request
        :return: The lifecycle response.
        :rtype: JSONResponse
        """

        return await _lifecycle(request, "cancel", STATUS_SCHEDULED, STATUS_CANCELLED)
    # endDef

    return app
# endDef


# Module-level instance for `uvicorn fake_ews.app:app`.
app = create_fake_ews_app()


# end_fake_ews/app.py
