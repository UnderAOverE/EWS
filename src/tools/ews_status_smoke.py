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
    logger.info(
        "smoke: is_production=%s api_base_url=%s token_url=%s kid=%s mtls=%s",
        settings.is_production,
        settings.api_base_url,
        settings.token_url,
        settings.signing_kid,
        settings.client_certificate_path is not None,
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
        help="The EWS maintenanceEventId to look up (from EWS, not a facade eventId).",
    )
    arguments = parser.parse_args()
    # A standalone process has no host logging config, so the shared logger would swallow the
    # INFO-level verdict — give it a console handler here (host processes never run this).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run_smoke(arguments.maintenance_event_id))
# endDef


if __name__ == "__main__":
    raise SystemExit(main())
# endIf


# end_tools/ews_status_smoke.py
