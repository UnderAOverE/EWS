#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : tests/unit/zelle/test_watchdog.py.                                                  #
# Date of birth : 2026-07-26.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Watchdog scan tests: a stuck IN_PROGRESS event is detected and, when an             #
#                 AlertSender (the host EmailService) is injected, emailed with the configured        #
#                 only_production flag; with no alerter the scan still returns the stuck event.       #
# Dependencies  : pytest, apis.models.zelle.records, apis.repositories.zelle.*,                        #
#                 apis.services.zelle.watchdog.                                                       #
# Modifications : 2026-07-26 Shane Reddy — Initial version.                                           #
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
from datetime import datetime, timedelta, timezone

from mongomock_motor import AsyncMongoMockClient

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.enums import EventStatus, HoldMode
from src.apis.models.zelle.records import EventRecord
from src.apis.repositories.zelle.events import get_events_repository
from src.apis.repositories.zelle.leases import get_leases_repository
from src.apis.services.zelle.watchdog import Watchdog

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class _RecordingAlerter:

    """
    A test double satisfying the AlertSender port; records each send_alert call for assertions.
    """

    def __init__(self) -> None:

        """
        Initialise the call log.

        :return: None.
        :rtype: None
        """

        self.calls: list[tuple[str, str, bool]] = []
    # endDef

    async def send_alert(
        self,
        subject: str,
        body: str,
        only_production: bool = True,
        ) -> None:

        """
        Record an alert send.

        :param subject: The alert subject.
        :type subject: str
        :param body: The alert body.
        :type body: str
        :param only_production: The production-gating flag forwarded by the watchdog.
        :type only_production: bool
        :return: None.
        :rtype: None
        """

        self.calls.append((subject, body, only_production))
    # endDef
# endClass


def _stuck_in_progress_record(now: datetime) -> EventRecord:

    """
    Build an IN_PROGRESS event whose window ended well before ``now`` (stuck past grace).

    :param now: The reference current time (tz-aware UTC).
    :type now: datetime
    :return: The event record.
    :rtype: EventRecord
    """

    return EventRecord(
        event_id="evt-stuck-1",
        ews_event_id="ews-1",
        status=EventStatus.IN_PROGRESS,
        idempotency_id="idem-1",
        client_id="ops-portal",
        ticket_number="CHG0012345",
        reason="Quarterly failover drill",
        hold_mode=HoldMode.SELF_HOLD,
        scheduled_start=now - timedelta(hours=3),
        scheduled_end=now - timedelta(hours=2),
        payload_snapshot={},
        last_confirmed_upstream_at=None,
        created_at=now - timedelta(hours=4),
        updated_at=now - timedelta(hours=2),
    )
# endDef


async def test_scan_once_emails_stuck_event(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> None:

    """
    A stuck IN_PROGRESS event is returned by the scan and emailed via the injected alerter, with
    only_production forwarded from settings.

    :param settings: The fake-environment settings fixture.
    :type settings: ZelleSettings
    :param mongo_client: The mongomock client fixture.
    :type mongo_client: AsyncMongoMockClient
    :return: None.
    :rtype: None
    """

    events = await get_events_repository(mongo_client)
    leases = await get_leases_repository(mongo_client)
    now = datetime.now(timezone.utc)
    await events.create(_stuck_in_progress_record(now))
    alerter = _RecordingAlerter()
    watchdog = Watchdog(settings, events, leases, alerter)

    stuck = await watchdog.scan_once()

    assert [record.event_id for record in stuck] == ["evt-stuck-1"]
    assert len(alerter.calls) == 1
    subject, body, only_production = alerter.calls[0]
    assert "CHG0012345" in body
    assert only_production is settings.alert_only_in_production
# endDef


async def test_scan_once_without_alerter_still_detects(
    settings: ZelleSettings,
    mongo_client: AsyncMongoMockClient,
    ) -> None:

    """
    With no alerter injected, the scan still returns the stuck event (log-only path) and does not
    raise.

    :param settings: The fake-environment settings fixture.
    :type settings: ZelleSettings
    :param mongo_client: The mongomock client fixture.
    :type mongo_client: AsyncMongoMockClient
    :return: None.
    :rtype: None
    """

    events = await get_events_repository(mongo_client)
    leases = await get_leases_repository(mongo_client)
    now = datetime.now(timezone.utc)
    await events.create(_stuck_in_progress_record(now))
    watchdog = Watchdog(settings, events, leases, None)

    stuck = await watchdog.scan_once()

    assert [record.event_id for record in stuck] == ["evt-stuck-1"]
# endDef


# end_tests/unit/zelle/test_watchdog.py
