#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/services/zelle/alerter.py.                                                     #
# Date of birth : 2026-07-21.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : SmtpAlerter — the email egress adapter behind the watchdog's Alerter port. Sends    #
#                 stuck-event alerts through an SMTP relay using aiosmtplib so the async event loop    #
#                 is never blocked. Optional STARTTLS and optional relay credentials; the signing/     #
#                 EWS crown jewels are never involved here.                                           #
# Dependencies  : aiosmtplib, email (stdlib), pydantic (SecretStr).                                   #
# Modifications : 2026-07-21 Shane Reddy — Initial version.                                           #
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
from email.message import EmailMessage

import aiosmtplib
from pydantic import SecretStr

# Internal imports

# Local variables

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


class SmtpAlerter:

    """
    SMTP-relay implementation of the watchdog's ``Alerter`` port (structural typing — no explicit
    inheritance needed). One instance per process, constructed from settings in the runtime
    wiring; ``send`` builds a plain-text message and hands it to aiosmtplib. Only alert metadata
    is sent — never tokens, keys, or raw EWS bodies.
    """

    def __init__(
        self,
        host: str,
        port: int,
        use_tls: bool,
        username: str | None,
        password: SecretStr | None,
        sender: str,
        recipients: list[str],
        ) -> None:

        """
        Bind the alerter to its relay and envelope.

        :param host: SMTP relay hostname.
        :type host: str
        :param port: SMTP relay port.
        :type port: int
        :param use_tls: When True, negotiate STARTTLS on the connection.
        :type use_tls: bool
        :param username: Relay username, or None for an unauthenticated relay.
        :type username: str | None
        :param password: Relay password as a secret, or None for an unauthenticated relay.
        :type password: SecretStr | None
        :param sender: The From address.
        :type sender: str
        :param recipients: The To addresses.
        :type recipients: list[str]
        """

        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._username = username
        self._password = password
        self._sender = sender
        self._recipients = recipients
    # endDef

    async def send(
        self,
        subject: str,
        body: str,
        ) -> None:

        """
        Send a plain-text alert email through the configured relay.

        :param subject: The email subject line.
        :type subject: str
        :param body: The plain-text email body.
        :type body: str
        :return: None.
        :rtype: None
        :raises aiosmtplib.SMTPException: If the relay rejects or the connection fails (the caller
            swallows and logs — an alert-send failure must never kill the watchdog loop).
        """

        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = ", ".join(self._recipients)
        message["Subject"] = subject
        message.set_content(body)
        # Secret unwrapped only at the point of use; credentials omitted for an open relay.
        password = self._password.get_secret_value() if self._password is not None else None
        await aiosmtplib.send(
            message,
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=password,
            start_tls=self._use_tls,
        )
    # endDef
# endClass


# end_apis/services/zelle/alerter.py
