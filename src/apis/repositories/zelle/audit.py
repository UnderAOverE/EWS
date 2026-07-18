#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/audit.py.                                                   #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : AuditRepository — the append-only compliance trail. An INTENT document is           #
#                 inserted before every southbound call and an OUTCOME document after; the two        #
#                 share attempt_id. Deliberately, NO update or delete methods exist on this           #
#                 class — append-only is enforced by the absence of any other code path.              #
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
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING

# Internal imports

from apis.models.zelle.records import AuditRecord

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class AuditRepository:

    """
    Append-only Mongo audit trail. Intent and outcome are separate inserted documents sharing
    ``attempt_id`` — a facade crash mid-call still leaves the INTENT row as forensic evidence of
    an in-flight EWS mutation. This class exposes no update or delete methods by design.
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
        :param collection_prefix: Collection name prefix (collection ``{prefix}_audit``).
        :type collection_prefix: str
        """

        self._collection = database[f"{collection_prefix}_audit"]
    # endDef

    async def ensure_indexes(self) -> None:

        """
        Create the indexes audit queries use: by event and by timestamp.

        :return: None.
        :rtype: None
        """

        await self._collection.create_index([("event_id", ASCENDING)])
        await self._collection.create_index([("ts", ASCENDING)])
    # endDef

    async def record_intent(self, record: AuditRecord) -> str:

        """
        Insert an INTENT document (before every southbound call).

        :param record: The intent audit record.
        :type record: AuditRecord
        :return: The attempt id the paired OUTCOME document must share.
        :rtype: str
        """

        await self._collection.insert_one(record.model_dump(mode="python"))
        return record.attempt_id
    # endDef

    async def record_outcome(self, record: AuditRecord) -> None:

        """
        Insert an OUTCOME document (after the southbound call) — an insert, never an update.

        :param record: The outcome audit record (same ``attempt_id`` as its INTENT).
        :type record: AuditRecord
        :return: None.
        :rtype: None
        """

        await self._collection.insert_one(record.model_dump(mode="python"))
    # endDef
# endClass


# end_apis/repositories/zelle/audit.py
