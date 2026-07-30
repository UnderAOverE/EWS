#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/miscellaneous/utils.py.                                                     #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : String sanitizers used by model field validators to strip characters not needed      #
#                 for storage/display (a checkmarx-style safeguard). sanitize_string handles a single   #
#                 string; sanitize_payload_recursive walks nested dict/list/str structures. Local       #
#                 mirror of the host's common.miscellaneous.utils.                                     #
# Dependencies  : Standard library only (re).                                                          #
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

import re
from typing import Any

# Internal imports

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def sanitize_string(input_str: str) -> str:

    """
    Sanitizes a string by removing or escaping special characters.

    :param input_str: The string to sanitize.
    :type input_str: str
    :return: The sanitized string.
    :rtype: str
    """

    # This example uses regex to remove non-alphanumeric characters however spaces, commas, dots,
    # dashes and underscores are removed.
    # commas are needed for cron arguments, dots are needed for dot notations, dashes and
    # underscores are common in names.
    return re.sub(pattern=r"[^a-zA-Z0-9_.,/*\s-]", repl="", string=input_str)
# endDef


def sanitize_payload_recursive(data: Any) -> Any:

    """
    Recursively sanitizes strings within a nested data structure (dict, list, or string). Can be
    used for checkmarx issue.

    :param data: The data structure to sanitize.
    :type data: Any
    :return: The sanitized data structure.
    :rtype: Any
    """

    if isinstance(data, str):
        return sanitize_string(data)
    elif isinstance(data, list):
        return [sanitize_payload_recursive(item) for item in data]
    elif isinstance(data, dict):
        return {key: sanitize_payload_recursive(value) for key, value in data.items()}
    else:
        return data
    # endIfElifElse
# endDef


# end_common/miscellaneous/utils.py
