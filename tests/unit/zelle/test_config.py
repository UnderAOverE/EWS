#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_config.py.                                                    #
# Date of birth : 2026-08-11.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : ZelleSettings derivation tests — api_base_url, token_url, and token_aud resolve      #
#                 from is_production to the EWS-confirmed CAT/PROD values, and explicit values        #
#                 always win over derivation.                                                         #
# Dependencies  : pytest, apis.config.zelle.                                                          #
# Modifications : 2026-08-11 Shane Reddy — Initial version.                                           #
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
from pathlib import Path
from typing import Any

import pytest

# Internal imports

from src.apis.config.zelle import (
    CAT_API_BASE_URL,
    CAT_TOKEN_AUD,
    CAT_TOKEN_URL,
    PROD_API_BASE_URL,
    PROD_TOKEN_AUD,
    PROD_TOKEN_URL,
    ZelleSettings,
)

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@pytest.fixture(autouse=True)
def _clean_derivation_env(monkeypatch: pytest.MonkeyPatch) -> None:

    """
    Remove any ambient ZELLE_* endpoint vars so the derivation tests see true defaults.

    :param monkeypatch: The pytest monkeypatch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    :return: None.
    :rtype: None
    """

    for name in (
        "ZELLE_IS_PRODUCTION",
        "ZELLE_API_BASE_URL",
        "ZELLE_TOKEN_URL",
        "ZELLE_TOKEN_AUD",
    ):
        monkeypatch.delenv(name, raising=False)
    # endFor
# endDef


def _settings(**overrides: Any) -> ZelleSettings:

    """
    Build settings with the minimum required fields plus overrides.

    :param overrides: Field overrides for the case under test.
    :type overrides: Any
    :return: The settings model.
    :rtype: ZelleSettings
    """

    base: dict[str, Any] = {
        "client_id": "test-client-id",
        "signing_kid": "kid-1",
        "signing_key_path": Path("signing.pem"),
        "org_id": "BBO",
        "participant_name": "Bobs Bank of Omaha",
        "submitted_name": "Bob Barker",
        "contact_name": "Terry Technology",
        "contact_phone": "9999999977",
        "contact_email": "TTechnology@BBO.com",
    }
    base.update(overrides)
    return ZelleSettings(**base)
# endDef


def test_cat_defaults_derive_from_is_production_false() -> None:

    """
    Non-production derives the CAT base URL, token URL, and token audience.
    """

    settings = _settings(is_production=False)
    assert settings.api_base_url == CAT_API_BASE_URL
    assert settings.token_url == CAT_TOKEN_URL
    assert settings.token_aud == CAT_TOKEN_AUD
# endDef


def test_prod_defaults_derive_from_is_production_true() -> None:

    """
    Production derives the PROD base URL, token URL, and token audience.
    """

    settings = _settings(is_production=True)
    assert settings.api_base_url == PROD_API_BASE_URL
    assert settings.token_url == PROD_TOKEN_URL
    assert settings.token_aud == PROD_TOKEN_AUD
# endDef


def test_explicit_endpoint_values_always_win() -> None:

    """
    Explicit api_base_url / token_url / token_aud beat derivation — the stub/override path.
    """

    settings = _settings(
        is_production=True,
        api_base_url="http://fake-ews/zoms",
        token_url="http://fake-ews/token",
        token_aud="http://fake-ews",
    )
    assert settings.api_base_url == "http://fake-ews/zoms"
    assert settings.token_url == "http://fake-ews/token"
    assert settings.token_aud == "http://fake-ews"
# endDef


# end_tests/unit/zelle/test_config.py
