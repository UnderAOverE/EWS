#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/southbound.py.                                                    #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Southbound EWS wire models — exact vendor field names and lengths per               #
#                 docs/zoms-api-reference.md — plus format_ews_datetime, the only serializer          #
#                 through which datetimes reach the EWS wire.                                         #
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
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# Internal imports

from src.apis.models.zelle.enums import HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def format_ews_datetime(value: datetime) -> str:

    """
    Serialize a tz-aware datetime to the EWS wire format ``YYYY-MM-DDTHH:MM:SS.NNNZ``.

    Pydantic's default ``+00:00`` suffix is a likely silent CAT 400, so this function is the
    only path by which datetimes reach the EWS wire: always UTC, exactly three millisecond
    digits, literal ``Z`` suffix.

    :param value: The datetime to serialize; must be timezone-aware.
    :type value: datetime
    :return: The EWS wire representation, e.g. ``2025-10-20T23:00:00.123Z``.
    :rtype: str
    :raises ValueError: If ``value`` is naive (no usable tzinfo).
    """

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("format_ews_datetime requires a timezone-aware datetime")
    # endIf
    utc_value = value.astimezone(timezone.utc)
    return f"{utc_value:%Y-%m-%dT%H:%M:%S}.{utc_value.microsecond // 1000:03d}Z"
# endDef


class EwsScheduleRequest(BaseModel):

    """
    Body of ``POST /v1/events/schedule`` — field names and lengths mirror the vendor spec.

    The ``to_camel`` alias generator emits exactly the vendor field names: ``orgId``,
    ``participantName``, ``submittedName``, ``contactName``, ``contactPhone``,
    ``contactEmail``, ``scheduledStartDate``, ``scheduledEndDate``, ``ewsHold``,
    ``suppressDuplicatePayments``, ``ticketNumber``, ``networkNotificationId``. Serialize with
    ``model_dump(mode="json", by_alias=True)``. Scheduled dates are pre-formatted strings from
    :func:`format_ews_datetime`.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    org_id: Annotated[str, Field(min_length=3, max_length=3)]
    participant_name: Annotated[str, Field(min_length=1, max_length=50)]
    submitted_name: Annotated[str, Field(min_length=1, max_length=50)]
    contact_name: Annotated[str, Field(min_length=1, max_length=128)]
    contact_phone: Annotated[str, Field(min_length=9, max_length=12)]
    contact_email: Annotated[str, Field(min_length=1, max_length=255)]
    scheduled_start_date: str
    scheduled_end_date: str
    ews_hold: HoldMode
    suppress_duplicate_payments: bool | None = None
    ticket_number: Annotated[str, Field(min_length=1, max_length=36)] | None = None
    network_notification_id: Annotated[str, Field(min_length=1, max_length=36)] | None = None
    # EMERGENCY_IMMEDIATE indicator (vendor rules doc, transcribed 2026-08-18): True marks an
    # incident window starting within about fifteen minutes, exempt from the allowed-window
    # rule. Omitted from the wire when None (the client dumps with exclude_none).
    emergency_immediate_start: bool | None = None
# endClass


class EwsLifecycleRequest(BaseModel):

    """
    Body of ``POST /v1/events/{start|complete|cancel}`` — the EWS maintenance event id
    (``maintenanceEventId``, exactly 36 characters).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    maintenance_event_id: Annotated[str, Field(min_length=36, max_length=36)]
# endClass


class EwsScheduleResponse(BaseModel):

    """
    LENIENT parse of the schedule 201 body. The confirmed shape (vendor spec pp. 21, transcribed
    2026-08-12) wraps every field in a ``maintenanceEvent`` envelope::

        {"maintenanceEvent": {"maintenanceEventId": "...", "status": "NOT_STARTED", ...}}

    The before-validator unwraps that envelope; unknown fields are retained via
    ``extra="allow"`` and ``maintenanceEventId`` stays optional so an unexpected body degrades
    to the PENDING_UPSTREAM_ID path instead of crashing.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    maintenance_event_id: str | None = None
    status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unwrap_envelope(cls, data: Any) -> Any:

        """
        Unwrap the vendor's ``maintenanceEvent`` envelope when present — direct construction
        (tests, stubs) without the envelope passes through untouched.

        :param data: The raw input mapping before field validation.
        :type data: Any
        :return: The inner event mapping, or the input unchanged.
        :rtype: Any
        """

        if isinstance(data, dict) and isinstance(data.get("maintenanceEvent"), dict):
            return data["maintenanceEvent"]
        # endIf
        return data
    # endDef
# endClass


class EwsEventStatusResponse(BaseModel):

    """
    LENIENT parse of one entry of the org-scoped list read's ``maintenanceEvents`` array
    (``GET /v1/events?orgId={orgId}``, vendor spec pp. 50-52). Unknown fields are retained via
    ``extra="allow"`` and both typed fields may be absent. ``status`` stays a raw string here —
    the confirmed upstream vocabulary (NOT_STARTED, IN_PROGRESS, PRE_COMPLETE, COMPLETE,
    CANCELLED, NO_SHOW) is EWS's, not the facade's, and an unlisted value must surface verbatim.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    maintenance_event_id: str | None = None
    status: str | None = None
# endClass


class EwsQueueDepth(BaseModel):

    """
    LENIENT parse of one entry of the count read's ``queueDepths`` array
    (``GET /v1/count?orgId={orgId}``, vendor spec pp. 56-57): the queue ``name`` and the
    ``count`` of notifications currently held in it. Unknown fields are retained via
    ``extra="allow"`` and both fields may be absent.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    name: str | None = None
    count: int | None = None
# endClass


# end_apis/models/zelle/southbound.py
