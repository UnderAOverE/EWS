#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/leases.py.                                                  #
# Date of birth : 2026-07-18.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : LeaseRepository — Mongo lease documents for process singletons (the watchdog) on      #
#                 the base write/delete motor repositories. acquire() wins by upserting against an       #
#                 expired-or-own-lease filter; a unique index on ``name`` makes an accidental            #
#                 scale-out produce idle replicas, and a TTL index on expires_at GCs abandoned leases.  #
# Dependencies  : motor, pymongo, common.db.motor_repository, apis.models.zelle.records,               #
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
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError as PyMongoDuplicateKeyError

# Internal imports

from src.apis.models.zelle.records import LeaseRecord
from src.common.constants import DatabasesCollections
from src.common.db.motor_repository import (
    BaseDeleteMotorRepository,
    BaseWriteMotorRepository,
    MongoDocument,
)
from src.common.logger import logger

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class LeaseRepository(
    BaseWriteMotorRepository[LeaseRecord],
    BaseDeleteMotorRepository[LeaseRecord],
    ):

    """
    Mongo lease documents backing process singletons. A lease is held by exactly one ``holder``
    until ``expires_at``; acquisition races are settled by the unique ``name`` index — the losing
    upsert raises a duplicate-key error and is reported as not acquired.
    """

    _database_name = DatabasesCollections.APPLICATION_MAIN_DATABASE
    _collection_name = DatabasesCollections.ZELLE_LEASES_COLLECTION

    async def ensure_indexes(self) -> None:

        """
        Create the unique ``name`` index and the TTL index that GCs abandoned leases at expires_at.

        :return: None.
        :rtype: None
        """

        logger.debug("ensuring unique name + TTL indexes on %s", self._collection_name)
        await self._collection.create_index([("name", ASCENDING)], unique=True)
        await self._collection.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    # endDef

    async def create(self, entity: LeaseRecord) -> LeaseRecord:

        """
        Insert a lease document.

        :param entity: The lease record to persist.
        :type entity: LeaseRecord
        :return: The persisted lease record.
        :rtype: LeaseRecord
        """

        await self._execute_insert_one(self._write_map_to_document(entity))
        return entity
    # endDef

    async def create_many(self, entities: list[LeaseRecord]) -> list[LeaseRecord]:

        """
        Insert multiple lease documents.

        :param entities: The lease records to persist.
        :type entities: list[LeaseRecord]
        :return: The persisted lease records.
        :rtype: list[LeaseRecord]
        """

        await self._execute_insert_many([self._write_map_to_document(e) for e in entities])
        return entities
    # endDef

    async def update_one(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> LeaseRecord | None:

        """
        Apply an update and return the updated lease record.

        :param filter_query: The filter query to match the document.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, insert if no document matches.
        :type upsert: bool
        :return: The updated lease record, or None when nothing matched.
        :rtype: LeaseRecord | None
        """

        doc = await self._collection.find_one_and_update(
            filter_query,
            update_doc_payload,
            upsert=upsert,
            return_document=ReturnDocument.AFTER,
        )
        return self._write_map_to_model(doc) if doc is not None else None
    # endDef

    async def update_many(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> int:

        """
        Apply an update to many lease documents.

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

    async def delete_one(self, filter_query: MongoDocument) -> bool:

        """
        Delete a single lease document.

        :param filter_query: The filter query to match the document.
        :type filter_query: MongoDocument
        :return: True when a document was deleted.
        :rtype: bool
        """

        result = await self._execute_delete_one(filter_query)
        return result.deleted_count > 0
    # endDef

    async def delete_many(self, filter_query: MongoDocument) -> int:

        """
        Delete multiple lease documents.

        :param filter_query: The filter query to match documents.
        :type filter_query: MongoDocument
        :return: The number of documents deleted.
        :rtype: int
        """

        result = await self._execute_delete_many(filter_query)
        return result.deleted_count
    # endDef

    async def acquire(
        self,
        name: str,
        holder: str,
        ttl_seconds: float,
        ) -> bool:

        """
        Try to take the lease: succeeds when the lease is absent, expired, or already held by this
        holder. A concurrent acquisition losing the upsert race is reported as False.

        :param name: The lease name.
        :type name: str
        :param holder: This instance's holder identity (uuid4 per instance).
        :type holder: str
        :param ttl_seconds: Lease lifetime from now.
        :type ttl_seconds: float
        :return: True when the lease is held by ``holder`` on return; False otherwise.
        :rtype: bool
        """

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            # Upsert against an expired-or-own filter: a live foreign lease fails the filter and the
            # insert then collides on the unique name index, which is the "not acquired" signal.
            await self._collection.find_one_and_update(
                {
                    "name": name,
                    "$or": [{"holder": holder}, {"expires_at": {"$lte": now}}],
                },
                {"$set": {"name": name, "holder": holder, "expires_at": expires_at}},
                upsert=True,
            )
        except PyMongoDuplicateKeyError:
            return False
        # endTryExcept
        return True
    # endDef

    async def renew(
        self,
        name: str,
        holder: str,
        ttl_seconds: float,
        ) -> bool:

        """
        Extend the lease, but only when still held by ``holder``.

        :param name: The lease name.
        :type name: str
        :param holder: This instance's holder identity.
        :type holder: str
        :param ttl_seconds: New lease lifetime from now.
        :type ttl_seconds: float
        :return: True when renewed; False when the lease is gone or held by another instance.
        :rtype: bool
        """

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        result = await self._collection.update_one(
            {"name": name, "holder": holder},
            {"$set": {"expires_at": expires_at}},
        )
        return result.matched_count > 0
    # endDef

    async def release(
        self,
        name: str,
        holder: str,
        ) -> None:

        """
        Release the lease if still held by ``holder``; releasing a foreign lease is a no-op.

        :param name: The lease name.
        :type name: str
        :param holder: This instance's holder identity.
        :type holder: str
        :return: None.
        :rtype: None
        """

        await self._collection.delete_one({"name": name, "holder": holder})
    # endDef
# endClass


async def get_leases_repository(
    db_client: AsyncIOMotorClient[MongoDocument],
    ) -> LeaseRepository:

    """
    Dependency function to get an instance of LeaseRepository.

    :param db_client: The MongoDB client instance.
    :type db_client: AsyncIOMotorClient
    :return: An instance of LeaseRepository.
    :rtype: LeaseRepository
    """

    return LeaseRepository(db_client, LeaseRecord)
# endDef


# end_apis/repositories/zelle/leases.py
