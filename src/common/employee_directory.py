#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/employee_directory.py.                                                       #
# Date of birth : 2026-08-17.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Local mirror of the host application's employee directory (GlobalDirectory)         #
#                 integration: a partial EmployeeRecord model and an async lookup client with a       #
#                 bounded TTL cache. Every failure mode (down, non-JSON, "Not found in                #
#                 GlobalDirectory", unusable record) degrades to None so callers fall back to         #
#                 configured defaults. Replaced by the host's real client at merge.                   #
# Dependencies  : httpx, pydantic, common.logger.                                                     #
# Modifications : 2026-08-17 Shane Reddy — Initial version.                                           #
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

import time
from typing import Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Internal imports

from src.common.constants import HTTPCodes
from src.common.logger import logger

# Local variables

# The directory's not-found responses carry this text rather than a clean 404 body.
NOT_FOUND_MARKER = "Not found in GlobalDirectory"
# Bounded cache: entries older than the TTL are re-fetched; the oldest entries are evicted
# beyond this size so a scan of usernames cannot grow the map unbounded.
CACHE_MAX_ENTRIES = 512


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EmployeeRecord(BaseModel):

    """
    PARTIAL mirror of the host's EmployeeModel (GlobalDirectory) — only the fields the zelle
    facade consumes. Every field is optional upstream, so every field is optional here; unknown
    fields are ignored. The host's full model replaces this at merge.
    """

    model_config = ConfigDict(populate_by_name=True)

    rits_id: str | None = Field(default=None, alias="ritsId")
    soeid: str | None = None
    name: str | None = None
    email_address: str | None = Field(default=None, alias="emailAddress")
    phone: str | None = None
    department: str | None = None
# endClass


class EmployeeLookup(Protocol):

    """
    Port for resolving an SSO username to an employee record. Satisfied by
    :class:`EmployeeDirectoryClient` locally and by the host's real directory client at merge.
    """

    async def get_employee(self, username: str) -> EmployeeRecord | None:

        """
        Resolve a username to an employee record, or None when unavailable.

        :param username: The SSO username (``Sm-User`` header value).
        :type username: str
        :return: The employee record, or None on any failure or not-found.
        :rtype: EmployeeRecord | None
        """

        ...
    # endDef
# endClass


class EmployeeDirectoryClient:

    """
    Async GlobalDirectory lookup adapter: ``GET {base_url}/{username}`` on the injected internal
    HTTP client (never the EWS-proxied southbound client), with a bounded TTL cache. Returns
    None — never raises — on any failure: an unreachable directory must degrade to configured
    defaults, not break a payments-network call.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        *,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        cache_ttl_seconds: float,
        ) -> None:

        """
        Wire the client.

        :param base_url: The directory API base URL (username is appended as a path segment).
        :type base_url: str
        :param client: The internal-network async HTTP client (plain, no EWS proxy/mTLS).
        :type client: httpx.AsyncClient
        :param connect_timeout_seconds: Connect timeout for a lookup.
        :type connect_timeout_seconds: float
        :param read_timeout_seconds: Read timeout for a lookup.
        :type read_timeout_seconds: float
        :param cache_ttl_seconds: How long a lookup result (found or not) is reused.
        :type cache_ttl_seconds: float
        """

        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = httpx.Timeout(read_timeout_seconds, connect=connect_timeout_seconds)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, tuple[float, EmployeeRecord | None]] = {}
    # endDef

    async def get_employee(self, username: str) -> EmployeeRecord | None:

        """
        Resolve a username to an employee record, consulting the TTL cache first.

        :param username: The SSO username (``Sm-User`` header value).
        :type username: str
        :return: The employee record, or None on any failure or not-found.
        :rtype: EmployeeRecord | None
        """

        cached = self._cache.get(username)
        if cached is not None and time.monotonic() - cached[0] < self._cache_ttl_seconds:
            return cached[1]
        # endIf
        record = await self._fetch(username)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            oldest = min(self._cache, key=lambda key: self._cache[key][0])
            del self._cache[oldest]
        # endIf
        self._cache[username] = (time.monotonic(), record)
        return record
    # endDef

    async def _fetch(self, username: str) -> EmployeeRecord | None:

        """
        Perform one directory lookup, degrading every failure mode to None with a log line
        naming the cause (the username is an attribution id; contact PII is never logged).

        :param username: The SSO username to look up.
        :type username: str
        :return: The employee record, or None.
        :rtype: EmployeeRecord | None
        """

        url = f"{self._base_url}/{quote(username, safe='')}"
        try:
            response = await self._client.get(url, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.warning(
                "employee directory unreachable for user=%s (%s)",
                username,
                type(exc).__name__,
            )
            return None
        # endTryExcept
        if response.status_code != HTTPCodes.SUCCESS:
            logger.warning(
                "employee directory returned HTTP %s for user=%s",
                response.status_code,
                username,
            )
            return None
        # endIf
        if NOT_FOUND_MARKER.lower() in response.text.lower():
            logger.warning("user=%s not found in GlobalDirectory", username)
            return None
        # endIf
        try:
            record = EmployeeRecord.model_validate(response.json())
        except (ValueError, ValidationError):
            logger.warning("employee directory body unparseable for user=%s", username)
            return None
        # endTryExcept
        return record
    # endDef
# endClass


# end_common/employee_directory.py
