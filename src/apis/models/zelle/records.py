#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/records.py.                                                       #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Internal persistence shapes stored in Mongo by the zelle repositories:              #
#                 EventRecord (facade event + state machine), IdempotencyRecord (schedule             #
#                 replay ledger), and AuditRecord (append-only intent/outcome trail).                 #
# Dependencies  : pydantic, apis.models.zelle.enums.                                                  #
# Modifications : 2026-07-16 Shane Reddy — Initial version.                                           #
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
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# Internal imports

from apis.models.zelle.enums import AuditKind, AuditOutcome, EventStatus, HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EventRecord(BaseModel):

    """
    Persistence shape of one maintenance event. ``event_id`` (a uuid4 string minted by the
    facade) maps to the Mongo ``_id``; the repository converts model <-> document explicitly.
    All datetimes are tz-aware UTC.
    """

    event_id: str
    ews_event_id: str | None
    status: EventStatus
    # uuid4 sent to EWS as the idempotency-id header on schedule, reused verbatim on retries.
    idempotency_id: str
    client_id: str
    ticket_number: str
    reason: str
    hold_mode: HoldMode
    scheduled_start: datetime
    scheduled_end: datetime
    # dict[str, Any] is contract-pinned: the exact EWS request body as sent (PII — audit only).
    payload_snapshot: dict[str, Any]
    last_confirmed_upstream_at: datetime | None
    created_at: datetime
    updated_at: datetime
# endClass


class IdempotencyRecord(BaseModel):

    """
    Ledger document that closes the schedule idempotency race (unique compound index on
    ``(client_id, key)``); the stored response snapshot is replayed for duplicate submissions.
    """

    client_id: str
    key: str
    # sha256 hex of the canonical northbound JSON; a mismatch on replay is a 409, never silent.
    body_hash: str
    event_id: str
    status: Literal["pending", "succeeded", "failed"]
    # dict[str, Any] is contract-pinned: the stored northbound response body for replay.
    response_snapshot: dict[str, Any] | None
    response_status_code: int | None
    created_at: datetime
# endClass


class AuditRecord(BaseModel):

    """
    Append-only audit document. An INTENT document is inserted before every southbound call and
    an OUTCOME document after; the two share ``attempt_id`` and are never updated in place.
    """

    attempt_id: str
    kind: AuditKind
    ts: datetime
    actor_client_id: str
    correlation_id: str
    event_id: str
    # One of: "schedule" | "start" | "complete" | "cancel" | "resolve".
    action: str
    ews_request_ids: list[str]
    # Set on OUTCOME documents only; None on INTENT.
    outcome: AuditOutcome | None
    http_status: int | None
    detail_redacted: str | None
# endClass


# end_apis/models/zelle/records.py
