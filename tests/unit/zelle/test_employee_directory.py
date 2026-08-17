#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_employee_directory.py.                                        #
# Date of birth : 2026-08-17.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : EmployeeDirectoryClient contract tests over respx: alias parsing on a hit, every    #
#                 failure mode ("Not found in GlobalDirectory" text, non-2xx, transport error,        #
#                 unparseable body) degrading to None, and the TTL cache short-circuiting repeat      #
#                 lookups.                                                                            #
# Dependencies  : httpx, pytest, respx, common.employee_directory.                                    #
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

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

# Internal imports

from src.common.employee_directory import EmployeeDirectoryClient

# Local variables

LOGGER = logging.getLogger(__name__)
BASE_URL = "http://directory.internal/employees"
USERNAME = "sreddy"
LOOKUP_URL = f"{BASE_URL}/{USERNAME}"
EMPLOYEE_JSON = {
    "ritsId": "R123",
    "soeid": "SR12345",
    "name": "Shane Reddy",
    "emailAddress": "sreddy@bank.com",
    "phone": "+1 (555) 123-4567",
    "department": "Payments Engineering",
}


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:

    """
    An async HTTP client for the directory calls (respx intercepts its transport).
    """

    async with httpx.AsyncClient() as instance:
        yield instance
    # endWith
# endDef


@pytest.fixture
def directory(http_client: httpx.AsyncClient) -> EmployeeDirectoryClient:

    """
    The client under test with short timeouts and a generous cache TTL.
    """

    return EmployeeDirectoryClient(
        BASE_URL,
        http_client,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        cache_ttl_seconds=600.0,
    )
# endDef


@respx.mock
async def test_lookup_parses_aliases(directory: EmployeeDirectoryClient) -> None:

    """
    A directory hit parses the aliased fields (emailAddress, ritsId) into the record.
    """

    respx.get(LOOKUP_URL).mock(return_value=httpx.Response(200, json=EMPLOYEE_JSON))
    record = await directory.get_employee(USERNAME)
    assert record is not None
    assert record.name == "Shane Reddy"
    assert record.email_address == "sreddy@bank.com"
    assert record.phone == "+1 (555) 123-4567"
    assert record.rits_id == "R123"
# endDef


@respx.mock
async def test_not_found_marker_text_is_none(directory: EmployeeDirectoryClient) -> None:

    """
    The directory's "Not found in GlobalDirectory" body (even on HTTP 200) degrades to None.
    """

    respx.get(LOOKUP_URL).mock(
        return_value=httpx.Response(200, text="Not found in GlobalDirectory"),
    )
    assert await directory.get_employee(USERNAME) is None
# endDef


@respx.mock
async def test_non_2xx_is_none(directory: EmployeeDirectoryClient) -> None:

    """
    A non-200 (e.g. 404 or 500) degrades to None, never an exception.
    """

    respx.get(LOOKUP_URL).mock(return_value=httpx.Response(500, text="boom"))
    assert await directory.get_employee(USERNAME) is None
# endDef


@respx.mock
async def test_transport_failure_is_none(directory: EmployeeDirectoryClient) -> None:

    """
    An unreachable directory degrades to None, never an exception.
    """

    respx.get(LOOKUP_URL).mock(side_effect=httpx.ConnectError("down"))
    assert await directory.get_employee(USERNAME) is None
# endDef


@respx.mock
async def test_unparseable_body_is_none(directory: EmployeeDirectoryClient) -> None:

    """
    A 200 with a non-JSON body degrades to None.
    """

    respx.get(LOOKUP_URL).mock(return_value=httpx.Response(200, text="<html>login</html>"))
    assert await directory.get_employee(USERNAME) is None
# endDef


@respx.mock
async def test_cache_short_circuits_repeat_lookups(
    directory: EmployeeDirectoryClient,
    ) -> None:

    """
    A second lookup within the TTL is served from the cache — exactly one HTTP call is made.
    """

    route = respx.get(LOOKUP_URL).mock(return_value=httpx.Response(200, json=EMPLOYEE_JSON))
    first = await directory.get_employee(USERNAME)
    second = await directory.get_employee(USERNAME)
    assert first is not None and second is not None
    assert route.call_count == 1
# endDef


# end_tests/unit/zelle/test_employee_directory.py
