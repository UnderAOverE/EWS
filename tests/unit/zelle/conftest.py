#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/conftest.py.                                                       #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Shared fixtures for the zelle test suite: a throwaway session RSA signing key       #
#                 on disk, a fully-populated ZelleSettings pointed at the fake EWS URLs, and a        #
#                 mongomock Motor database.                                                           #
# Dependencies  : pytest, joserfc, mongomock_motor, apis.config.zelle.                                #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
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

import pytest
from joserfc.jwk import RSAKey
from mongomock_motor import AsyncMongoMockClient

# Internal imports

from src.apis.config.zelle import ZelleSettings

# Local variables

LOGGER = logging.getLogger(__name__)
RSA_KEY_SIZE = 2048


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


@pytest.fixture(scope="session")
def signing_key_path(tmp_path_factory: pytest.TempPathFactory) -> Path:

    """
    Generate a throwaway RSA private key once per session and write it to a temp PEM file.

    :param tmp_path_factory: Pytest session temp-path factory.
    :type tmp_path_factory: pytest.TempPathFactory
    :return: Path to the PEM file.
    :rtype: Path
    """

    key = RSAKey.generate_key(RSA_KEY_SIZE)
    path = tmp_path_factory.mktemp("keys") / "signing.pem"
    path.write_bytes(key.as_pem(private=True))
    return path
# endDef


@pytest.fixture
def settings(signing_key_path: Path) -> ZelleSettings:

    """
    Fully-populated fake-environment settings pointing at the fake EWS URLs.

    :param signing_key_path: The throwaway signing key PEM path.
    :type signing_key_path: Path
    :return: The settings model.
    :rtype: ZelleSettings
    """

    return ZelleSettings(
        environment="fake",
        api_base_url="http://fake-ews/zoms",
        token_url="http://fake-ews/token",
        token_aud="http://fake-ews",
        client_id="test-client-id",
        signing_kid="kid-1",
        signing_key_path=signing_key_path,
        org_id="BBO",
        participant_name="Bobs Bank of Omaha",
        submitted_name="Bob Barker",
        contact_name="Terry Technology",
        contact_phone="9999999977",
        contact_email="TTechnology@BBO.com",
    )
# endDef


@pytest.fixture
def database() -> AsyncMongoMockClient:

    """
    A fresh mongomock Motor database per test.

    :return: The mock database.
    :rtype: AsyncMongoMockClient
    """

    return AsyncMongoMockClient()["zelle_tests"]
# endDef


# end_tests/unit/zelle/conftest.py
