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
# Explanation   : Internal persistence shapes stored in Mongo by the zelle repositories, following     #
#                 the base-repository conventions: a PyObjectId ``_id``, ``ConfigDict(extra=forbid,     #
#                 populate_by_name)``, nullable fields defaulted so the base exclude_none round-trip    #
#                 works. EventRecord (facade event + state machine), IdempotencyRecord (schedule       #
#                 replay ledger), AuditRecord (append-only intent/outcome trail), LeaseRecord          #
#                 (watchdog singleton lease).                                                          #
# Dependencies  : pydantic, common.db.utils, common.miscellaneous.utils, apis.models.zelle.enums.      #
# Modifications : 2026-07-16 Shane Reddy — Initial version.                                            #
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
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# Internal imports

from src.apis.models.zelle.enums import AuditKind, AuditOutcome, EventStatus, HoldMode
from src.common.db.utils import PyObjectId
from src.common.miscellaneous.utils import sanitize_payload_recursive

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EventRecord(BaseModel):

    """
    Persistence shape of one maintenance event. ``_id`` is an auto ObjectId (base-repo convention);
    the facade ``event_id`` (a uuid4 string minted by the facade) is a normal unique-indexed field
    consumers key on. All datetimes are tz-aware UTC.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id_: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    event_id: str
    ews_event_id: str | None = None
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
    last_confirmed_upstream_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("*", mode="before")
    @classmethod
    def clean_all_strings(cls, value: Any, field: ValidationInfo) -> Any:

        """
        Sanitize only free-text fields. Ids, the contact block, the EWS ``payload_snapshot``,
        datetimes, and enums are skipped so lookups, audit fidelity, and PII (e.g. an email's ``@``)
        are preserved.

        :param value: The field value.
        :type value: Any
        :param field: The field information.
        :type field: ValidationInfo
        :return: The (possibly sanitized) value.
        :rtype: Any
        """

        if field.field_name in ["reason"]:
            return sanitize_payload_recursive(value)
        # endIf
        return value
    # endDef

    @field_validator(
        "scheduled_start",
        "scheduled_end",
        "last_confirmed_upstream_at",
        "created_at",
        "updated_at",
        mode="before",
        )
    @classmethod
    def _coerce_utc(cls, value: Any) -> Any:

        """
        Coerce naive datetimes (Motor returns naive UTC by default) back to tz-aware UTC so all
        internal datetimes stay comparable with ``datetime.now(timezone.utc)``.

        :param value: The field value.
        :type value: Any
        :return: The value, made tz-aware when it was a naive datetime.
        :rtype: Any
        """

        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        # endIf
        return value
    # endDef
# endClass


class IdempotencyRecord(BaseModel):

    """
    Ledger document that closes the schedule idempotency race (unique compound index on
    ``(client_id, key)``); the stored response snapshot is replayed for duplicate submissions. No
    string sanitizer: ``client_id``/``key`` are exact-match lookup keys and the response snapshot
    must replay byte-for-byte.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id_: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    client_id: str
    key: str
    # sha256 hex of the canonical northbound JSON; a mismatch on replay is a 409, never silent.
    body_hash: str
    event_id: str
    status: Literal["pending", "succeeded", "failed"]
    # dict[str, Any] is contract-pinned: the stored northbound response body for replay.
    response_snapshot: dict[str, Any] | None = None
    response_status_code: int | None = None
    created_at: datetime
# endClass


class AuditRecord(BaseModel):

    """
    Append-only audit document. An INTENT document is inserted before every southbound call and an
    OUTCOME document after; the two share ``attempt_id`` and are never updated in place. No string
    sanitizer: the audit trail is a forensic record stored exactly as captured (already redacted).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id_: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
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
    outcome: AuditOutcome | None = None
    http_status: int | None = None
    detail_redacted: str | None = None
# endClass


class LeaseRecord(BaseModel):

    """
    Persistence shape of a process-singleton lease (the watchdog). ``name`` is the unique lease name
    (indexed unique); ``expires_at`` drives the TTL index for garbage collection.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id_: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    name: str
    holder: str
    expires_at: datetime

    @field_validator("expires_at", mode="before")
    @classmethod
    def _coerce_utc(cls, value: Any) -> Any:

        """
        Coerce a naive ``expires_at`` (Motor default) to tz-aware UTC for correct lease comparisons.

        :param value: The field value.
        :type value: Any
        :return: The value, made tz-aware when it was a naive datetime.
        :rtype: Any
        """

        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        # endIf
        return value
    # endDef
# endClass


# end_apis/models/zelle/records.py
