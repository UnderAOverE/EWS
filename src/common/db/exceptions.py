#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/db/exceptions.py.                                                           #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Database exception types. MongoError is the base; DuplicateKeyError is raised by      #
#                 the base repositories when a unique-index conflict occurs, carrying the offending     #
#                 key. Local mirror of the host's common.db.exceptions.                               #
# Dependencies  : Standard library only (sys).                                                         #
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

from typing import Any

# Internal imports

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class MongoError(Exception):

    """
    Base class for database (Mongo) errors surfaced by the repositories.
    """
# endClass


class DuplicateKeyError(MongoError):

    """
    Raised for duplicate key errors.
    """

    def __init__(
        self,
        message: str,
        duplicate_key: Any = None,
        ) -> None:

        """
        Initialize the DuplicateKeyError.

        :param message: Error message.
        :type message: str
        :param duplicate_key: The key that caused the duplication error (optional).
        :type duplicate_key: Any
        :return: None.
        :rtype: None
        """

        super().__init__(message)
        self.duplicate_key = duplicate_key
    # endDef

    def __str__(self) -> str:

        """
        Render the error with its duplicate key.

        :return: The string representation.
        :rtype: str
        """

        return f"{super().__str__()} (duplicate_key={self.duplicate_key})"
    # endDef
# endClass


# end_common/db/exceptions.py
