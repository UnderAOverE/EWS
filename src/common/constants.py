#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/constants.py.                                                               #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Shared cross-cutting constants: DatabasesCollections (Mongo database and collection   #
#                 names, including the zelle context) and HTTPCodes (HTTP status codes). Local mirror   #
#                 of the host's common.constants (a partial copy — the host holds the full set).       #
# Dependencies  : Standard library only (enum).                                                        #
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

from enum import IntEnum, StrEnum, auto

# Internal imports

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class Constants(StrEnum):

    """
    General state, action, and status words. Partial mirror of the host's ``Constants``
    StrEnum (the host holds the full set); members use ``auto()`` so each value is the
    lowercase member name, exactly like the host.
    """

    cancel = auto()
    complete = auto()
    pending = auto()
    resolve = auto()
    schedule = auto()
    start = auto()
    succeeded = auto()

# endClass


class DatabasesCollections(StrEnum):

    """
    Mongo database and collection names. Partial mirror of the host set — includes the databases
    and the zelle-context collections. Concrete repositories reference these for _database_name and
    _collection_name.
    """

    # Databases.
    AMP_MAIN_DATABASE = "amp"
    APIS_DATABASE = "fdn-c-amp-apis-py"
    APPLICATION_MAIN_DATABASE = "fdn-c-amp-fapis-py"
    AWS_DATABASE = "fdn-c-amp-aws-cronjob"
    COMMON_DATABASE = "common"
    OPENSHIFT_DATABASE = "OpenShift"
    # Existing collections (subset).
    AUDITS_COLLECTION = "audits"
    SAAS_CACHE_COLLECTION = "saas_cache"
    SETTINGS_COLLECTION = "settings"
    # Zelle bounded context collections.
    ZELLE_EVENTS_COLLECTION = "zelle_events"
    ZELLE_IDEMPOTENCY_COLLECTION = "zelle_idempotency"
    ZELLE_AUDIT_COLLECTION = "zelle_audit"
    ZELLE_LEASES_COLLECTION = "zelle_leases"
# endClass


class HTTPCodes(IntEnum):

    """
    HTTP status codes used across request handling.
    """

    SUCCESS = 200
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204
    MIXED_CONTENT = 207
    MULTIPLE_CHOICES = 300
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONTENT_TYPE_NOT_ACCEPTABLE = 406
    CONFLICT = 409
    UNSUPPORTED_MEDIA_TYPE = 415
    TEAPOT = 418
    UNPROCESSABLE_ENTITY = 422
    RATE_LIMITED_TOO_MANY_REQUESTS = 429
    SSL_CERTIFICATE_ERROR = 495
    SSL_CERTIFICATE_REQUIRED = 496
    INTERNAL_SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503
# endClass


# end_common/constants.py
