#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/northbound.py.                                                    #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Northbound (consumer-facing) wire models with camelCase aliases: the schedule       #
#                 request with window validators, event response/list views, and the operator         #
#                 resolve request. EWS vocabulary never appears in this plane.                        #
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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

# Internal imports

from src.apis.models.zelle.enums import EventStatus, HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)
# Clock-skew grace for the "start not in the past" rule (contract: 5 minutes).
START_TIME_PAST_GRACE = timedelta(minutes=5)
# Statuses an operator may resolve an event into (contract: ResolveRequest.actual_status).
RESOLVABLE_STATUSES: frozenset[EventStatus] = frozenset(
    {
        EventStatus.SCHEDULED,
        EventStatus.IN_PROGRESS,
        EventStatus.COMPLETE,
        EventStatus.CANCELLED,
        EventStatus.FAILED,
    },
)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class ScheduleEventRequest(BaseModel):

    """
    Consumer request to schedule a maintenance window. Consumers send only what a change ticket
    knows; config owns the org constants and contact block enriched southbound.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    start_time: datetime
    end_time: datetime
    ticket_number: Annotated[str, Field(min_length=1, max_length=36)]
    reason: Annotated[str, Field(min_length=1, max_length=255)]
    # None -> settings.default_hold_mode at the service layer.
    hold_mode: HoldMode | None = None
    allow_overlap: bool = False
    suppress_duplicate_payments: bool | None = None
    network_notification_id: Annotated[str, Field(min_length=1, max_length=36)] | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _require_tz_aware(cls, value: datetime) -> datetime:

        """
        Reject naive datetimes; all facade datetimes are timezone-aware.

        :param value: The datetime under validation.
        :type value: datetime
        :return: The unchanged value when timezone-aware.
        :rtype: datetime
        :raises ValueError: If the datetime is naive.
        """

        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("datetime must be timezone-aware (e.g. 2026-08-01T06:00:00Z)")
        # endIf
        return value
    # endDef

    @model_validator(mode="after")
    def _validate_window(self) -> Self:

        """
        Enforce a coherent window: end after start, and start not in the past beyond a
        five-minute clock-skew grace.

        :return: The validated model instance.
        :rtype: Self
        :raises ValueError: If the window is inverted/empty or starts in the past.
        """

        if self.end_time <= self.start_time:
            raise ValueError("endTime must be after startTime")
        # endIf
        if self.start_time < datetime.now(timezone.utc) - START_TIME_PAST_GRACE:
            raise ValueError("startTime must not be in the past")
        # endIf
        return self
    # endDef
# endClass


class MaintenanceEventResponse(BaseModel):

    """
    Consumer view of a maintenance event; ``last_confirmed_upstream_at`` documents last known
    intent, never upstream authority. Serialize with ``model_dump(mode="json", by_alias=True)``.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    event_id: str
    status: EventStatus
    start_time: datetime
    end_time: datetime
    ticket_number: str
    reason: str
    hold_mode: HoldMode
    correlation_id: str
    created_at: datetime
    last_confirmed_upstream_at: datetime | None
# endClass


class EventListResponse(BaseModel):

    """
    Envelope for ``GET /v1/maintenance-events`` — a list of consumer event views.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    events: list[MaintenanceEventResponse]
# endClass


class ResolveRequest(BaseModel):

    """
    Operator resolution of an UNCERTAIN or PENDING_UPSTREAM_ID event after manual reconciliation
    with EWS. ``ews_event_id`` is required when resolving PENDING_UPSTREAM_ID — enforced by the
    service, which knows the event's current status.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    actual_status: EventStatus
    # Operator attestation, e.g. "EWS NOC ref 4471".
    attestation: Annotated[str, Field(min_length=1, max_length=500)]
    ews_event_id: str | None = None

    @field_validator("actual_status")
    @classmethod
    def _require_resolvable_status(cls, value: EventStatus) -> EventStatus:

        """
        Restrict resolution targets to the statuses an operator may attest.

        :param value: The requested target status.
        :type value: EventStatus
        :return: The unchanged value when allowed.
        :rtype: EventStatus
        :raises ValueError: If the status is not a legal resolution target.
        """

        if value not in RESOLVABLE_STATUSES:
            allowed = ", ".join(sorted(status.value for status in RESOLVABLE_STATUSES))
            raise ValueError(f"actualStatus must be one of: {allowed}")
        # endIf
        return value
    # endDef
# endClass


# end_apis/models/zelle/northbound.py
