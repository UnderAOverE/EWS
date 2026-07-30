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
# Explanation   : EventsRepository — Mongo persistence for maintenance events on the base read/write   #
#                 motor repositories. Implements the abstract CRUD and keeps the specialized methods:   #
#                 the atomic state-machine transition (find_one_and_update filtered on expected         #
#                 statuses), overlap and stuck-event queries, and the startup PENDING sweep. The        #
#                 facade event_id is a unique-indexed field (the _id is an auto ObjectId).             #
# Dependencies  : motor, pymongo, common.db.motor_repository, apis.models.zelle.{enums,records},        #
#                 common.constants, common.logger.                                                    #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                            #
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

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.collation import Collation

# Internal imports

from src.apis.models.zelle.enums import EventStatus
from src.apis.models.zelle.records import EventRecord
from src.common.constants import DatabasesCollections
from src.common.db.motor_repository import (
    BaseReadMotorRepository,
    BaseWriteMotorRepository,
    MongoDocument,
)
from src.common.logger import logger

# Local variables

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


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EventsRepository(BaseReadMotorRepository[EventRecord], BaseWriteMotorRepository[EventRecord]):

    """
    Mongo persistence for maintenance events. The facade ``event_id`` (uuid4 string) is a unique-
    indexed field; the ``_id`` is an auto ObjectId per the base-repository convention. Reads and the
    atomic state-machine transition filter on ``event_id``.
    """

    _database_name = DatabasesCollections.APPLICATION_MAIN_DATABASE
    _collection_name = DatabasesCollections.ZELLE_EVENTS_COLLECTION

    async def ensure_indexes(self) -> None:

        """
        Create the indexes this repository queries on: unique event_id, status, and the window pair
        used by overlap detection.

        :return: None.
        :rtype: None
        """

        logger.debug("ensuring indexes on %s", self._collection_name)
        await self._collection.create_index([("event_id", ASCENDING)], unique=True)
        await self._collection.create_index([("status", ASCENDING)])
        await self._collection.create_index(
            [("scheduled_start", ASCENDING), ("scheduled_end", ASCENDING)],
        )
    # endDef

    async def find_one(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort_options: list[tuple[str, int]] | None = None,
        collation: Collation | None = None,
        ) -> EventRecord | None:

        """
        Find a single event document and map it to an EventRecord.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort_options: Optional sorting options.
        :type sort_options: list[tuple[str, int]] | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: The event record, or None when absent.
        :rtype: EventRecord | None
        """

        doc = await self._execute_find_one(filter_query, projection, sort_options, collation)
        return self._read_map_to_model(doc) if doc is not None else None
    # endDef

    async def create(self, entity: EventRecord) -> EventRecord:

        """
        Insert a new event document.

        :param entity: The event record to persist.
        :type entity: EventRecord
        :return: The persisted event record.
        :rtype: EventRecord
        """

        await self._execute_insert_one(self._write_map_to_document(entity))
        logger.info("created maintenance event id=%s", entity.event_id)
        return entity
    # endDef

    async def create_many(self, entities: list[EventRecord]) -> list[EventRecord]:

        """
        Insert multiple event documents.

        :param entities: The event records to persist.
        :type entities: list[EventRecord]
        :return: The persisted event records.
        :rtype: list[EventRecord]
        """

        await self._execute_insert_many([self._write_map_to_document(e) for e in entities])
        return entities
    # endDef

    async def update_one(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> EventRecord | None:

        """
        Apply an update and return the updated event record.

        :param filter_query: The filter query to match the document.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, insert if no document matches.
        :type upsert: bool
        :return: The updated event record, or None when nothing matched.
        :rtype: EventRecord | None
        """

        doc = await self._collection.find_one_and_update(
            filter_query,
            update_doc_payload,
            upsert=upsert,
            return_document=ReturnDocument.AFTER,
        )
        return self._read_map_to_model(doc) if doc is not None else None
    # endDef

    async def update_many(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> int:

        """
        Apply an update to many event documents.

        :param filter_query: The filter query to match documents.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, insert if no document matches.
        :type upsert: bool
        :return: The number of documents modified.
        :rtype: int
        """

        result = await self._collection.update_many(filter_query, update_doc_payload, upsert=upsert)
        return result.modified_count
    # endDef

    async def get(self, event_id: str) -> EventRecord | None:

        """
        Load one event by facade id.

        :param event_id: The facade event id.
        :type event_id: str
        :return: The event record, or None when absent.
        :rtype: EventRecord | None
        """

        return await self.find_one({"event_id": event_id})
    # endDef

    async def list_events(
        self,
        status: EventStatus | None = None,
        limit: int | None = DEFAULT_LIST_LIMIT,
        ) -> list[EventRecord]:

        """
        List events, optionally filtered by status, newest first.

        :param status: Optional status filter; None returns all statuses.
        :type status: EventStatus | None
        :param limit: Maximum number of records to return.
        :type limit: int | None
        :return: Matching event records ordered by ``created_at`` descending.
        :rtype: list[EventRecord]
        """

        query: MongoDocument = {}
        if status is not None:
            query["status"] = status.value
        # endIf
        return await self.find_many_paginated_skip_limit(
            query,
            sort_options=[("created_at", DESCENDING)],
            limit=limit,
        )
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
        Atomically move an event along a state-machine edge: the update filter requires the current
        status to be in ``expected``, so a concurrent transition loses deterministically.

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
        update: MongoDocument = {"status": new_status.value, "updated_at": now}
        if ews_event_id is not None:
            update["ews_event_id"] = ews_event_id
        # endIf
        if confirmed_upstream:
            update["last_confirmed_upstream_at"] = now
        # endIf
        doc = await self._collection.find_one_and_update(
            {"event_id": event_id, "status": {"$in": [status.value for status in expected]}},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            logger.warning("transition lost for event_id=%s -> %s", event_id, new_status.value)
            return None
        # endIf
        return self._read_map_to_model(doc)
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

        query: MongoDocument = {
            "status": {"$in": [status.value for status in ACTIVE_STATUSES]},
            "scheduled_start": {"$lt": end},
            "scheduled_end": {"$gt": start},
        }
        return await self.find_many_paginated_skip_limit(query, limit=None)
    # endDef

    async def sweep_pending(self) -> list[EventRecord]:

        """
        Move every PENDING event to UNCERTAIN (startup safety: a schedule idempotency-id is never
        blind-replayed after a crash). Each document is claimed atomically.

        :return: The swept records (post-transition state).
        :rtype: list[EventRecord]
        """

        swept: list[EventRecord] = []
        while True:
            doc = await self._collection.find_one_and_update(
                {"status": EventStatus.PENDING.value},
                {
                    "$set": {
                        "status": EventStatus.UNCERTAIN.value,
                        "updated_at": datetime.now(timezone.utc),
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                break
            # endIf
            swept.append(self._read_map_to_model(doc))
        # endWhile
        if swept:
            logger.warning("startup sweep moved %d PENDING event(s) to UNCERTAIN", len(swept))
        # endIf
        return swept
    # endDef

    async def find_stuck(
        self,
        now: datetime,
        grace_seconds: float,
        ) -> list[EventRecord]:

        """
        Find events the watchdog should page on: IN_PROGRESS past ``scheduled_end + grace`` or
        SCHEDULED past ``scheduled_start + grace``.

        :param now: The current time (tz-aware UTC).
        :type now: datetime
        :param grace_seconds: Grace period beyond the scheduled boundary.
        :type grace_seconds: float
        :return: Stuck event records.
        :rtype: list[EventRecord]
        """

        threshold = now - timedelta(seconds=grace_seconds)
        query: MongoDocument = {
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
        return await self.find_many_paginated_skip_limit(query, limit=None)
    # endDef
# endClass


async def get_events_repository(
    db_client: AsyncIOMotorClient[MongoDocument],
    ) -> EventsRepository:

    """
    Dependency function to get an instance of EventsRepository.

    :param db_client: The MongoDB client instance.
    :type db_client: AsyncIOMotorClient
    :return: An instance of EventsRepository.
    :rtype: EventsRepository
    """

    return EventsRepository(db_client, EventRecord)
# endDef


# end_apis/repositories/zelle/events.py
