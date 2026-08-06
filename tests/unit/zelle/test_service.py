#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_service.py.                                                   #
# Date of birth : 2026-08-06.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Tests for the ZelleService wiring helpers — currently the southbound HTTP           #
#                 client construction: the optional corporate egress proxy is passed through to       #
#                 httpx (unwrapped from SecretStr) and omitted by default.                            #
# Dependencies  : pytest, apis.config.zelle, apis.services.zelle.service.                             #
# Modifications : 2026-08-06 Shane Reddy — Initial version.                                           #
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
from typing import Any

import pytest
from pydantic import SecretStr

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.services.zelle import service as service_module

# Local variables

LOGGER = logging.getLogger(__name__)
PROXY_URL = "http://smoke-user:smoke-pass@proxy.bank.local:8080"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@pytest.fixture
def captured_client_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:

    """
    Replace ``httpx.AsyncClient`` (as seen by the service module) with a capture stub and return
    the dict its constructor kwargs land in.

    :param monkeypatch: The pytest monkeypatch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    :return: The (initially empty) captured kwargs mapping.
    :rtype: dict[str, Any]
    """

    captured: dict[str, Any] = {}

    class _CaptureClient:

        """
        Constructor-capturing stand-in for httpx.AsyncClient.
        """

        def __init__(self, **kwargs: Any) -> None:

            """
            Record the constructor kwargs.
            """

            captured.update(kwargs)
        # endDef
    # endClass

    monkeypatch.setattr(service_module.httpx, "AsyncClient", _CaptureClient)
    return captured
# endDef


def test_build_http_client_passes_proxy(
    settings: ZelleSettings,
    captured_client_kwargs: dict[str, Any],
    ) -> None:

    """
    A configured proxy_url is unwrapped from SecretStr and passed to httpx as ``proxy``.
    """

    with_proxy = settings.model_copy(update={"proxy_url": SecretStr(PROXY_URL)})
    service_module._build_http_client(with_proxy)
    assert captured_client_kwargs["proxy"] == PROXY_URL
# endDef


def test_build_http_client_defaults_to_no_proxy(
    settings: ZelleSettings,
    captured_client_kwargs: dict[str, Any],
    ) -> None:

    """
    Without proxy_url the client is built with proxy=None (httpx ambient env behavior intact).
    """

    service_module._build_http_client(settings)
    assert captured_client_kwargs["proxy"] is None
# endDef


def test_proxy_url_never_in_logs(
    settings: ZelleSettings,
    captured_client_kwargs: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    ) -> None:

    """
    The proxy URL (which may embed credentials) never appears in log output — only a boolean.
    """

    with_proxy = settings.model_copy(update={"proxy_url": SecretStr(PROXY_URL)})
    with caplog.at_level(logging.DEBUG):
        service_module._build_http_client(with_proxy)
    # endWith
    assert "smoke-pass" not in caplog.text
    assert "proxy.bank.local" not in caplog.text
# endDef


# end_tests/unit/zelle/test_service.py
