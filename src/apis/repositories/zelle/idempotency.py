#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/idempotency.py.                                             #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : IdempotencyRepository — the schedule replay ledger on the base read/write motor      #
#                 repositories. A unique compound index on (client_id, key) makes a concurrent          #
#                 duplicate lose deterministically at try_insert (base raises DuplicateKeyError);        #
#                 stored response snapshots are replayed, and cleanly-failed rows are reclaimed.        #
# Dependencies  : motor, pymongo, common.db.motor_repository, common.db.exceptions,                    #
#                 apis.models.zelle.records, common.constants, common.logger.                          #
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

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, ReturnDocument
from pymongo.collation import Collation

# Internal imports

from src.apis.models.zelle.records import IdempotencyRecord
from src.common.constants import DatabasesCollections
from src.common.db.exceptions import DuplicateKeyError
from src.common.db.motor_repository import (
    BaseReadMotorRepository,
    BaseWriteMotorRepository,
    MongoDocument,
)
from src.common.logger import logger

# Local variables

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class IdempotencyRepository(
    BaseReadMotorRepository[IdempotencyRecord],
    BaseWriteMotorRepository[IdempotencyRecord],
    ):

    """
    Mongo ledger closing the schedule idempotency race. The unique ``(client_id, key)`` index is the
    load-bearing guarantee: whichever request inserts first wins; the loser re-reads and replays (or
    409s).
    """

    _database_name = DatabasesCollections.APPLICATION_MAIN_DATABASE
    _collection_name = DatabasesCollections.ZELLE_IDEMPOTENCY_COLLECTION

    async def ensure_indexes(self) -> None:

        """
        Create the unique compound index on ``(client_id, key)`` that closes the race.

        :return: None.
        :rtype: None
        """

        logger.debug("ensuring unique (client_id, key) index on %s", self._collection_name)
        await self._collection.create_index(
            [("client_id", ASCENDING), ("key", ASCENDING)],
            unique=True,
        )
    # endDef

    async def find_one(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort_options: list[tuple[str, int]] | None = None,
        collation: Collation | None = None,
        ) -> IdempotencyRecord | None:

        """
        Find a single ledger document and map it to an IdempotencyRecord.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort_options: Optional sorting options.
        :type sort_options: list[tuple[str, int]] | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: The ledger record, or None when absent.
        :rtype: IdempotencyRecord | None
        """

        doc = await self._execute_find_one(filter_query, projection, sort_options, collation)
        return self._read_map_to_model(doc) if doc is not None else None
    # endDef

    async def create(self, entity: IdempotencyRecord) -> IdempotencyRecord:

        """
        Insert a ledger row.

        :param entity: The ledger record to persist.
        :type entity: IdempotencyRecord
        :return: The persisted ledger record.
        :rtype: IdempotencyRecord
        """

        await self._execute_insert_one(self._write_map_to_document(entity))
        return entity
    # endDef

    async def create_many(self, entities: list[IdempotencyRecord]) -> list[IdempotencyRecord]:

        """
        Insert multiple ledger rows.

        :param entities: The ledger records to persist.
        :type entities: list[IdempotencyRecord]
        :return: The persisted ledger records.
        :rtype: list[IdempotencyRecord]
        """

        await self._execute_insert_many([self._write_map_to_document(e) for e in entities])
        return entities
    # endDef

    async def update_one(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> IdempotencyRecord | None:

        """
        Apply an update and return the updated ledger record.

        :param filter_query: The filter query to match the document.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, insert if no document matches.
        :type upsert: bool
        :return: The updated ledger record, or None when nothing matched.
        :rtype: IdempotencyRecord | None
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
        Apply an update to many ledger rows.

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

    async def try_insert(self, record: IdempotencyRecord) -> bool:

        """
        Insert a ledger row; a concurrent duplicate loses deterministically on the unique index.

        :param record: The ledger row to insert.
        :type record: IdempotencyRecord
        :return: True when inserted; False when a row for ``(client_id, key)`` already exists.
        :rtype: bool
        """

        try:
            await self._execute_insert_one(self._write_map_to_document(record))
        except DuplicateKeyError:
            logger.debug(
                "idempotency key already in flight: client_id=%s key=%s",
                record.client_id,
                record.key,
            )
            return False
        # endTryExcept
        return True
    # endDef

    async def get(
        self,
        client_id: str,
        key: str,
        ) -> IdempotencyRecord | None:

        """
        Load the ledger row for ``(client_id, key)``.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :param key: The consumer ``Idempotency-Key`` value.
        :type key: str
        :return: The ledger row, or None when absent.
        :rtype: IdempotencyRecord | None
        """

        return await self.find_one({"client_id": client_id, "key": key})
    # endDef

    async def mark_succeeded(
        self,
        client_id: str,
        key: str,
        response_snapshot: dict[str, Any],
        status_code: int,
        ) -> None:

        """
        Record the successful northbound response for later replay.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :param key: The consumer ``Idempotency-Key`` value.
        :type key: str
        :param response_snapshot: The northbound response body (camelCase JSON form).
        :type response_snapshot: dict[str, Any]
        :param status_code: The HTTP status the route returned (201 or 202).
        :type status_code: int
        :return: None.
        :rtype: None
        """

        await self._collection.update_one(
            {"client_id": client_id, "key": key},
            {
                "$set": {
                    "status": STATUS_SUCCEEDED,
                    "response_snapshot": response_snapshot,
                    "response_status_code": status_code,
                },
            },
        )
    # endDef

    async def mark_failed(
        self,
        client_id: str,
        key: str,
        ) -> None:

        """
        Mark the row failed after a clean pre-send failure, freeing it for reclaim on retry.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :param key: The consumer ``Idempotency-Key`` value.
        :type key: str
        :return: None.
        :rtype: None
        """

        logger.warning("marking idempotency row failed: client_id=%s key=%s", client_id, key)
        await self._collection.update_one(
            {"client_id": client_id, "key": key},
            {"$set": {"status": STATUS_FAILED}},
        )
    # endDef

    async def reclaim_failed(
        self,
        client_id: str,
        key: str,
        ) -> bool:

        """
        Atomically flip a ``failed`` row back to ``pending`` so a consumer retry after a clean
        pre-send failure re-drives safely; only one concurrent retry wins the flip.

        :param client_id: Attributed caller identity.
        :type client_id: str
        :param key: The consumer ``Idempotency-Key`` value.
        :type key: str
        :return: True when this caller reclaimed the row; False otherwise.
        :rtype: bool
        """

        doc = await self._collection.find_one_and_update(
            {"client_id": client_id, "key": key, "status": STATUS_FAILED},
            {"$set": {"status": STATUS_PENDING}},
        )
        return doc is not None
    # endDef
# endClass


async def get_idempotency_repository(
    db_client: AsyncIOMotorClient[MongoDocument],
    ) -> IdempotencyRepository:

    """
    Dependency function to get an instance of IdempotencyRepository.

    :param db_client: The MongoDB client instance.
    :type db_client: AsyncIOMotorClient
    :return: An instance of IdempotencyRepository.
    :rtype: IdempotencyRepository
    """

    return IdempotencyRepository(db_client, IdempotencyRecord)
# endDef


# end_apis/repositories/zelle/idempotency.py
