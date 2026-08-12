#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tools/ews_status_smoke.py.                                                          #
# Date of birth : 2026-08-06.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Standalone EWS connectivity smoke test — signs a client assertion, exchanges it     #
#                 for a token, and calls the ZOMS status read for a raw maintenanceEventId. No        #
#                 Mongo, no routes, no local event record: purely the southbound stack, to prove      #
#                 DNS/TLS/mTLS/auth/routing end to end. Run: python -m src.tools.ews_status_smoke.    #
# Dependencies  : apis.config.zelle, apis.models.zelle.errors, apis.services.zelle.service,           #
#                 apis.services.zelle.token_broker, apis.services.zelle.zoms_client.                  #
# Modifications : 2026-08-06 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
EWS connectivity smoke test.

Usage (env configured exactly like the facade — the ``ZELLE_*`` variables)::

    python -m src.tools.ews_status_smoke <maintenanceEventId>
    python -m src.tools.ews_status_smoke --list
    python -m src.tools.ews_status_smoke --dump-assertion

``--list`` calls the org-scoped list read (``GET /v1/events?orgId=...``) and prints every
maintenance event EWS holds for the configured org — id, status, and scheduled window only,
never the contact PII. This is the recovery path for an event stuck in PENDING_UPSTREAM_ID:
find its EWS id here, then resolve it through the admin endpoint.

``--dump-assertion`` prints one freshly signed client assertion plus its decoded header and
claims — the artifact EWS support asks for when they offer to "decode your assertion." It is
safe to share with EWS: it contains no key material and expires within minutes. Without a
maintenanceEventId the tool exits after dumping; with one it dumps and then runs the smoke.

Exit codes:

- ``0`` — EWS answered 2xx: token, TLS/mTLS, routing, and the event id all good.
- ``2`` — EWS answered a definite 4xx: **connectivity and auth are proven**; only the
  maintenanceEventId (or the §3.5 path form) is in question.
- ``1`` — the smoke failed before a definite EWS answer: unreachable, rate limited, or
  credentials rejected. The log line says which.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

import argparse
import asyncio
import base64
import json
import logging

# Internal imports

from src.apis.config.zelle import get_zelle_settings
from src.apis.models.zelle.errors import (
    AuthConfigError,
    RateLimitedError,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from src.apis.services.zelle.service import _build_http_client
from src.apis.services.zelle.token_broker import TokenBroker
from src.apis.services.zelle.zoms_client import ZomsClient
from src.common.logger import logger

# Local variables

EXIT_SUCCESS = 0
EXIT_NOT_PROVEN = 1
EXIT_REACHED_BUT_REJECTED = 2


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


async def run_smoke(maintenance_event_id: str) -> int:

    """
    Execute one status read against EWS with the full southbound stack and log the verdict.

    :param maintenance_event_id: The raw EWS maintenance event id to look up.
    :type maintenance_event_id: str
    :return: The process exit code (see the module docstring).
    :rtype: int
    """

    settings = get_zelle_settings()
    # The full non-secret request context, so the log line is shareable with EWS support as-is.
    # client_id stays out (SecretStr) — it is visible in the --dump-assertion claims instead.
    logger.info(
        "smoke: is_production=%s api_base_url=%s token_url=%s token_aud=%s scope=%s kid=%s "
        "mtls=%s proxy_configured=%s",
        settings.is_production,
        settings.api_base_url,
        settings.token_url,
        settings.token_aud,
        settings.token_scope,
        settings.signing_kid,
        settings.client_certificate_path is not None,
        settings.proxy_url is not None,
    )
    # Reuse the exact production TLS/mTLS client construction — the point of the smoke test is
    # to exercise the same path the facade will use, not an approximation of it.
    client = _build_http_client(settings)
    try:
        broker = TokenBroker(settings, client)
        zoms = ZomsClient(settings, client, broker)
        parsed, request_ids = await zoms.get_status(maintenance_event_id)
        logger.info(
            "smoke: SUCCESS — EWS answered 2xx: maintenanceEventId=%s status=%s attempts=%d",
            parsed.maintenance_event_id,
            parsed.status,
            len(request_ids),
        )
        return EXIT_SUCCESS
    except UpstreamRejectedError as exc:
        logger.warning(
            "smoke: CONNECTIVITY PROVEN, id rejected — %s Token, TLS, and routing all worked; "
            "EWS answered and said no. Check the maintenanceEventId, and if every id is "
            "rejected, confirm the §3.5 path form with EWS (slash vs dot).",
            exc.message,
        )
        return EXIT_REACHED_BUT_REJECTED
    except AuthConfigError as exc:
        logger.error(
            "smoke: AUTH FAILURE — %s The network path works but EWS rejected our credentials; "
            "check client registration, the signing key, and the registered kid.",
            exc.message,
        )
        return EXIT_NOT_PROVEN
    except RateLimitedError as exc:
        logger.error("smoke: RATE LIMITED — %s Retry shortly.", exc.message)
        return EXIT_NOT_PROVEN
    except UpstreamUnavailableError as exc:
        logger.error(
            "smoke: UNREACHABLE — %s Check DNS, firewall/proxy egress, the base/token URLs, "
            "and the mTLS material paths.",
            exc.message,
        )
        return EXIT_NOT_PROVEN
    finally:
        await client.aclose()
    # endTryExceptFinally
# endDef


async def run_list() -> int:

    """
    List every maintenance event EWS holds for the configured org and log id, status, and
    scheduled window — never the contact PII the entries also carry.

    :return: The process exit code (see the module docstring).
    :rtype: int
    """

    settings = get_zelle_settings()
    client = _build_http_client(settings)
    try:
        broker = TokenBroker(settings, client)
        zoms = ZomsClient(settings, client, broker)
        entries, request_ids = await zoms.list_events()
        logger.info(
            "smoke: LIST SUCCESS — EWS holds %d event(s) for org %s (attempts=%d)",
            len(entries),
            settings.org_id,
            len(request_ids),
        )
        for entry in entries:
            extra = entry.model_extra or {}
            logger.info(
                "upstream event: maintenanceEventId=%s status=%s start=%s end=%s",
                entry.maintenance_event_id,
                entry.status,
                extra.get("scheduledStartDate"),
                extra.get("scheduledEndDate"),
            )
        # endFor
        return EXIT_SUCCESS
    except UpstreamRejectedError as exc:
        logger.warning("smoke: LIST REJECTED — %s", exc.message)
        return EXIT_REACHED_BUT_REJECTED
    except AuthConfigError as exc:
        logger.error(
            "smoke: AUTH FAILURE — %s The network path works but EWS rejected our credentials; "
            "check client registration, the signing key, and the registered kid.",
            exc.message,
        )
        return EXIT_NOT_PROVEN
    except RateLimitedError as exc:
        logger.error("smoke: RATE LIMITED — %s Retry shortly.", exc.message)
        return EXIT_NOT_PROVEN
    except UpstreamUnavailableError as exc:
        logger.error(
            "smoke: UNREACHABLE — %s Check DNS, firewall/proxy egress, the base/token URLs, "
            "and the mTLS material paths.",
            exc.message,
        )
        return EXIT_NOT_PROVEN
    finally:
        await client.aclose()
    # endTryExceptFinally
# endDef


def _b64url_decode(segment: str) -> bytes:

    """
    Decode one base64url JWT segment, restoring stripped padding.

    :param segment: The base64url-encoded segment.
    :type segment: str
    :return: The decoded bytes.
    :rtype: bytes
    """

    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
# endDef


async def dump_assertion() -> int:

    """
    Sign one fresh client assertion and log it with its decoded header and claims — the
    artifact EWS support decodes to say which claim they reject. No network call is made.

    :return: The process exit code (always success).
    :rtype: int
    """

    settings = get_zelle_settings()
    client = _build_http_client(settings)
    try:
        broker = TokenBroker(settings, client)
        # Private-method access is deliberate: the broker owns assertion signing, and this
        # diagnostic must emit EXACTLY what the broker would send, not a reimplementation.
        assertion = broker._build_assertion()
    finally:
        await client.aclose()
    # endTryFinally
    header, claims, _signature = assertion.split(".")
    logger.info(
        "client assertion (safe to share with EWS: no key material, expires in minutes):",
    )
    logger.info("%s", assertion)
    logger.info("decoded header: %s", json.dumps(json.loads(_b64url_decode(header))))
    logger.info("decoded claims: %s", json.dumps(json.loads(_b64url_decode(claims))))
    return EXIT_SUCCESS
# endDef


def main() -> int:

    """
    Parse the command line and run the smoke test.

    :return: The process exit code (see the module docstring).
    :rtype: int
    """

    parser = argparse.ArgumentParser(
        prog="python -m src.tools.ews_status_smoke",
        description=(
            "EWS connectivity smoke test: call the ZOMS status read for a raw "
            "maintenanceEventId using the facade's southbound stack (no Mongo, no routes)."
        ),
    )
    parser.add_argument(
        "maintenance_event_id",
        nargs="?",
        default=None,
        help="The EWS maintenanceEventId to look up (from EWS, not a facade eventId).",
    )
    parser.add_argument(
        "--dump-assertion",
        action="store_true",
        help="Sign and print one client assertion with decoded claims (to share with EWS "
        "support); exits after dumping unless a maintenanceEventId is also given.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_events",
        help="List every maintenance event EWS holds for the configured org "
        "(id, status, window — no PII) and exit.",
    )
    arguments = parser.parse_args()
    if arguments.maintenance_event_id is None and not arguments.dump_assertion \
            and not arguments.list_events:
        parser.error("provide a maintenanceEventId, --list, --dump-assertion, or a combination")
    # endIf
    # A standalone process has no host logging config, so the shared logger would swallow the
    # INFO-level verdict — give it a console handler here (host processes never run this).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if arguments.dump_assertion:
        dump_code = asyncio.run(dump_assertion())
        if arguments.maintenance_event_id is None and not arguments.list_events:
            return dump_code
        # endIf
    # endIf
    if arguments.list_events:
        list_code = asyncio.run(run_list())
        if arguments.maintenance_event_id is None:
            return list_code
        # endIf
    # endIf
    return asyncio.run(run_smoke(arguments.maintenance_event_id))
# endDef


if __name__ == "__main__":
    raise SystemExit(main())
# endIf


# end_tools/ews_status_smoke.py
