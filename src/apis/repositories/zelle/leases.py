#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/leases.py.                                                  #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : LeaseRepository — Mongo lease documents for process singletons (the watchdog).      #
#                 acquire() wins by upserting against an expired-or-own-lease filter, so an           #
#                 accidental scale-out produces idle replicas, not duplicate pagers; a TTL index      #
#                 on expires_at garbage-collects abandoned leases.                                    #
# Dependencies  : motor, pymongo.                                                                     #
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
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

# Internal imports

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class LeaseRepository:

    """
    Mongo lease documents (``_id`` = lease name) backing process singletons. A lease is held by
    exactly one ``holder`` until ``expires_at``; acquisition races are settled by the unique
    ``_id`` — the losing upsert raises a duplicate-key error and is reported as not acquired.
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
        :param collection_prefix: Collection name prefix (collection ``{prefix}_leases``).
        :type collection_prefix: str
        """

        self._collection = database[f"{collection_prefix}_leases"]
    # endDef

    async def ensure_indexes(self) -> None:

        """
        Create the TTL index that garbage-collects abandoned leases at ``expires_at``.

        :return: None.
        :rtype: None
        """

        await self._collection.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
    # endDef

    async def acquire(
        self,
        name: str,
        holder: str,
        ttl_seconds: float,
        ) -> bool:

        """
        Try to take the lease: succeeds when the lease is absent, expired, or already held by
        this holder. A concurrent acquisition losing the upsert race is reported as False.

        :param name: The lease name (Mongo ``_id``).
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
            # Upsert against an expired-or-own filter: a live foreign lease fails the filter and
            # the insert then collides on _id, which is exactly the "not acquired" signal.
            await self._collection.find_one_and_update(
                {
                    "_id": name,
                    "$or": [{"holder": holder}, {"expires_at": {"$lte": now}}],
                },
                {"$set": {"holder": holder, "expires_at": expires_at}},
                upsert=True,
            )
        except DuplicateKeyError:
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
            {"_id": name, "holder": holder},
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

        await self._collection.delete_one({"_id": name, "holder": holder})
    # endDef
# endClass


# end_apis/repositories/zelle/leases.py
