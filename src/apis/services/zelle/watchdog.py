#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/watchdog.py.                                                    #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Watchdog — background alerter for stuck maintenance events: IN_PROGRESS past        #
#                 scheduled_end + grace (a window EWS may still be holding messages for) and          #
#                 orphaned SCHEDULED events past scheduled_start + grace. Singleton across            #
#                 replicas via a Mongo lease; alerting is CRITICAL log lines the host picks up.       #
# Dependencies  : apis.config.zelle, apis.repositories.zelle.events,                                  #
#                 apis.repositories.zelle.leases.                                                     #
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

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.apis.models.zelle.records import EventRecord
from src.apis.repositories.zelle.events import EventsRepository
from src.apis.repositories.zelle.leases import LeaseRepository

# Local variables

LOGGER = logging.getLogger(__name__)
LEASE_NAME = "zelle-watchdog"
# Lease TTL is a multiple of the scan interval so a healthy holder never loses its lease.
LEASE_TTL_INTERVALS = 2.0


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class Alerter(Protocol):

    """
    Port for sending operational alerts out of the watchdog. Implemented by an edge adapter
    (e.g. :class:`apis.services.zelle.alerter.SmtpAlerter`); the watchdog depends only on this
    Protocol, so the domain never imports the SMTP client directly.
    """

    async def send(
        self,
        subject: str,
        body: str,
        ) -> None:

        """
        Send an alert.

        :param subject: The alert subject line.
        :type subject: str
        :param body: The alert body.
        :type body: str
        :return: None.
        :rtype: None
        """

        ...
    # endDef
# endClass


class Watchdog:

    """
    Background stuck-event alerter. One instance per process; only the instance holding the
    Mongo lease scans, so an accidental scale-out produces idle replicas, not duplicate pagers.
    Alerting is ``LOGGER.critical`` per stuck event — host monitoring pages on CRITICAL lines.
    """

    def __init__(
        self,
        settings: ZelleSettings,
        events: EventsRepository,
        leases: LeaseRepository,
        alerter: Alerter | None = None,
        ) -> None:

        """
        Wire the watchdog.

        :param settings: Zelle facade settings (interval, grace).
        :type settings: ZelleSettings
        :param events: Event repository providing the stuck-event query.
        :type events: EventsRepository
        :param leases: Lease repository backing the cross-replica singleton.
        :type leases: LeaseRepository
        :param alerter: Optional alert egress; when set, stuck events are emailed in addition to
            the CRITICAL log line. None disables email alerting.
        :type alerter: Alerter | None
        """

        self._settings = settings
        self._events = events
        self._leases = leases
        self._alerter = alerter
        self._holder = str(uuid.uuid4())
        self._stopped = asyncio.Event()
    # endDef

    async def run_forever(self) -> None:

        """
        Loop until :meth:`stop`: acquire/renew the lease, scan when holding it, then sleep the
        configured interval. Scan errors are logged and swallowed — the watchdog must outlive
        transient Mongo failures; it is the thing that pages, so it never dies quietly.

        :return: None.
        :rtype: None
        """

        interval = self._settings.watchdog_interval_seconds
        ttl = interval * LEASE_TTL_INTERVALS
        while not self._stopped.is_set():
            try:
                holding = await self._leases.acquire(LEASE_NAME, self._holder, ttl)
                if holding:
                    await self.scan_once()
                else:
                    LOGGER.debug("watchdog lease held elsewhere; idling")
                # endIfElse
            except Exception:
                # Broad by design: a scan/lease failure must not kill the pager loop.
                LOGGER.exception("watchdog iteration failed; will retry next interval")
            # endTryExcept
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except TimeoutError:
                # Interval elapsed without a stop signal — loop for the next scan.
                pass
            # endTryExcept
        # endWhile
        await self._leases.release(LEASE_NAME, self._holder)
    # endDef

    async def scan_once(self) -> list[EventRecord]:

        """
        Run one stuck-event scan and page (CRITICAL log line) per stuck event.

        :return: The stuck events found.
        :rtype: list[EventRecord]
        """

        now = datetime.now(timezone.utc)
        stuck = await self._events.find_stuck(now, self._settings.watchdog_grace_seconds)
        for record in stuck:
            LOGGER.critical(
                "stuck maintenance event: id=%s status=%s ticket=%s window=%s..%s — "
                "manual intervention required (see RUNBOOK escalation path)",
                record.event_id,
                record.status.value,
                record.ticket_number,
                record.scheduled_start.isoformat(),
                record.scheduled_end.isoformat(),
            )
        # endFor
        if stuck and self._alerter is not None:
            await self._send_alert(stuck)
        # endIf
        return stuck
    # endDef

    async def _send_alert(self, records: list[EventRecord]) -> None:

        """
        Best-effort email alert summarising the stuck events. A send failure is logged and
        swallowed: the CRITICAL log lines already emitted are the guaranteed alert of record, and
        a flaky relay must never kill the pager loop.

        :param records: The stuck event records to summarise.
        :type records: list[EventRecord]
        :return: None.
        :rtype: None
        """

        alerter = self._alerter
        if alerter is None:
            return
        # endIf
        subject = f"[Zelle facade] {len(records)} stuck maintenance event(s) need intervention"
        lines = [
            f"- id={record.event_id} status={record.status.value} "
            f"ticket={record.ticket_number} "
            f"window={record.scheduled_start.isoformat()}..{record.scheduled_end.isoformat()}"
            for record in records
        ]
        body = (
            "The Zelle maintenance watchdog found events stuck past their grace period.\n"
            "Manual intervention is required (see the runbook escalation path).\n\n"
            + "\n".join(lines)
        )
        try:
            await alerter.send(subject, body)
        except Exception:
            # Broad by design: an alert-send failure must not kill the pager loop; the CRITICAL
            # log lines already emitted remain the alert of record.
            LOGGER.exception("watchdog email alert failed; CRITICAL logs remain the alert path")
        # endTryExcept
    # endDef

    def stop(self) -> None:

        """
        Signal :meth:`run_forever` to exit after the current iteration.

        :return: None.
        :rtype: None
        """

        self._stopped.set()
    # endDef
# endClass


# end_apis/services/zelle/watchdog.py
