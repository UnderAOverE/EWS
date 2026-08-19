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
# Explanation   : NotificationService renders and sends the rich HTML per-attempt notification        #
#                 email (EAMP-style layout: title bar, outcome banner, request details table,         #
#                 disclaimer and important notes) through the host application's injected email       #
#                 sender. Sending is strictly best-effort: a mail failure is logged (recipient        #
#                 masked) and never propagates into the API call that triggered it.                   #
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
# Outcome banners: statuses that read as failures, attention, or a dry run; everything else is
# a success. Colors are inline (email clients strip style blocks).
FAILURE_STATUSES = frozenset({"REJECTED", "UNAVAILABLE", "FAILED"})
BANNER_SUCCESS = ("SUCCESS", "#1e7d34")
BANNER_FAILED = ("FAILED", "#b02a37")
BANNER_ATTENTION = ("ACTION REQUIRED", "#b26a00")
BANNER_DRY_RUN = ("DRY RUN", "#4a5568")
# Human phrases for the known success outcomes; anything unlisted falls back to a generic
# "<action> <status>" phrase so new outcomes never break rendering.
SUCCESS_PHRASES: dict[tuple[str, str], str] = {
    ("schedule", "SCHEDULED"): "Maintenance window scheduled",
    ("schedule", "PENDING_UPSTREAM_ID"): "Schedule accepted, awaiting the EWS event id",
    ("start", "IN_PROGRESS"): "Maintenance started, message holds are active",
    ("complete", "COMPLETE"): "Maintenance completed, held messages released",
    ("cancel", "CANCELLED"): "Maintenance window cancelled",
}
# Inline styles for the EAMP-style layout.
CONTAINER_STYLE = (
    "font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1a1a1a;"
    "max-width:720px;border:1px solid #d0d0d0;"
)
TITLE_BAR_STYLE = "background:#1f3864;color:#ffffff;padding:12px 16px;font-size:16px;"
BANNER_STYLE = "color:#ffffff;padding:6px 16px;font-weight:bold;"
BODY_STYLE = "padding:16px;"
SECTION_TITLE_STYLE = "margin:18px 0 6px 0;font-size:15px;"
ROW_LABEL_STYLE = (
    "padding:6px 12px;border:1px solid #d0d0d0;background:#f5f5f5;"
    "font-weight:bold;text-align:left;white-space:nowrap;width:220px;"
)
ROW_VALUE_STYLE = "padding:6px 12px;border:1px solid #d0d0d0;text-align:left;"
NOTE_STYLE = (
    "margin:12px 0 0 0;padding:8px 12px;background:#fff3cd;border:1px solid #ffe08a;"
    "color:#664d03;"
)
LIST_STYLE = "margin:4px 0 0 0;padding-left:20px;"
FOOTER_STYLE = "margin:16px 0 0 0;color:#777777;font-size:12px;"


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class EmailSender(Protocol):

    """
    Port for sending a rich HTML email to a specific recipient. Satisfied structurally by the
    host application's EmailService; injected via ZelleService.get_service. The sender owns
    transport, from address, and any delivery rules.
    """

    async def send_email(
        self,
        to: str,
        subject: str,
        html_body: str,
        cc: list[str] | None = None,
        ) -> None:

        """
        Send one HTML email.

        :param to: The recipient address.
        :type to: str
        :param subject: The subject line.
        :type subject: str
        :param html_body: The HTML body.
        :type html_body: str
        :param cc: Optional CC recipient addresses.
        :type cc: list[str] | None
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


def _describe(action: str, status: str) -> tuple[str, str, str]:

    """
    Turn an (action, status) pair into presentation pieces: the banner word, the banner
    color, and a human subject phrase. Avoids the redundant "SCHEDULE SCHEDULED" style of
    wording; unknown combinations degrade to a generic phrase, never an error.

    :param action: The attempted action (schedule, start, complete, cancel, resolve).
    :type action: str
    :param status: The outcome status reported for the attempt.
    :type status: str
    :return: The (banner word, banner color, subject phrase) triple.
    :rtype: tuple[str, str, str]
    """

    if action == "resolve":
        return (*BANNER_SUCCESS, f"Event resolved to {status} by the operator")

    # endIf

    if status == "DRY_RUN":
        return (*BANNER_DRY_RUN, f"Dry run of {action}, no changes made")

    # endIf

    if status == "UNCERTAIN":
        return (
            *BANNER_ATTENTION,
            f"The {action} outcome is UNCERTAIN, operator action required",
        )

    # endIf

    if status in FAILURE_STATUSES:
        return (*BANNER_FAILED, f"The {action} attempt was {status}")

    # endIf

    phrase = SUCCESS_PHRASES.get((action, status), f"{action} {status}")
    return (*BANNER_SUCCESS, phrase)
# endDef


class NotificationService:

    """
    Per-attempt email notifier. One send per audited attempt (schedule, start, complete,
    cancel, resolve, including failures and dry runs), rendered in the EAMP-style rich
    layout. Best-effort by contract: :meth:`send_attempt` never raises.
    """

    def __init__(
        self,
        sender: EmailSender,
        settings: ZelleSettings,
        ) -> None:

        """
        Wire the notifier.

        :param sender: The host email egress (rich HTML capable).
        :type sender: EmailSender
        :param settings: Zelle facade settings (notification switch and CC list).
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
        reason: str | None = None,
        hold_mode: str | None = None,
        ) -> None:

        """
        Send the notification email for one attempt. Never raises: a mail failure is logged
        with the recipient masked and swallowed so the triggering API call is unaffected.

        :param action: The attempted action (schedule, start, complete, cancel, resolve).
        :type action: str
        :param status: The outcome to report (SCHEDULED, REJECTED, UNCERTAIN, DRY_RUN, ...).
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
        :param note: A contact-fallback or context note to surface, or None.
        :type note: str | None
        :param correlation_id: Correlation id bound to the attempt.
        :type correlation_id: str
        :param reason: The event's change reason, or None.
        :type reason: str | None
        :param hold_mode: The event's hold mode value, or None.
        :type hold_mode: str | None
        :return: None.
        :rtype: None
        """

        if not self._settings.notification_emails_enabled:
            return

        # endIf

        banner_word, banner_color, phrase = _describe(action, status)
        subject = f"{SUBJECT_PREFIX} {banner_word}: {phrase} | ticket {ticket_number}"
        html_body = self._render(
            banner_word=banner_word,
            banner_color=banner_color,
            phrase=phrase,
            action=action,
            status=status,
            event_id=event_id,
            ticket_number=ticket_number,
            window_start=window_start,
            window_end=window_end,
            requested_by=requested_by,
            note=note,
            correlation_id=correlation_id,
            reason=reason,
            hold_mode=hold_mode,
        )
        cc = list(self._settings.notification_cc)
        try:
            if cc:
                await self._sender.send_email(recipient, subject, html_body, cc=cc)
            else:
                # Positional-only call keeps hosts without a cc parameter working until
                # someone actually configures ZELLE_NOTIFICATION_CC.
                await self._sender.send_email(recipient, subject, html_body)

            # endIfElse

            logger.info(
                "notification sent: action=%s status=%s to=%s cc_count=%d event_id=%s",
                action,
                status,
                _mask_email(recipient),
                len(cc),
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
        banner_word: str,
        banner_color: str,
        phrase: str,
        action: str,
        status: str,
        event_id: str,
        ticket_number: str,
        window_start: datetime,
        window_end: datetime,
        requested_by: str | None,
        note: str | None,
        correlation_id: str,
        reason: str | None,
        hold_mode: str | None,
        ) -> str:

        """
        Render the EAMP-style HTML body: title bar, colored outcome banner, greeting,
        request details table, optional highlighted note, disclaimer, important notes, and
        footer. All values are HTML-escaped; inline styles only.

        :param banner_word: The outcome banner word (SUCCESS, FAILED, ...).
        :type banner_word: str
        :param banner_color: The banner background color.
        :type banner_color: str
        :param phrase: The human outcome phrase used in the confirmation line.
        :type phrase: str
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
        :param note: The optional fallback or context note.
        :type note: str | None
        :param correlation_id: The attempt's correlation id.
        :type correlation_id: str
        :param reason: The event's change reason, or None.
        :type reason: str | None
        :param hold_mode: The event's hold mode, or None.
        :type hold_mode: str | None
        :return: The HTML body.
        :rtype: str
        """

        rows = [
            ("Event ID", event_id),
            ("Ticket", ticket_number),
            ("Operation", action.upper()),
            ("Outcome Status", status),
            ("Reason", reason or "(not provided)"),
            ("Hold Mode", hold_mode or "(not provided)"),
            ("Window Start (UTC)", window_start.astimezone(timezone.utc).isoformat()),
            ("Window End (UTC)", window_end.astimezone(timezone.utc).isoformat()),
            ("Requested By", requested_by or "(not provided)"),
            ("Correlation ID", correlation_id),
            ("Audit Timestamp (UTC)", datetime.now(timezone.utc).isoformat()),
        ]
        row_html = "".join(
            f'<tr><th style="{ROW_LABEL_STYLE}">{escape(label)}</th>'
            f'<td style="{ROW_VALUE_STYLE}">{escape(value)}</td></tr>'
            for label, value in rows
        )
        note_html = (
            f'<p style="{NOTE_STYLE}">&#9888; {escape(note)}</p>' if note is not None else ""
        )
        greeting = escape(requested_by) if requested_by else "there"
        return (
            f'<div style="{CONTAINER_STYLE}">'
            f'<div style="{TITLE_BAR_STYLE}">Zelle Maintenance ({escape(action.upper())}) '
            "Operation Notification</div>"
            f'<div style="{BANNER_STYLE}background:{banner_color};">{escape(banner_word)}</div>'
            f'<div style="{BODY_STYLE}">'
            f"<p>Hello {greeting},</p>"
            f"<p>This is to confirm the following Zelle maintenance operation: "
            f"<b>{escape(phrase)}</b>.</p>"
            f'<h3 style="{SECTION_TITLE_STYLE}">Request Details</h3>'
            f'<table style="border-collapse:collapse;">{row_html}</table>'
            f"{note_html}"
            f'<h3 style="{SECTION_TITLE_STYLE}">Maintenance Lifecycle Disclaimer</h3>'
            f'<ul style="{LIST_STYLE}">'
            "<li>This notification reflects the facade's state at send time; EWS is the "
            "authority for live status (use the upstream-status endpoint to verify).</li>"
            "<li>Between start and complete, message holds affect live Zelle payment "
            "traffic.</li>"
            "<li>It is the requester's responsibility to complete or cancel the window on "
            "time; unstarted windows expire as NO_SHOW upstream.</li>"
            "</ul>"
            f'<h3 style="{SECTION_TITLE_STYLE}">Important Notes</h3>'
            f'<ul style="{LIST_STYLE}">'
            "<li>This action has been audited and logged; the correlation id above links "
            "the full audit trail.</li>"
            "<li>Every attempt (including failures and dry runs) is tracked for "
            "accountability.</li>"
            "<li>If you did not initiate this action, contact the AMP team "
            "immediately.</li>"
            "</ul>"
            "<p>Regards,<br/>AMP Zelle Maintenance Facade</p>"
            f'<p style="{FOOTER_STYLE}">This is an automated notification from the Zelle '
            "maintenance facade. Do not reply to this email.</p>"
            "</div>"
            "</div>"
        )
    # endDef
# endClass


# end_apis/services/zelle/notifications.py
