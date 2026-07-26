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
# Explanation   : ZelleSettings — runtime configuration for the zelle facade: the is_production       #
#                 flag (drives CAT/PROD URL selection), EWS endpoints, token-broker inputs, org        #
#                 constants injected into every schedule                                               #
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
from typing import Annotated, Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Internal imports

from src.apis.models.zelle.enums import HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)
# Built-in ZOMS endpoints (docs/zoms-api-reference.md), selected by the ``is_production`` flag —
# the host passes its IS_PRODUCTION_ENVIRONMENT — so non-prod resolves to CAT and prod to PROD
# without hand-set URLs; an explicit value always overrides (that is how a local/fake deployment
# points at its stub). NOTE: the token URLs are flagged unconfirmed in the vendor reference
# (sourced from Paze docs) — override token_url once EWS confirms them. token_aud stays explicit.
CAT_API_BASE_URL = "https://api.zelle.cat.earlywarning.io/zoms"
PROD_API_BASE_URL = "https://api.zelle.earlywarning.com/zoms"
CAT_TOKEN_URL = "https://auth.wallet.cat.earlywarning.io/token"
PROD_TOKEN_URL = "https://auth.wallet.earlywarning.com/token"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class ZelleSettings(BaseSettings):

    """
    Runtime configuration for the zelle facade.

    Covers the production flag (drives CAT/PROD ZOMS URL selection), the ZOMS API and token
    endpoints, client-assertion inputs, the org constants enriched into every southbound schedule
    payload, consumer guardrail allowlists, southbound timeouts, circuit-breaker and watchdog
    tuning, and the Mongo collection prefix. Values load from environment variables with prefix
    ``ZELLE_`` (nested delimiter ``__``); the host application may also construct the model
    directly with keyword arguments.
    """

    model_config = SettingsConfigDict(env_prefix="ZELLE_", env_nested_delimiter="__")

    # The host passes its IS_PRODUCTION_ENVIRONMENT here; it drives the ZOMS base URL and token URL
    # (prod -> PROD, otherwise CAT) unless api_base_url / token_url are set explicitly.
    is_production: bool = False
    api_base_url: str
    token_url: str
    # Audience claim; explicit/required and never derived — the ZOMS auth server audience is
    # unconfirmed (docs/zoms-api-reference.md).
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
    # Email alerting: the watchdog reuses the host application's injected EmailService (see
    # register_zelle), so there is no SMTP config here. This flag is forwarded as
    # EmailService.send_alert(only_production=...); True keeps stuck-event emails production-only,
    # matching the host convention.
    alert_only_in_production: bool = True
    # Mongo.
    mongo_collection_prefix: str = "zelle"

    @model_validator(mode="before")
    @classmethod
    def _default_endpoints(cls, data: Any) -> Any:

        """
        Fill ``api_base_url`` and ``token_url`` from ``is_production`` when the caller did not set
        them explicitly: production resolves to PROD, everything else to CAT. An explicit value
        (env var or kwarg) always wins — that is how a local/fake deployment points at its stub.

        :param data: The raw input mapping before field validation.
        :type data: Any
        :return: The possibly-augmented input mapping.
        :rtype: Any
        """

        if isinstance(data, dict):
            is_production = data.get("is_production", False)
            if isinstance(is_production, str):
                is_production = is_production.strip().lower() in {"1", "true", "yes", "on"}
            # endIf
            if not data.get("api_base_url"):
                data["api_base_url"] = PROD_API_BASE_URL if is_production else CAT_API_BASE_URL
            # endIf
            if not data.get("token_url"):
                data["token_url"] = PROD_TOKEN_URL if is_production else CAT_TOKEN_URL
            # endIf
        # endIf
        return data
    # endDef
# endClass


# end_apis/config/zelle.py
