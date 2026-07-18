#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/enums.py.                                                         #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Closed vocabularies for the zelle module as StrEnum types: event status, hold       #
#                 mode, consumer error codes, lifecycle actions, and audit kinds/outcomes.            #
# Dependencies  : Standard library (enum, logging).                                                   #
# Modifications : 2026-07-16 Shane Reddy — Initial version.                                           #
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
from enum import StrEnum

# Internal imports

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EventStatus(StrEnum):

    """
    Facade-local lifecycle status of a maintenance event (state machine per architecture §5).
    """

    PENDING = "PENDING"
    PENDING_UPSTREAM_ID = "PENDING_UPSTREAM_ID"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"
# endClass


class HoldMode(StrEnum):

    """
    Who holds MQ messages during the window; values are the exact EWS ``ewsHold`` wire strings.
    """

    EWS_HOLD = "EWS_HOLD"
    SELF_HOLD = "SELF_HOLD"
# endClass


class ErrorCode(StrEnum):

    """
    Consumer-facing error codes carried in the error envelope; EWS vocabulary never leaks here.
    """

    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFLICT = "CONFLICT"
    FORBIDDEN_ACTION = "FORBIDDEN_ACTION"
    NOT_FOUND = "NOT_FOUND"
    UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_UNCERTAIN = "UPSTREAM_UNCERTAIN"
# endClass


class LifecycleAction(StrEnum):

    """
    Lifecycle verbs on a scheduled event. Values are lowercase so they double as the EWS
    ``/v1/events/{action}`` path segment and the audit ``action`` string.
    """

    START = "start"
    COMPLETE = "complete"
    CANCEL = "cancel"
# endClass


class AuditKind(StrEnum):

    """
    Audit document kind: INTENT is inserted before every southbound call, OUTCOME after.
    """

    INTENT = "INTENT"
    OUTCOME = "OUTCOME"
# endClass


class AuditOutcome(StrEnum):

    """
    Terminal classification of an audited attempt (OUTCOME documents only).
    """

    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    UNCERTAIN = "UNCERTAIN"
    REPLAYED = "REPLAYED"
    DRY_RUN = "DRY_RUN"
# endClass


# end_apis/models/zelle/enums.py
