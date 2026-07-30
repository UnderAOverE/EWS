#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/db/utils.py.                                                                #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : PyObjectId — a reusable pydantic v2 annotated type wrapping bson.ObjectId:           #
#                 validates ObjectId/str-of-ObjectId and serializes to str. Use as the _id field       #
#                 type on any Mongo-backed model. Local mirror of the host's common.db.utils.          #
# Dependencies  : bson, pydantic, pydantic_core.                                                       #
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

from typing import Annotated, Any

from bson import ObjectId
from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

# Internal imports

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class _PyObjectId:

    """
    Pydantic v2 adapter for :class:`bson.ObjectId`: validates an ObjectId (or a valid ObjectId
    string) and serializes it back to a plain string.
    """

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
        ) -> core_schema.CoreSchema:

        """
        Provide the core schema used by pydantic to validate and serialize ObjectId values.

        :param source_type: The annotated source type.
        :type source_type: Any
        :param handler: The pydantic core-schema handler.
        :type handler: GetCoreSchemaHandler
        :return: The core schema.
        :rtype: core_schema.CoreSchema
        """

        def _validate(value: Any) -> ObjectId:

            """
            Coerce an ObjectId or ObjectId-string to an ObjectId.

            :param value: The value under validation.
            :type value: Any
            :return: The validated ObjectId.
            :rtype: ObjectId
            :raises ValueError: If the value is not a valid ObjectId.
            """

            if isinstance(value, ObjectId):
                return value
            # endIf
            if isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
            # endIf
            raise ValueError(f"Invalid ObjectId: {value!r}")
        # endDef

        return core_schema.no_info_plain_validator_function(
            _validate,
            # This defines how the ObjectId is serialized (converted to a string).
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda instance: str(instance),
            ),
        )
    # endDef
# endClass


# --- Create the Reusable Annotated Type ---
# PyObjectId is now a reusable type that you can import and use in any Pydantic model.
PyObjectId = Annotated[ObjectId, _PyObjectId]


# end_common/db/utils.py
