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

from enum import IntEnum, StrEnum

# Internal imports

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


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

    HTTP_SUCCESS = 200
    HTTP_CREATED = 201
    HTTP_ACCEPTED = 202
    HTTP_NO_CONTENT = 204
    HTTP_MIXED_CONTENT = 207
    HTTP_BAD_REQUEST = 400
    HTTP_UNAUTHORIZED = 401
    HTTP_FORBIDDEN = 403
    HTTP_NOT_FOUND = 404
    HTTP_METHOD_NOT_ALLOWED = 405
    HTTP_CONTENT_TYPE_NOT_ACCEPTABLE = 406
    HTTP_CONFLICT = 409
    HTTP_UNSUPPORTED_MEDIA_TYPE = 415
    HTTP_TEAPOT = 418
    HTTP_UNPROCESSABLE_ENTITY = 422
    HTTP_RATE_LIMITED_TOO_MANY_REQUESTS = 429
    HTTP_SSL_CERTIFICATE_ERROR = 495
    HTTP_SSL_CERTIFICATE_REQUIRED = 496
    HTTP_INTERNAL_SERVER_ERROR = 500
    HTTP_SERVICE_UNAVAILABLE = 503
# endClass


# end_common/constants.py
