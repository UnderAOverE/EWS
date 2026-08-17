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

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Internal imports

from src.apis.models.zelle.enums import HoldMode

# Local variables

LOGGER = logging.getLogger(__name__)
# Built-in ZOMS endpoints (docs/zoms-api-reference.md), selected by the ``is_production`` flag —
# the host passes its IS_PRODUCTION_ENVIRONMENT — so non-prod resolves to CAT and prod to PROD
# without hand-set URLs; an explicit value always overrides (that is how a local/fake deployment
# points at its stub). Token endpoints + audiences are the EWS-confirmed values (2026-08-11,
# EWS "Obtaining RESTful Service Authorizations" / Platform OAuth guide v2.0 — vendor doc §4);
# the audience is a fixed URL-shaped string that is deliberately NOT the token endpoint. An EWS
# support email quoted slightly different CAT values — the doc page wins here; vendor doc §4
# records the discrepancy, and env overrides win if EWS reconciles differently.
CAT_API_BASE_URL = "https://api.zelle.cat.earlywarning.io/zoms"
PROD_API_BASE_URL = "https://api.zelle.earlywarning.com/zoms"
CAT_TOKEN_URL = "https://auth.zelle.cat.earlywarning.io/token"
PROD_TOKEN_URL = "https://auth.zelle.earlywarning.com/token"
CAT_TOKEN_AUD = "https://auth-zelle.cat.earlywarning.io/oauth2/access/v1/token"
PROD_TOKEN_AUD = "https://auth-zelle.earlywarning.com/oauth2/access/v1/token"
# JWKS files carrying the public key + kid registered with EWS, selected by is_production the same
# way the endpoints are (CAT reads uat_zell.jwks, PROD reads zelle.jwks). They live in the host
# app's common folder (mirrored locally at src/common); the file's kid fills signing_kid when
# ZELLE_SIGNING_KID is not set explicitly. Public material only — never the private key.
CAT_JWKS_FILENAME = "uat_zell.jwks"
PROD_JWKS_FILENAME = "zelle.jwks"
JWKS_DIR = Path(__file__).resolve().parents[2] / "common"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


def _coerce_production_flag(value: Any) -> bool:

    """
    Normalize the raw ``is_production`` input to a bool — env vars arrive as strings, kwargs as
    bools, and both validators below need the answer before pydantic's own coercion runs.

    :param value: The raw flag value from the pre-validation input mapping.
    :type value: Any
    :return: True when the value reads as production.
    :rtype: bool
    """

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    # endIf
    return bool(value)
# endDef


def _read_signing_kid(jwks_path: Path) -> str:

    """
    Extract the signing key's ``kid`` from a JWKS file — either a bare JWK object or a
    ``{"keys": [...]}`` keyset. The first entry usable for signing wins (``use`` equal to
    ``sig``, or no ``use`` member at all).

    :param jwks_path: Path to the JWKS file registered with EWS.
    :type jwks_path: Path
    :return: The key id to place in the client-assertion JWT header.
    :rtype: str
    :raises ValueError: If the file is missing, unparsable, or holds no signing key with a kid.
    """

    if not jwks_path.is_file():
        raise ValueError(
            f"signing_kid is not set and no JWKS file exists at {jwks_path} — set "
            "ZELLE_SIGNING_KID explicitly or place the registered JWKS file there.",
        )
    # endIf
    try:
        document = json.loads(jwks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"JWKS file {jwks_path} is unreadable or not valid JSON: {error}",
        ) from error
    # endTryExcept
    keys = document.get("keys", [document]) if isinstance(document, dict) else []
    for key in keys:
        if not isinstance(key, dict) or key.get("use", "sig") != "sig":
            continue
        # endIf
        kid = key.get("kid")
        if isinstance(kid, str) and kid:
            # kid is loggable token metadata (never the key material itself).
            LOGGER.info("signing_kid loaded from JWKS file %s: kid=%s", jwks_path, kid)
            return kid
        # endIf
    # endFor
    raise ValueError(f"JWKS file {jwks_path} contains no signing key with a kid.")
# endDef


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
    # Audience claim for the client assertion — a fixed URL-shaped string that is NOT the token
    # endpoint (vendor doc §4). Derived from is_production unless set explicitly.
    token_aud: str
    token_scope: str = "maintenance-event"
    client_id: SecretStr
    # Must match the kid of the JWKS entry registered with EWS.
    signing_kid: str
    signing_key_path: Path
    # Southbound TLS for the zelle-owned httpx client. verify_ssl=False disables verification
    # (non-prod only). ca_certificate_path is a private CA bundle to trust EWS. client_certificate_
    # path + client_key_path enable mTLS (the EWS client keypair — crown jewels, mounted read-only);
    # both must be set together (enforced below).
    verify_ssl: bool = True
    ca_certificate_path: Path | None = None
    client_certificate_path: Path | None = None
    client_key_path: Path | None = None
    # Corporate egress forward proxy for ALL southbound zelle traffic (token + ZOMS), e.g.
    # ``http://proxy.bank.local:8080``. SecretStr because bank proxy URLs may embed credentials
    # (``http://user:pass@proxy:8080``) — never logged; unwrapped only where the client is built.
    # None keeps httpx's default behavior, which still honors ambient HTTPS_PROXY/NO_PROXY env
    # vars — the explicit setting exists to pin the proxy for zelle without touching the host's.
    proxy_url: SecretStr | None = None
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
    # Employee directory (GlobalDirectory) enrichment: when a base URL is set and the consumer
    # sends Sm-User, the schedule contact block and the notification recipient come from the
    # directory (GET {base}/{username}); any failure falls back to the configured defaults and
    # is noted in the audit trail and the notification email. None disables enrichment.
    employee_api_base_url: str | None = None
    employee_api_connect_timeout_seconds: float = 2.0
    employee_api_read_timeout_seconds: float = 4.0
    employee_cache_ttl_seconds: float = 600.0
    # Minimum whole days between "now" and a requested startTime (facade guardrail, checked at
    # schedule time; EWS applies its own lead-time tiers independently). 0 disables the rule —
    # set ZELLE_MIN_SCHEDULE_LEAD_DAYS per environment.
    min_schedule_lead_days: int = 0
    # Master switch for the per-attempt rich-HTML notification emails.
    notification_emails_enabled: bool = True
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
    # ZelleService.get_service), so there is no SMTP config here. This flag is forwarded as
    # EmailService.send_alert(only_production=...); True keeps stuck-event emails production-only,
    # matching the host convention.
    alert_only_in_production: bool = True

    @model_validator(mode="before")
    @classmethod
    def _default_endpoints(cls, data: Any) -> Any:

        """
        Fill ``api_base_url``, ``token_url``, and ``token_aud`` from ``is_production`` when the
        caller did not set them explicitly: production resolves to PROD, everything else to CAT.
        An explicit value (env var or kwarg) always wins — that is how a local/fake deployment
        points at its stub.

        :param data: The raw input mapping before field validation.
        :type data: Any
        :return: The possibly-augmented input mapping.
        :rtype: Any
        """

        if isinstance(data, dict):
            is_production = _coerce_production_flag(data.get("is_production", False))
            if not data.get("api_base_url"):
                data["api_base_url"] = PROD_API_BASE_URL if is_production else CAT_API_BASE_URL
            # endIf
            if not data.get("token_url"):
                data["token_url"] = PROD_TOKEN_URL if is_production else CAT_TOKEN_URL
            # endIf
            if not data.get("token_aud"):
                data["token_aud"] = PROD_TOKEN_AUD if is_production else CAT_TOKEN_AUD
            # endIf
        # endIf
        return data
    # endDef

    @model_validator(mode="before")
    @classmethod
    def _default_signing_kid(cls, data: Any) -> Any:

        """
        Fill ``signing_kid`` from the environment's registered JWKS file when the caller did not
        set it explicitly: production reads ``PROD_JWKS_FILENAME``, everything else
        ``CAT_JWKS_FILENAME``, both under ``JWKS_DIR``. An explicit value (env var or kwarg)
        always wins, so tests and stub deployments are untouched.

        :param data: The raw input mapping before field validation.
        :type data: Any
        :return: The possibly-augmented input mapping.
        :rtype: Any
        :raises ValueError: If no explicit kid is given and the JWKS file is missing or invalid.
        """

        if isinstance(data, dict) and not data.get("signing_kid"):
            is_production = _coerce_production_flag(data.get("is_production", False))
            filename = PROD_JWKS_FILENAME if is_production else CAT_JWKS_FILENAME
            data["signing_kid"] = _read_signing_kid(JWKS_DIR / filename)
        # endIf
        return data
    # endDef

    @model_validator(mode="after")
    def _validate_client_cert_pair(self) -> Self:

        """
        Enforce that the mTLS client certificate and key are configured together — a half-set pair
        would silently skip mTLS, which on a payments integration must fail loud, not quiet.

        :return: The validated settings instance.
        :rtype: Self
        :raises ValueError: If exactly one of the client cert / key paths is set.
        """

        if bool(self.client_certificate_path) != bool(self.client_key_path):
            raise ValueError(
                "client_certificate_path and client_key_path must be set together for mTLS.",
            )
        # endIf
        return self
    # endDef
# endClass


@lru_cache(maxsize=1)
def get_zelle_settings() -> ZelleSettings:

    """
    Module-level accessor for the zelle settings, built once from the environment — the way the
    host's ``environment_settings`` is a single module-level config object. ZelleService.get_service
    reads this when no settings are injected. ``is_production`` then comes from ``ZELLE_IS_PRODUCTION``
    (set it from the host's IS_PRODUCTION_ENVIRONMENT); inject settings explicitly to pass the flag
    directly instead.

    :return: The process-wide zelle settings.
    :rtype: ZelleSettings
    """

    # BaseSettings populates the required fields from the environment at runtime; mypy cannot see
    # that, so the call-arg check is suppressed at this single sanctioned construction site.
    return ZelleSettings()  # type: ignore[call-arg]
# endDef


# end_apis/config/zelle.py
