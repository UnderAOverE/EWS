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
# Explanation   : AuditRepository — the append-only compliance trail on the base write motor           #
#                 repository. record_intent inserts an INTENT before every southbound call and          #
#                 record_outcome an OUTCOME after; the two share attempt_id. update_one/update_many     #
#                 raise — append-only is enforced.                                                    #
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

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING

# Internal imports

from src.apis.models.zelle.records import AuditRecord
from src.common.constants import DatabasesCollections
from src.common.db.motor_repository import BaseWriteMotorRepository, MongoDocument
from src.common.logger import logger

# Local variables

APPEND_ONLY_MESSAGE = "The zelle audit trail is append-only; updates are not permitted."


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class AuditRepository(BaseWriteMotorRepository[AuditRecord]):

    """
    Append-only Mongo audit trail. Intent and outcome are separate inserted documents sharing
    ``attempt_id``. Updates are refused so a facade crash mid-call still leaves the INTENT row as
    forensic evidence of an in-flight EWS mutation.
    """

    _database_name: str = DatabasesCollections.APPLICATION_MAIN_DATABASE
    _collection_name: str = DatabasesCollections.ZELLE_AUDIT_COLLECTION

    async def ensure_indexes(self) -> None:

        """
        Create the indexes audit queries use: by event and by timestamp.

        :return: None.
        :rtype: None
        """

        logger.debug("ensuring indexes on %s", self._collection_name)
        await self._collection.create_index([("event_id", ASCENDING)])
        await self._collection.create_index([("ts", ASCENDING)])
    # endDef

    async def create(self, entity: AuditRecord) -> AuditRecord:

        """
        Insert an audit document.

        :param entity: The audit record to persist.
        :type entity: AuditRecord
        :return: The persisted audit record.
        :rtype: AuditRecord
        """

        await self._execute_insert_one(self._write_map_to_document(entity))
        return entity
    # endDef

    async def create_many(self, entities: list[AuditRecord]) -> list[AuditRecord]:

        """
        Insert multiple audit documents.

        :param entities: The audit records to persist.
        :type entities: list[AuditRecord]
        :return: The persisted audit records.
        :rtype: list[AuditRecord]
        """

        await self._execute_insert_many([self._write_map_to_document(e) for e in entities])
        return entities
    # endDef

    async def update_one(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> AuditRecord | None:

        """
        Refused: the audit trail is append-only.

        :param filter_query: Unused.
        :type filter_query: MongoDocument
        :param update_doc_payload: Unused.
        :type update_doc_payload: MongoDocument
        :param upsert: Unused.
        :type upsert: bool
        :return: Never returns.
        :rtype: AuditRecord | None
        :raises RuntimeError: Always — the audit trail is append-only.
        """

        raise RuntimeError(APPEND_ONLY_MESSAGE)
    # endDef

    async def update_many(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> int:

        """
        Refused: the audit trail is append-only.

        :param filter_query: Unused.
        :type filter_query: MongoDocument
        :param update_doc_payload: Unused.
        :type update_doc_payload: MongoDocument
        :param upsert: Unused.
        :type upsert: bool
        :return: Never returns.
        :rtype: int
        :raises RuntimeError: Always — the audit trail is append-only.
        """

        raise RuntimeError(APPEND_ONLY_MESSAGE)
    # endDef

    async def record_intent(self, record: AuditRecord) -> str:

        """
        Insert an INTENT document (before every southbound call).

        :param record: The intent audit record.
        :type record: AuditRecord
        :return: The attempt id the paired OUTCOME document must share.
        :rtype: str
        """

        await self._execute_insert_one(self._write_map_to_document(record))
        logger.debug("audit INTENT attempt_id=%s action=%s", record.attempt_id, record.action)
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

        await self._execute_insert_one(self._write_map_to_document(record))
        logger.debug(
            "audit OUTCOME attempt_id=%s outcome=%s",
            record.attempt_id,
            record.outcome.value if record.outcome is not None else None,
        )
    # endDef
# endClass


async def get_audit_repository(
    db_client: AsyncIOMotorClient[MongoDocument],
    ) -> AuditRepository:

    """
    Dependency function to get an instance of AuditRepository.

    :param db_client: The MongoDB client instance.
    :type db_client: AsyncIOMotorClient
    :return: An instance of AuditRepository.
    :rtype: AuditRepository
    """

    return AuditRepository(db_client, AuditRecord)
# endDef


# end_apis/repositories/zelle/audit.py
