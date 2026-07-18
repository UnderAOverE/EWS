#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/config/zelle.py.                                                               #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : ZelleSettings — runtime configuration for the zelle facade: environment, EWS        #
#                 endpoints, token-broker inputs, org constants injected into every schedule          #
#                 payload, guardrail allowlists, timeouts, breaker/watchdog tuning, and the           #
#                 Mongo collection prefix. Values load from env vars prefixed ZELLE_.                 #
# Dependencies  : pydantic, pydantic-settings, apis.models.zelle.enums.                               #
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
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Internal imports

from src.apis.models.zelle.enums import HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class ZelleSettings(BaseSettings):

    """
    Runtime configuration for the zelle facade.

    Covers EWS environment selection, the ZOMS API and token endpoints, client-assertion inputs,
    the org constants enriched into every southbound schedule payload, consumer guardrail
    allowlists, southbound timeouts, circuit-breaker and watchdog tuning, and the Mongo
    collection prefix. Values load from environment variables with prefix ``ZELLE_`` (nested
    delimiter ``__``); the host application may also construct the model directly with keyword
    arguments.
    """

    model_config = SettingsConfigDict(env_prefix="ZELLE_", env_nested_delimiter="__")

    environment: Literal["fake", "cat", "prod"] = "fake"
    api_base_url: str
    token_url: str
    # Explicit config, never derived — the ZOMS auth server is unconfirmed (architecture §3).
    token_aud: str
    token_scope: str = "maintenance-event"
    client_id: SecretStr
    # Must match the kid of the JWKS entry registered with EWS.
    signing_kid: str
    signing_key_path: Path
    # Org constants injected into every schedule payload; lengths per docs/zoms-api-reference.md.
    org_id: Annotated[str, Field(min_length=3, max_length=3)]
    participant_name: Annotated[str, Field(min_length=1, max_length=50)]
    submitted_name: Annotated[str, Field(min_length=1, max_length=50)]
    contact_name: Annotated[str, Field(min_length=1, max_length=128)]
    contact_phone: Annotated[str, Field(min_length=9, max_length=12)]
    contact_email: Annotated[str, Field(min_length=1, max_length=255)]
    default_hold_mode: HoldMode = HoldMode.SELF_HOLD
    # Guardrails: empty client_allowlist = allow any (dev only); empty lifecycle allowlist
    # falls back to client_allowlist.
    client_allowlist: list[str] = []
    lifecycle_client_allowlist: list[str] = []
    # Timeouts / broker.
    token_connect_timeout_seconds: float = 3.0
    token_read_timeout_seconds: float = 7.0
    api_connect_timeout_seconds: float = 3.0
    api_read_timeout_seconds: float = 10.0
    breaker_failure_threshold: int = 5
    breaker_reset_seconds: float = 30.0
    # Watchdog.
    watchdog_enabled: bool = False
    watchdog_interval_seconds: float = 60.0
    watchdog_grace_seconds: float = 900.0
    # Mongo.
    mongo_collection_prefix: str = "zelle"
# endClass


# end_apis/config/zelle.py
