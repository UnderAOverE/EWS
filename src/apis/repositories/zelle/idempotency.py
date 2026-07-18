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
# Explanation   : IdempotencyRepository — the schedule replay ledger. A unique compound index on      #
#                 (client_id, key) makes a concurrent duplicate lose deterministically at             #
#                 try_insert; stored response snapshots are replayed for duplicate submissions,       #
#                 and cleanly-failed rows can be reclaimed for a safe re-drive.                       #
# Dependencies  : motor, pymongo, apis.models.zelle.records.                                          #
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
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

# Internal imports

from src.apis.models.zelle.records import IdempotencyRecord

# Local variables

LOGGER = logging.getLogger(__name__)
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class IdempotencyRepository:

    """
    Mongo ledger closing the schedule idempotency race. The unique ``(client_id, key)`` index
    is the load-bearing guarantee: whichever request inserts first wins; the loser re-reads and
    replays (or 409s). Rows carry the canonical body hash and, on success, the stored northbound
    response snapshot for replay.
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
        :param collection_prefix: Collection name prefix (collection ``{prefix}_idempotency``).
        :type collection_prefix: str
        """

        self._collection = database[f"{collection_prefix}_idempotency"]
    # endDef

    async def ensure_indexes(self) -> None:

        """
        Create the unique compound index on ``(client_id, key)`` that closes the race.

        :return: None.
        :rtype: None
        """

        await self._collection.create_index(
            [("client_id", ASCENDING), ("key", ASCENDING)],
            unique=True,
        )
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
            await self._collection.insert_one(record.model_dump(mode="python"))
        except DuplicateKeyError:
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

        document = await self._collection.find_one({"client_id": client_id, "key": key})
        if document is None:
            return None
        # endIf
        return self._from_document(document)
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

        document = await self._collection.find_one_and_update(
            {"client_id": client_id, "key": key, "status": STATUS_FAILED},
            {"$set": {"status": STATUS_PENDING}},
        )
        return document is not None
    # endDef

    def _from_document(self, document: dict[str, Any]) -> IdempotencyRecord:

        """
        Convert a Mongo document back to a ledger record, coercing the naive ``created_at``
        (Motor's default) to tz-aware UTC.

        :param document: The Mongo document.
        :type document: dict[str, Any]
        :return: The ledger record.
        :rtype: IdempotencyRecord
        """

        data = dict(document)
        data.pop("_id", None)
        created_at = data.get("created_at")
        if isinstance(created_at, datetime) and created_at.tzinfo is None:
            data["created_at"] = created_at.replace(tzinfo=timezone.utc)
        # endIf
        return IdempotencyRecord.model_validate(data)
    # endDef
# endClass


# end_apis/repositories/zelle/idempotency.py
