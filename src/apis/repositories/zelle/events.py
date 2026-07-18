#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/events.py.                                                  #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : EventsRepository — Mongo persistence for maintenance events: create/read/list,      #
#                 the atomic state-machine transition edge (find_one_and_update filtered on the       #
#                 expected statuses), overlap and stuck-event queries, and the startup PENDING        #
#                 sweep. EventRecord.event_id maps to the Mongo _id.                                  #
# Dependencies  : motor, pymongo, apis.models.zelle.enums, apis.models.zelle.records.                 #
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
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument

# Internal imports

from apis.models.zelle.enums import EventStatus
from apis.models.zelle.records import EventRecord

# Local variables

LOGGER = logging.getLogger(__name__)
DEFAULT_LIST_LIMIT = 100
# Statuses that occupy a maintenance window for overlap detection (architecture §4).
ACTIVE_STATUSES: frozenset[EventStatus] = frozenset(
    {
        EventStatus.PENDING,
        EventStatus.PENDING_UPSTREAM_ID,
        EventStatus.SCHEDULED,
        EventStatus.IN_PROGRESS,
    },
)
# Datetime fields coerced back to tz-aware UTC on read (Motor returns naive UTC by default).
DATETIME_FIELDS: tuple[str, ...] = (
    "scheduled_start",
    "scheduled_end",
    "last_confirmed_upstream_at",
    "created_at",
    "updated_at",
)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EventsRepository:

    """
    Mongo persistence for maintenance events. The facade ``event_id`` (uuid4 string) is stored
    as the Mongo ``_id``; the repository converts model <-> document explicitly and coerces
    naive datetimes from Mongo back to tz-aware UTC on read.
    """

    def __init__(
        self,
        database: AsyncIOMotorDatabase[dict[str, Any]],
        collection_prefix: str,
        ) -> None:

        """
        Bind the repository to its collection.

        :param database: The host application's Motor database, injected.
        :type database: AsyncIOMotorDatabase[dict[str, Any]]
        :param collection_prefix: Collection name prefix (collection ``{prefix}_events``).
        :type collection_prefix: str
        """

        self._collection = database[f"{collection_prefix}_events"]
    # endDef

    async def ensure_indexes(self) -> None:

        """
        Create the indexes this repository queries on: status, and the window pair used by
        overlap detection.

        :return: None.
        :rtype: None
        """

        await self._collection.create_index([("status", ASCENDING)])
        await self._collection.create_index(
            [("scheduled_start", ASCENDING), ("scheduled_end", ASCENDING)],
        )
    # endDef

    async def create(self, record: EventRecord) -> None:

        """
        Insert a new event document.

        :param record: The event record to persist.
        :type record: EventRecord
        :return: None.
        :rtype: None
        """

        await self._collection.insert_one(self._to_document(record))
    # endDef

    async def get(self, event_id: str) -> EventRecord | None:

        """
        Load one event by facade id.

        :param event_id: The facade event id (Mongo ``_id``).
        :type event_id: str
        :return: The event record, or None when absent.
        :rtype: EventRecord | None
        """

        document = await self._collection.find_one({"_id": event_id})
        if document is None:
            return None
        # endIf
        return self._from_document(document)
    # endDef

    async def list_events(
        self,
        status: EventStatus | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        ) -> list[EventRecord]:

        """
        List events, optionally filtered by status, newest first.

        :param status: Optional status filter; None returns all statuses.
        :type status: EventStatus | None
        :param limit: Maximum number of records to return.
        :type limit: int
        :return: Matching event records ordered by ``created_at`` descending.
        :rtype: list[EventRecord]
        """

        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = status.value
        # endIf
        cursor = self._collection.find(query).sort("created_at", DESCENDING).limit(limit)
        documents = await cursor.to_list(length=limit)
        return [self._from_document(document) for document in documents]
    # endDef

    async def transition(
        self,
        event_id: str,
        expected: tuple[EventStatus, ...],
        new_status: EventStatus,
        *,
        ews_event_id: str | None = None,
        confirmed_upstream: bool = False,
        ) -> EventRecord | None:

        """
        Atomically move an event along a state-machine edge: the update filter requires the
        current status to be in ``expected``, so a concurrent transition loses deterministically.

        :param event_id: The facade event id.
        :type event_id: str
        :param expected: Statuses the event must currently be in for the edge to apply.
        :type expected: tuple[EventStatus, ...]
        :param new_status: The status to transition into.
        :type new_status: EventStatus
        :param ews_event_id: EWS maintenance event id to record; None leaves the field untouched.
        :type ews_event_id: str | None
        :param confirmed_upstream: When True, stamp ``last_confirmed_upstream_at`` with now.
        :type confirmed_upstream: bool
        :return: The updated record, or None when the precondition lost (caller raises 409).
        :rtype: EventRecord | None
        """

        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {"status": new_status.value, "updated_at": now}
        if ews_event_id is not None:
            update["ews_event_id"] = ews_event_id
        # endIf
        if confirmed_upstream:
            update["last_confirmed_upstream_at"] = now
        # endIf
        document = await self._collection.find_one_and_update(
            {"_id": event_id, "status": {"$in": [status.value for status in expected]}},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        # endIf
        return self._from_document(document)
    # endDef

    async def find_overlapping(
        self,
        start: datetime,
        end: datetime,
        ) -> list[EventRecord]:

        """
        Find active events whose window overlaps ``[start, end)`` — overlap means
        ``scheduled_start < end AND scheduled_end > start``.

        :param start: Window start (tz-aware UTC).
        :type start: datetime
        :param end: Window end (tz-aware UTC).
        :type end: datetime
        :return: Overlapping active event records.
        :rtype: list[EventRecord]
        """

        query: dict[str, Any] = {
            "status": {"$in": [status.value for status in ACTIVE_STATUSES]},
            "scheduled_start": {"$lt": end},
            "scheduled_end": {"$gt": start},
        }
        documents = await self._collection.find(query).to_list(length=None)
        return [self._from_document(document) for document in documents]
    # endDef

    async def sweep_pending(self) -> list[EventRecord]:

        """
        Move every PENDING event to UNCERTAIN (startup safety: a schedule idempotency-id is
        never blind-replayed after a crash). Each document is claimed atomically so concurrent
        sweepers never double-report an event.

        :return: The swept records (post-transition state).
        :rtype: list[EventRecord]
        """

        swept: list[EventRecord] = []
        while True:
            document = await self._collection.find_one_and_update(
                {"status": EventStatus.PENDING.value},
                {
                    "$set": {
                        "status": EventStatus.UNCERTAIN.value,
                        "updated_at": datetime.now(timezone.utc),
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if document is None:
                break
            # endIf
            swept.append(self._from_document(document))
        # endWhile
        return swept
    # endDef

    async def find_stuck(
        self,
        now: datetime,
        grace_seconds: float,
        ) -> list[EventRecord]:

        """
        Find events the watchdog should page on: IN_PROGRESS past ``scheduled_end + grace``
        (a window that never completed) or SCHEDULED past ``scheduled_start + grace`` (an
        orphan that never started).

        :param now: The current time (tz-aware UTC).
        :type now: datetime
        :param grace_seconds: Grace period beyond the scheduled boundary.
        :type grace_seconds: float
        :return: Stuck event records.
        :rtype: list[EventRecord]
        """

        threshold = now - timedelta(seconds=grace_seconds)
        query: dict[str, Any] = {
            "$or": [
                {
                    "status": EventStatus.IN_PROGRESS.value,
                    "scheduled_end": {"$lt": threshold},
                },
                {
                    "status": EventStatus.SCHEDULED.value,
                    "scheduled_start": {"$lt": threshold},
                },
            ],
        }
        documents = await self._collection.find(query).to_list(length=None)
        return [self._from_document(document) for document in documents]
    # endDef

    def _to_document(self, record: EventRecord) -> dict[str, Any]:

        """
        Convert an event record to its Mongo document form (``event_id`` becomes ``_id``).

        :param record: The event record.
        :type record: EventRecord
        :return: The Mongo document.
        :rtype: dict[str, Any]
        """

        document = record.model_dump(mode="python")
        document["_id"] = document.pop("event_id")
        # StrEnum members are str subclasses, but store the plain value for clean documents.
        document["status"] = record.status.value
        document["hold_mode"] = record.hold_mode.value
        return document
    # endDef

    def _from_document(self, document: dict[str, Any]) -> EventRecord:

        """
        Convert a Mongo document back to an event record, restoring ``event_id`` and coercing
        naive datetimes (Motor's default) to tz-aware UTC.

        :param document: The Mongo document.
        :type document: dict[str, Any]
        :return: The event record.
        :rtype: EventRecord
        """

        data = dict(document)
        data["event_id"] = data.pop("_id")
        for field in DATETIME_FIELDS:
            value = data.get(field)
            if isinstance(value, datetime) and value.tzinfo is None:
                data[field] = value.replace(tzinfo=timezone.utc)
            # endIf
        # endFor
        return EventRecord.model_validate(data)
    # endDef
# endClass


# end_apis/repositories/zelle/events.py
