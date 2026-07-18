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
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# Internal imports

from apis.models.zelle.enums import HoldMode

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
    LENIENT parse of the schedule 201 body — its exact shape is unconfirmed (open question #2),
    so unknown fields are retained via ``extra="allow"`` and ``maintenanceEventId`` may be
    absent.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="allow")

    maintenance_event_id: str | None = None
# endClass


# end_apis/models/zelle/southbound.py
