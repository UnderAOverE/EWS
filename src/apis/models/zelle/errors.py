#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/errors.py.                                                        #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Consumer-facing error envelope models, the ZelleFacadeError hierarchy carrying      #
#                 code/status/retryability, and the FastAPI exception handler that renders the        #
#                 envelope (with Retry-After when known). EWS bodies never leak northbound.           #
# Dependencies  : fastapi, pydantic, apis.models.zelle.enums.                                         #
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
import math

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# Internal imports

from src.apis.models.zelle.enums import ErrorCode

# Local variables

LOGGER = logging.getLogger(__name__)
DEFAULT_CORRELATION_ID = "unknown"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class ErrorDetail(BaseModel):

    """
    Single consumer-facing error payload; the wire form is camelCase (``correlationId``).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    code: ErrorCode
    message: str
    correlation_id: str
    retryable: bool
# endClass


class ErrorEnvelope(BaseModel):

    """
    Top-level error envelope returned to consumers: ``{"error": {...}}``.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    error: ErrorDetail
# endClass


class ZelleFacadeError(Exception):

    """
    Base of the facade error hierarchy. Subclasses pin ``code``, ``status_code`` and
    ``retryable`` as class attributes; instances carry the consumer-safe ``message`` and an
    optional ``retry_after_seconds`` hint surfaced as the Retry-After header.

    :param message: Consumer-safe message; never a raw EWS body.
    :type message: str
    :param retry_after_seconds: Optional retry hint in seconds; None when unknown.
    :type retry_after_seconds: float | None
    """

    code: ErrorCode = ErrorCode.UPSTREAM_REJECTED
    status_code: int = 502
    retryable: bool = False

    def __init__(
        self,
        message: str,
        retry_after_seconds: float | None = None,
        ) -> None:

        """
        Store the consumer-safe message and the optional retry hint.

        :param message: Consumer-safe message; never a raw EWS body.
        :type message: str
        :param retry_after_seconds: Optional retry hint in seconds; None when unknown.
        :type retry_after_seconds: float | None
        """

        super().__init__(message)
        self.message: str = message
        self.retry_after_seconds: float | None = retry_after_seconds
    # endDef
# endClass


class ConflictError(ZelleFacadeError):

    """
    409 ``CONFLICT`` — state-machine violation, overlap, ticket mismatch, or idempotency
    body-hash / in-flight conflict.
    """

    code = ErrorCode.CONFLICT
    status_code = 409
    retryable = False
# endClass


class ForbiddenActionError(ZelleFacadeError):

    """
    403 ``FORBIDDEN_ACTION`` — caller is not in the applicable client allowlist.
    """

    code = ErrorCode.FORBIDDEN_ACTION
    status_code = 403
    retryable = False
# endClass


class NotFoundError(ZelleFacadeError):

    """
    404 ``NOT_FOUND`` — no event with the requested id exists locally.
    """

    code = ErrorCode.NOT_FOUND
    status_code = 404
    retryable = False
# endClass


class UpstreamRejectedError(ZelleFacadeError):

    """
    502 ``UPSTREAM_REJECTED`` — EWS rejected the call; the rejected fields were
    facade-enriched, so a vendor 4xx is facade-owned, never a consumer 4xx.
    """

    code = ErrorCode.UPSTREAM_REJECTED
    status_code = 502
    retryable = False
# endClass


class UpstreamUnavailableError(ZelleFacadeError):

    """
    503 ``UPSTREAM_UNAVAILABLE`` — EWS unreachable or persistently 5xx; the call never
    executed (or failed cleanly pre-send), so the consumer may retry.
    """

    code = ErrorCode.UPSTREAM_UNAVAILABLE
    status_code = 503
    retryable = True
# endClass


class RateLimitedError(ZelleFacadeError):

    """
    503 ``RATE_LIMITED`` — EWS throttled the call; retry after the hinted delay.
    """

    code = ErrorCode.RATE_LIMITED
    status_code = 503
    retryable = True
# endClass


class UpstreamUncertainError(ZelleFacadeError):

    """
    502 ``UPSTREAM_UNCERTAIN`` — the request may have executed (post-send failure or lifecycle
    5xx); the event is locked pending manual reconciliation.
    """

    code = ErrorCode.UPSTREAM_UNCERTAIN
    status_code = 502
    retryable = False
# endClass


class AuthConfigError(ZelleFacadeError):

    """
    502 ``UPSTREAM_REJECTED`` — signing key / client registration incident on the token
    endpoint (400/401 from /token, or repeated 401 from ZOMS). Never retried; alerts on a
    distinct channel from transient failures.
    """

    code = ErrorCode.UPSTREAM_REJECTED
    status_code = 502
    retryable = False
# endClass


class ValidationFailedError(ZelleFacadeError):

    """
    400 ``VALIDATION_FAILED`` — a malformed or missing required header (e.g. ``X-Client-Id``).
    Body validation failures surface as 422 with the same code via
    :func:`validation_exception_handler` instead.
    """

    code = ErrorCode.VALIDATION_FAILED
    status_code = 400
    retryable = False
# endClass


def zelle_exception_handler(request: Request, exc: ZelleFacadeError) -> JSONResponse:

    """
    Translate a :class:`ZelleFacadeError` into the consumer error envelope.

    :param request: The active request; ``request.state.correlation_id`` feeds the envelope and
        falls back to ``"unknown"`` when absent.
    :type request: Request
    :param exc: The raised facade error.
    :type exc: ZelleFacadeError
    :return: JSON response carrying the envelope, an ``X-Correlation-Id`` header, and a
        ``Retry-After`` header when the error carries a hint.
    :rtype: JSONResponse
    """

    correlation_id = getattr(request.state, "correlation_id", DEFAULT_CORRELATION_ID)
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            retryable=exc.retryable,
        ),
    )
    headers: dict[str, str] = {"X-Correlation-Id": correlation_id}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(math.ceil(exc.retry_after_seconds))
    # endIf
    # Log metadata only — messages are facade-authored, but bodies/tokens/PII never reach here.
    LOGGER.info(
        "zelle facade error code=%s status=%s correlation_id=%s retryable=%s",
        exc.code,
        exc.status_code,
        correlation_id,
        exc.retryable,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(mode="json", by_alias=True),
        headers=headers,
    )
# endDef


def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
    ) -> JSONResponse:

    """
    Override FastAPI's default RequestValidationError response with the consumer envelope
    (422 ``VALIDATION_FAILED``). The details echo the consumer's own invalid input — pydantic
    messages, never EWS vocabulary.

    :param request: The active request; ``request.state.correlation_id`` feeds the envelope and
        falls back to ``"unknown"`` when absent.
    :type request: Request
    :param exc: The raised validation error.
    :type exc: RequestValidationError
    :return: JSON response carrying the envelope and an ``X-Correlation-Id`` header.
    :rtype: JSONResponse
    """

    correlation_id = getattr(request.state, "correlation_id", DEFAULT_CORRELATION_ID)
    details = "; ".join(
        f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg', 'invalid')}"
        for error in exc.errors()
    )
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Request validation failed: {details}",
            correlation_id=correlation_id,
            retryable=False,
        ),
    )
    return JSONResponse(
        status_code=422,
        content=envelope.model_dump(mode="json", by_alias=True),
        headers={"X-Correlation-Id": correlation_id},
    )
# endDef


# end_apis/models/zelle/errors.py
