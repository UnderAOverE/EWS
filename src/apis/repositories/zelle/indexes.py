#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/repositories/zelle/indexes.py.                                                 #
# Date of birth : 2026-07-21.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : One-time index provisioning for the zelle collections. register_zelle no longer     #
#                 creates indexes on startup (pods must not run DDL every restart / may lack the       #
#                 privilege), so this module owns index creation: create_zelle_indexes() is the        #
#                 reusable routine, and the __main__ entrypoint runs it once against a Mongo URI       #
#                 read from the environment. The unique idempotency index and TTL lease index are      #
#                 load-bearing for correctness — they MUST exist before serving traffic.              #
# Dependencies  : motor, apis.repositories.zelle.{audit,events,idempotency,leases}.                    #
# Modifications : 2026-07-21 Shane Reddy — Initial version.                                           #
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

import asyncio
import logging
import os
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# Internal imports

from src.apis.repositories.zelle.audit import AuditRepository
from src.apis.repositories.zelle.events import EventsRepository
from src.apis.repositories.zelle.idempotency import IdempotencyRepository
from src.apis.repositories.zelle.leases import LeaseRepository

# Local variables

LOGGER = logging.getLogger(__name__)
DEFAULT_COLLECTION_PREFIX = "zelle"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


async def create_zelle_indexes(
    database: AsyncIOMotorDatabase[dict[str, Any]],
    collection_prefix: str,
    ) -> None:

    """
    Create every index the zelle repositories rely on, idempotently (``create_index`` is a no-op
    when the index already exists). Call once at provisioning time — or from a test harness — with
    an injected database; the running facade no longer does this on startup.

    :param database: The target Motor database.
    :type database: AsyncIOMotorDatabase[dict[str, Any]]
    :param collection_prefix: Collection name prefix (e.g. ``zelle``).
    :type collection_prefix: str
    :return: None.
    :rtype: None
    """

    await EventsRepository(database, collection_prefix).ensure_indexes()
    await IdempotencyRepository(database, collection_prefix).ensure_indexes()
    await AuditRepository(database, collection_prefix).ensure_indexes()
    await LeaseRepository(database, collection_prefix).ensure_indexes()
# endDef


async def _main() -> None:

    """
    Standalone entrypoint: connect to Mongo using environment configuration and create the zelle
    indexes once. Reads ``ZELLE_MONGO_URI`` (required), ``ZELLE_MONGO_DB`` (required), and
    ``ZELLE_MONGO_COLLECTION_PREFIX`` (default ``zelle``). This is the sanctioned config-boundary
    read of the environment — the repositories themselves take an injected database.

    :return: None.
    :rtype: None
    :raises RuntimeError: If a required environment variable is unset.
    """

    logging.basicConfig(level=logging.INFO)
    mongo_uri = os.environ.get("ZELLE_MONGO_URI")
    mongo_db = os.environ.get("ZELLE_MONGO_DB")
    prefix = os.environ.get("ZELLE_MONGO_COLLECTION_PREFIX", DEFAULT_COLLECTION_PREFIX)
    if not mongo_uri or not mongo_db:
        raise RuntimeError("ZELLE_MONGO_URI and ZELLE_MONGO_DB must be set to create indexes.")
    # endIf
    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(mongo_uri)
    try:
        await create_zelle_indexes(client[mongo_db], prefix)
        LOGGER.info("created zelle indexes on db=%s prefix=%s", mongo_db, prefix)
    finally:
        client.close()
    # endTryFinally
# endDef


if __name__ == "__main__":
    asyncio.run(_main())
# endIf


# end_apis/repositories/zelle/indexes.py
