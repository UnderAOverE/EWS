#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/db/motor_repository.py.                                                     #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Base Motor (async MongoDB) repositories: BaseReadMotorRepository,                    #
#                 BaseWriteMotorRepository, BaseDeleteMotorRepository. Each provides concrete            #
#                 _execute_* helpers over self._collection (error-wrapped, raising DuplicateKeyError     #
#                 on unique conflicts) and model<->document mapping, and declares the public CRUD as     #
#                 abstract for concrete repositories to implement. Local mirror of the host's           #
#                 common.db.motor_repository (replaced at merge).                                      #
# Dependencies  : motor, pymongo, bson, common.db.exceptions, common.logger.                          #
# Modifications : 2026-07-30 Shane Reddy — Initial version.                                            #
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

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

import bson
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from pymongo import ASCENDING, DESCENDING
from pymongo import errors as pymongo_errors
from pymongo.collation import Collation
from pymongo.results import DeleteResult, InsertManyResult, InsertOneResult, UpdateResult

# Internal imports

from src.common.db.exceptions import DuplicateKeyError

# Local variables

module_version: str = "1.0.0v"
MongoDocument = dict[str, Any]  # A type alias for MongoDB documents.


# ----------------------------------------------------------------------------------------------------#
# BaseMotorRepositories.                                                                              #
# ----------------------------------------------------------------------------------------------------#


class BaseReadMotorRepository[T](ABC):

    """
    Base read repository for MongoDB collections using Motor. Subclasses must define the
    ``_collection_name`` and ``_database_name`` attributes.
    """

    _database_name: str
    _collection_name: str

    def __init__(
        self,
        db_client: AsyncIOMotorClient[MongoDocument],
        base_model: type[T],
        ) -> None:

        """
        Base read repository constructor.

        :param db_client: MongoDB client.
        :type db_client: AsyncIOMotorClient
        :param base_model: The Pydantic model class representing the documents in the collection.
        :type base_model: type[T]
        :return: None.
        :rtype: None
        :raises NotImplementedError: If the subclass does not define _collection_name or
            _database_name.
        """

        if not hasattr(self, "_collection_name") or not self._collection_name:
            raise NotImplementedError("Subclasses must define _collection_name")
        # endIf
        if not hasattr(self, "_database_name") or not self._database_name:
            raise NotImplementedError("Subclasses must define _database_name")
        # endIf
        self.db_client: AsyncIOMotorClient[MongoDocument] = db_client
        self._collection: AsyncIOMotorCollection[MongoDocument] = (
            db_client[self._database_name][self._collection_name]
        )
        self._base_model = base_model
    # endDef

    def _read_map_to_model(self, doc: MongoDocument) -> T:

        """
        Maps a MongoDB document to the model type T.

        :param doc: The MongoDB document to map.
        :type doc: MongoDocument
        :return: An instance of type T.
        :rtype: T
        """

        return self._base_model(**doc)
    # endDef

    async def _execute_find_one(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort: list[tuple[str, int]] | None = None,
        collation: Collation | None = None,
        ) -> MongoDocument | None:

        """
        Executes a find_one operation on the collection.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort: Optional sorting options.
        :type sort: list[tuple[str, int]] | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: The first matching document, or None.
        :rtype: MongoDocument | None
        """

        try:
            return await self._collection.find_one(
                filter=filter_query,
                projection=projection,
                sort=sort,
                collation=collation,
            )
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_find_one for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_find_many(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int | None = None,
        collation: Collation | None = None,
        ) -> list[MongoDocument]:

        """
        Executes a find operation on the collection with pagination support.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort: Optional sorting options.
        :type sort: list[tuple[str, int]] | None
        :param skip: Number of documents to skip.
        :type skip: int
        :param limit: Maximum number of documents to return.
        :type limit: int | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: A list of documents matching the query.
        :rtype: list[MongoDocument]
        """

        try:
            cursor = self._collection.find(
                filter=filter_query,
                projection=projection,
                sort=sort,
                skip=skip,
                limit=limit if limit is not None and limit > 0 else 0,
                collation=collation,
            )
            effective_length = limit if limit is not None and limit > 0 else None
            return await cursor.to_list(length=effective_length)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_find_many for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_count_documents(self, filter_query: MongoDocument) -> int:

        """
        Executes a count_documents operation on the collection.

        :param filter_query: The filter query to count documents.
        :type filter_query: MongoDocument
        :return: The count of documents matching the filter query.
        :rtype: int
        """

        try:
            return await self._collection.count_documents(filter_query)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_count_documents for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def find_many_paginated_skip_limit(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort_options: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int | None = 100,
        collation: Collation | None = None,
        ) -> list[T]:

        """
        Finds multiple documents with pagination support using skip and limit. Suitable for
        collections with fewer than 100,000 documents.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort_options: Optional sorting options.
        :type sort_options: list[tuple[str, int]] | None
        :param skip: Number of documents to skip.
        :type skip: int
        :param limit: Maximum number of documents to return. If None, returns all matching.
        :type limit: int | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: A list of documents matching the query.
        :rtype: list[T]
        """

        if limit is not None and limit <= 0:
            return []
        # endIf
        docs = await self._execute_find_many(
            filter_query,
            projection,
            sort_options,
            skip,
            limit,
            collation,
        )
        if projection is None:
            return [self._read_map_to_model(doc) for doc in docs]
        # endIf
        # A projection was used; return raw documents (they may not satisfy the model type T).
        return docs  # type: ignore[return-value]
    # endDef

    async def find_many_paginated_seek(
        self,
        base_filter_query: MongoDocument,
        sort_field: str,
        batch_size: int,
        last_seen_value: Any | None = None,
        sort_order: int = ASCENDING,
        projection: MongoDocument | None = None,
        collation: Collation | None = None,
        ) -> AsyncGenerator[list[T], None]:

        """
        Finds multiple documents using seek pagination. Suitable for collections with more than
        100,000 documents.

        :param base_filter_query: The base filter query to apply.
        :type base_filter_query: MongoDocument
        :param sort_field: The field to sort by.
        :type sort_field: str
        :param batch_size: The number of documents to return in each batch.
        :type batch_size: int
        :param last_seen_value: The last seen value for pagination. If None, starts from the start.
        :type last_seen_value: Any | None
        :param sort_order: The sort order (ASCENDING or DESCENDING).
        :type sort_order: int
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: An async generator yielding lists of documents in batches.
        :rtype: AsyncGenerator[list[T], None]
        """

        if batch_size <= 0:
            raise ValueError("Batch size must be greater than 0.")
        # endIf
        if sort_order not in [ASCENDING, DESCENDING]:
            raise ValueError("sort_order must be ASCENDING or DESCENDING")
        # endIf
        current_filter = base_filter_query.copy()
        _last_seen_value = last_seen_value
        while True:
            iter_filter = current_filter.copy()
            if _last_seen_value is not None:
                if sort_order == ASCENDING:
                    iter_filter[sort_field] = {"$gt": _last_seen_value}
                else:
                    iter_filter[sort_field] = {"$lt": _last_seen_value}
                # endIfElse
            # endIf
            try:
                docs_batch = await self._execute_find_many(
                    filter_query=iter_filter,
                    projection=projection,
                    sort=[(sort_field, sort_order)],
                    limit=batch_size,
                    collation=collation,
                )
                if not docs_batch:
                    break
                # endIf
                if projection is None:
                    yield [self._read_map_to_model(doc) for doc in docs_batch]
                else:
                    yield docs_batch  # type: ignore[misc]
                # endIfElse
                _last_seen_value = docs_batch[-1].get(sort_field)
                if _last_seen_value is None:
                    break
                # endIf
            except Exception as generic_exception:
                raise RuntimeError(
                    f"Error in find_many_paginated_seek for {self._collection_name}, "
                    f"exception: {generic_exception!r}",
                )
            # endTryExcept
        # endWhile
    # endDef

    async def count(self, filter_query: MongoDocument | None = None) -> int:

        """
        Counts the number of documents matching the filter query.

        :param filter_query: The filter query. If None, counts all documents in the collection.
        :type filter_query: MongoDocument | None
        :return: The count of documents matching the filter query.
        :rtype: int
        """

        return await self._execute_count_documents(filter_query or {})
    # endDef

    @abstractmethod
    async def find_one(
        self,
        filter_query: MongoDocument,
        projection: MongoDocument | None = None,
        sort_options: list[tuple[str, int]] | None = None,
        collation: Collation | None = None,
        ) -> T | None:

        """
        Finds a single document matching the filter query.

        :param filter_query: The filter query to apply.
        :type filter_query: MongoDocument
        :param projection: Optional projection to apply.
        :type projection: MongoDocument | None
        :param sort_options: Optional sorting options.
        :type sort_options: list[tuple[str, int]] | None
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: The first matching document or None if no document matches.
        :rtype: T | None
        """

        ...
    # endDef
# endClass


class BaseWriteMotorRepository[T](ABC):

    """
    Base write repository for MongoDB (Motor) collections. Make sure the inheriting class defines
    ``_collection_name`` and ``_database_name``.
    """

    _database_name: str
    _collection_name: str

    def __init__(
        self,
        db_client: AsyncIOMotorClient[MongoDocument],
        base_model: type[T],
        ) -> None:

        """
        Base write repository constructor.

        :param db_client: MongoDB client.
        :type db_client: AsyncIOMotorClient
        :param base_model: The Pydantic model class representing the documents in the collection.
        :type base_model: type[T]
        :return: None.
        :rtype: None
        :raises NotImplementedError: If the subclass does not define _collection_name or
            _database_name.
        """

        if not hasattr(self, "_collection_name") or not self._collection_name:
            raise NotImplementedError("Subclasses must define _collection_name")
        # endIf
        if not hasattr(self, "_database_name") or not self._database_name:
            raise NotImplementedError("Subclasses must define _database_name")
        # endIf
        self.db_client: AsyncIOMotorClient[MongoDocument] = db_client
        self._collection: AsyncIOMotorCollection[MongoDocument] = (
            db_client[self._database_name][self._collection_name]
        )
        self._base_model = base_model
    # endDef

    def _write_map_to_model(self, doc: MongoDocument) -> T:

        """
        Maps a MongoDB document to the model type T.

        :param doc: The MongoDB document to map.
        :type doc: MongoDocument
        :return: An instance of type T.
        :rtype: T
        """

        return self._base_model(**doc)
    # endDef

    @staticmethod
    def _write_map_to_document(model: T) -> MongoDocument:

        """
        Maps the model type T to a MongoDB document and ensures the _id field is an ObjectId if
        present.

        :param model: The model instance to map.
        :type model: T
        :return: A MongoDB document representation of the model.
        :rtype: MongoDocument
        """

        # Pydantic v2: model_dump instead of dict.
        doc: MongoDocument = model.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
        # Ensure _id is ObjectId if present and not None.
        if "_id" in doc and doc["_id"] is not None and not isinstance(doc["_id"], bson.ObjectId):
            doc["_id"] = bson.ObjectId(doc["_id"])
        elif "_id" in doc and doc["_id"] is None:  # Remove if id was None and became _id: None.
            del doc["_id"]
        # endIfElif
        return doc
    # endDef

    async def _execute_insert_one(self, document: MongoDocument) -> InsertOneResult:

        """
        Executes an insert_one operation on the collection.

        :param document: The document to insert.
        :type document: MongoDocument
        :return: The result of the insert operation.
        :rtype: InsertOneResult
        :raises DuplicateKeyError: On a unique-index conflict.
        """

        try:
            return await self._collection.insert_one(document)
        except pymongo_errors.DuplicateKeyError as duplicate_key_error:
            raise DuplicateKeyError(
                message=f"Duplicate key error for document: {document}",
                duplicate_key=duplicate_key_error.details.get("keyValue", None)
                if duplicate_key_error.details
                else None,
            ) from duplicate_key_error
        except bson.errors.InvalidDocument:
            raise RuntimeError(
                f"Invalid document error in _execute_insert_one for {self._collection_name}",
            )
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_insert_one for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_insert_many(self, documents: list[MongoDocument]) -> InsertManyResult:

        """
        Executes an insert_many operation on the collection.

        :param documents: The documents to insert.
        :type documents: list[MongoDocument]
        :return: The result of the insert operation.
        :rtype: InsertManyResult
        :raises DuplicateKeyError: On a unique-index conflict.
        """

        try:
            return await self._collection.insert_many(documents)
        except pymongo_errors.DuplicateKeyError as duplicate_key_error:
            raise DuplicateKeyError(
                message=f"Duplicate key error for documents in {self._collection_name}",
                duplicate_key=duplicate_key_error.details.get("keyValue", None)
                if duplicate_key_error.details
                else None,
            ) from duplicate_key_error
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_insert_many for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_update_one(
        self,
        filter_query: MongoDocument,
        update_doc: MongoDocument,
        upsert: bool = False,
        ) -> UpdateResult:

        """
        Executes an update_one operation on the collection.

        :param filter_query: The filter query to match the document.
        :type filter_query: MongoDocument
        :param update_doc: The update document to apply.
        :type update_doc: MongoDocument
        :param upsert: When True, insert a new document if none match.
        :type upsert: bool
        :return: The result of the update operation.
        :rtype: UpdateResult
        """

        try:
            return await self._collection.update_one(filter_query, update_doc, upsert=upsert)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_update_one for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_update_many(
        self,
        filter_query: MongoDocument,
        update_docs: list[MongoDocument],
        upsert: bool = False,
        ) -> UpdateResult:

        """
        Executes an update_many operation on the collection (applies the provided update document).

        :param filter_query: The filter query to match documents.
        :type filter_query: MongoDocument
        :param update_docs: The update document payloads to apply.
        :type update_docs: list[MongoDocument]
        :param upsert: When True, insert a new document if none match.
        :type upsert: bool
        :return: The result of the update operation.
        :rtype: UpdateResult
        """

        update_doc = update_docs[0] if update_docs else {}
        try:
            return await self._collection.update_many(filter_query, update_doc, upsert=upsert)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_update_many for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_pipeline(
        self,
        pipeline: list[MongoDocument],
        collation: Collation | None = None,
        ) -> list[MongoDocument]:

        """
        Executes an aggregation pipeline on the collection.

        :param pipeline: The aggregation pipeline stages.
        :type pipeline: list[MongoDocument]
        :param collation: Optional collation to apply.
        :type collation: Collation | None
        :return: The aggregation result documents.
        :rtype: list[MongoDocument]
        """

        try:
            cursor = self._collection.aggregate(pipeline, collation=collation)
            return await cursor.to_list(length=None)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_pipeline for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    @abstractmethod
    async def create(self, entity: T) -> T:

        """
        Create a single document from the entity and return the persisted model.

        :param entity: The model to persist.
        :type entity: T
        :return: The persisted model.
        :rtype: T
        """

        ...
    # endDef

    @abstractmethod
    async def create_many(self, entities: list[T]) -> list[T]:

        """
        Create multiple documents from the entities.

        :param entities: The models to persist.
        :type entities: list[T]
        :return: The persisted models.
        :rtype: list[T]
        """

        ...
    # endDef

    @abstractmethod
    async def update_one(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> T | None:

        """
        Update a single document and return the updated model.

        :param filter_query: The filter query to match the document to update.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload to apply.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, create a new document if none match.
        :type upsert: bool
        :return: The updated model, or None if no document was found or updated.
        :rtype: T | None
        """

        ...
    # endDef

    @abstractmethod
    async def update_many(
        self,
        filter_query: MongoDocument,
        update_doc_payload: MongoDocument,
        upsert: bool = False,
        ) -> int:

        """
        Update multiple documents in the collection.

        :param filter_query: The filter query to match documents to update.
        :type filter_query: MongoDocument
        :param update_doc_payload: The update document payload to apply.
        :type update_doc_payload: MongoDocument
        :param upsert: When True, create a new document if none match.
        :type upsert: bool
        :return: The number of documents modified.
        :rtype: int
        """

        ...
    # endDef
# endClass


class BaseDeleteMotorRepository[T](ABC):

    """
    Base delete repository for MongoDB collections using Motor. Subclasses must define the
    ``_collection_name`` and ``_database_name`` attributes.
    """

    _database_name: str
    _collection_name: str

    def __init__(
        self,
        db_client: AsyncIOMotorClient[MongoDocument],
        base_model: type[T],
        ) -> None:

        """
        Base delete repository constructor.

        :param db_client: MongoDB client.
        :type db_client: AsyncIOMotorClient
        :param base_model: The Pydantic model class representing the documents in the collection.
        :type base_model: type[T]
        :return: None.
        :rtype: None
        :raises NotImplementedError: If the subclass does not define _collection_name or
            _database_name.
        """

        if not hasattr(self, "_collection_name") or not self._collection_name:
            raise NotImplementedError("Subclasses must define _collection_name")
        # endIf
        if not hasattr(self, "_database_name") or not self._database_name:
            raise NotImplementedError("Subclasses must define _database_name")
        # endIf
        self.db_client: AsyncIOMotorClient[MongoDocument] = db_client
        self._collection: AsyncIOMotorCollection[MongoDocument] = (
            db_client[self._database_name][self._collection_name]
        )
        self._base_model = base_model
    # endDef

    async def _execute_delete_one(self, filter_query: MongoDocument) -> DeleteResult:

        """
        Executes a delete_one operation on the collection.

        :param filter_query: The filter query to find the document to delete.
        :type filter_query: MongoDocument
        :return: The result of the delete operation.
        :rtype: DeleteResult
        """

        try:
            return await self._collection.delete_one(filter_query)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_delete_one for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    async def _execute_delete_many(self, filter_query: MongoDocument) -> DeleteResult:

        """
        Executes a delete_many operation on the collection.

        :param filter_query: The filter query to find the documents to delete.
        :type filter_query: MongoDocument
        :return: The result of the delete operation.
        :rtype: DeleteResult
        """

        try:
            return await self._collection.delete_many(filter_query)
        except Exception as generic_exception:
            raise RuntimeError(
                f"Error in _execute_delete_many for {self._collection_name}, "
                f"exception: {generic_exception!r}",
            )
        # endTryExcept
    # endDef

    @abstractmethod
    async def delete_one(self, filter_query: MongoDocument) -> bool:

        """
        Delete a single document matching the filter query.

        :param filter_query: The filter query to match the document to delete.
        :type filter_query: MongoDocument
        :return: True when a document was deleted.
        :rtype: bool
        """

        ...
    # endDef

    @abstractmethod
    async def delete_many(self, filter_query: MongoDocument) -> int:

        """
        Delete multiple documents matching the filter query.

        :param filter_query: The filter query to match documents to delete.
        :type filter_query: MongoDocument
        :return: The number of documents deleted.
        :rtype: int
        """

        ...
    # endDef
# endClass


# end_common/db/motor_repository.py
