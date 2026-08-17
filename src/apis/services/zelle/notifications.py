#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/notifications.py.                                               #
# Date of birth : 2026-08-17.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : NotificationService — renders and sends the rich-HTML per-attempt notification      #
#                 email (action + outcome status, window, ticket, requester, and any contact-         #
#                 fallback note) through the host application's injected email sender. Sending is     #
#                 strictly best-effort: a mail failure is logged (recipient masked) and never         #
#                 propagates into the API call that triggered it.                                     #
# Dependencies  : apis.config.zelle, common.logger.                                                   #
# Modifications : 2026-08-17 Shane Reddy — Initial version.                                           #
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

from datetime import datetime, timezone
from html import escape
from typing import Protocol

# Internal imports

from src.apis.config.zelle import ZelleSettings
from src.common.logger import logger

# Local variables

SUBJECT_PREFIX = "[Zelle Maintenance]"
# Inline styles only: email clients strip <style> blocks; tables render everywhere.
ROW_LABEL_STYLE = (
    "padding:6px 12px;border:1px solid #d0d0d0;background:#f5f5f5;"
    "font-weight:bold;text-align:left;white-space:nowrap;"
)
ROW_VALUE_STYLE = "padding:6px 12px;border:1px solid #d0d0d0;text-align:left;"
NOTE_STYLE = (
    "margin:12px 0 0 0;padding:8px 12px;background:#fff3cd;border:1px solid #ffe08a;"
    "color:#664d03;"
)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EmailSender(Protocol):

    """
    Port for sending a rich-HTML email to a specific recipient. Satisfied structurally by the
    host application's EmailService; injected via ZelleService.get_service. The sender owns
    transport, from-address, and production gating.
    """

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        ) -> None:

        """
        Send one HTML email.

        :param to: The recipient address.
        :type to: str
        :param subject: The subject line.
        :type subject: str
        :param html_body: The HTML body.
        :type html_body: str
        :return: None.
        :rtype: None
        """

        ...
    # endDef
# endClass


def _mask_email(address: str) -> str:

    """
    Mask an email address for logging per the PII policy (``T***@BBO.com``).

    :param address: The address to mask.
    :type address: str
    :return: The masked form.
    :rtype: str
    """

    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    # endIf
    return f"{local[:1]}***@{domain}"
# endDef


class NotificationService:

    """
    Per-attempt email notifier. One send per audited attempt (schedule, start, complete,
    cancel, resolve — including failures and dry runs), stating the action and its outcome
    status. Best-effort by contract: :meth:`send_attempt` never raises.
    """

    def __init__(
        self,
        sender: EmailSender,
        settings: ZelleSettings,
        ) -> None:

        """
        Wire the notifier.

        :param sender: The host email egress (rich-HTML capable).
        :type sender: EmailSender
        :param settings: Zelle facade settings (the master notification switch).
        :type settings: ZelleSettings
        """

        self._sender = sender
        self._settings = settings
    # endDef

    async def send_attempt(
        self,
        *,
        action: str,
        status: str,
        recipient: str,
        event_id: str,
        ticket_number: str,
        window_start: datetime,
        window_end: datetime,
        requested_by: str | None,
        note: str | None,
        correlation_id: str,
        ) -> None:

        """
        Send the notification email for one attempt. Never raises — a mail failure is logged
        with the recipient masked and swallowed so the triggering API call is unaffected.

        :param action: The attempted action (schedule/start/complete/cancel/resolve).
        :type action: str
        :param status: The outcome to report (e.g. SCHEDULED, REJECTED, UNCERTAIN, DRY_RUN).
        :type status: str
        :param recipient: The destination email address.
        :type recipient: str
        :param event_id: The facade event id.
        :type event_id: str
        :param ticket_number: The change ticket bound to the event.
        :type ticket_number: str
        :param window_start: Scheduled window start (tz-aware).
        :type window_start: datetime
        :param window_end: Scheduled window end (tz-aware).
        :type window_end: datetime
        :param requested_by: The SSO username that drove the attempt, or None.
        :type requested_by: str | None
        :param note: A contact-fallback / context note to surface, or None.
        :type note: str | None
        :param correlation_id: Correlation id bound to the attempt.
        :type correlation_id: str
        :return: None.
        :rtype: None
        """

        if not self._settings.notification_emails_enabled:
            return
        # endIf
        subject = f"{SUBJECT_PREFIX} {action.upper()} {status} — ticket {ticket_number}"
        html_body = self._render(
            action=action,
            status=status,
            event_id=event_id,
            ticket_number=ticket_number,
            window_start=window_start,
            window_end=window_end,
            requested_by=requested_by,
            note=note,
            correlation_id=correlation_id,
        )
        try:
            await self._sender.send_email(recipient, subject, html_body)
            logger.info(
                "notification sent: action=%s status=%s to=%s event_id=%s",
                action,
                status,
                _mask_email(recipient),
                event_id,
            )
        except Exception as exc:  # Best-effort by contract: mail must never break the API call.
            logger.warning(
                "notification send FAILED (ignored): action=%s status=%s to=%s event_id=%s (%s)",
                action,
                status,
                _mask_email(recipient),
                event_id,
                type(exc).__name__,
            )
        # endTryExcept
    # endDef

    def _render(
        self,
        *,
        action: str,
        status: str,
        event_id: str,
        ticket_number: str,
        window_start: datetime,
        window_end: datetime,
        requested_by: str | None,
        note: str | None,
        correlation_id: str,
        ) -> str:

        """
        Render the HTML body: a header line, a bordered detail table, and an optional
        highlighted note. All values are HTML-escaped.

        :param action: The attempted action.
        :type action: str
        :param status: The outcome status.
        :type status: str
        :param event_id: The facade event id.
        :type event_id: str
        :param ticket_number: The change ticket.
        :type ticket_number: str
        :param window_start: Scheduled window start.
        :type window_start: datetime
        :param window_end: Scheduled window end.
        :type window_end: datetime
        :param requested_by: The SSO username, or None.
        :type requested_by: str | None
        :param note: The optional fallback/context note.
        :type note: str | None
        :param correlation_id: The attempt's correlation id.
        :type correlation_id: str
        :return: The HTML body.
        :rtype: str
        """

        rows = [
            ("Action", action.upper()),
            ("Status", status),
            ("Event ID", event_id),
            ("Ticket", ticket_number),
            ("Window start (UTC)", window_start.astimezone(timezone.utc).isoformat()),
            ("Window end (UTC)", window_end.astimezone(timezone.utc).isoformat()),
            ("Requested by", requested_by or "(not provided)"),
            ("Correlation ID", correlation_id),
        ]
        row_html = "".join(
            f'<tr><th style="{ROW_LABEL_STYLE}">{escape(label)}</th>'
            f'<td style="{ROW_VALUE_STYLE}">{escape(value)}</td></tr>'
            for label, value in rows
        )
        note_html = (
            f'<p style="{NOTE_STYLE}">&#9888; {escape(note)}</p>' if note is not None else ""
        )
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1a1a1a;">'
            f"<h2 style=\"margin:0 0 4px 0;\">Zelle maintenance {escape(action.lower())}: "
            f"{escape(status)}</h2>"
            '<p style="margin:0 0 12px 0;color:#555555;">'
            "Automated notification from the Zelle maintenance facade.</p>"
            f'<table style="border-collapse:collapse;">{row_html}</table>'
            f"{note_html}"
            "</div>"
        )
    # endDef
# endClass


# end_apis/services/zelle/notifications.py
