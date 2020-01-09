"""
 ****************************************************************************
 Filename:          email.py
 Description:       Contains the implementation of email plugin.

 Creation Date:     12/17/2019
 Author:            Alexander Nogikh

 Do NOT modify or remove this copyright and confidentiality notice!
 Copyright (c) 2001 - $Date: 2015/01/14 $ Seagate Technology, LLC.
 The code contained herein is CONFIDENTIAL to Seagate Technology, LLC.
 Portions are also trade secret. Any use, duplication, derivation, distribution
 or disclosure of this code, for any reason, not expressly authorized is
 prohibited. All other rights are expressly reserved by Seagate Technology, LLC.
 ****************************************************************************
"""

import asyncio
import ssl
from email.message import Message as EmailMessage
from email.headerregistry import Address
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor
from smtplib import SMTP_SSL, SMTP
from smtplib import SMTPHeloError, SMTPServerDisconnected, SMTPAuthenticationError, SMTPRecipientsRefused, SMTPSenderRefused
from csm.common.errors import CsmInternalError


class EmailError(CsmInternalError):
    pass

class InvalidCredentialsError(EmailError):
    pass

class OutOfAttemptsEmailError(EmailError):
    pass

class ServerCommunicationError(EmailError):
    pass

class BadEmailMessageError(EmailError):
    pass


class EmailConfiguration:
    smtp_host:str
    smtp_port:str
    smtp_login:str  # Set to None if the SMTP server does not require authentication
    smtp_password:str
    smtp_use_ssl:bool
    ssl_context=None  # If set to None and smtp_use_ssl is True, default context will be used
    timeout:int=30  # Timeout for a single reconnection attempt
    reconnect_attempts:int=2


class EmailSender:
    """
    Class that provides asynchronous interface for email management.
    Handles all connection/reconnection issues.

    An example of how to use it:

    config = EmailConfiguration()
    config.smtp_host = "smtp.gmail.com"
    config.smtp_port = 465
    config.smtp_login = "some_account@gmail.com"
    config.smtp_password = "SomePassword"
    config.smtp_use_ssl = True
    
    s = EmailSender(config)
    await s.send_multipart("some_account@gmail.com", 
        "target@email.com", "Subject", "<html><body>Hi!</body></html>", "Plain text")


    :param config:
    """

    SEND_MAIL_ATTEMPTS = 1
    
    def __init__(self, config: EmailConfiguration):
        self._config = config
        self._smtp_obj = self._create_smtp_object()
        self._is_connected = False
        self._executor = ThreadPoolExecutor(max_workers=1)

    def _create_smtp_object(self):
        """ Helper method that generates SMTP management objects from the configuration """
        if self._config.smtp_use_ssl:
            context = self._config.ssl_context or ssl.create_default_context()
            return SMTP_SSL(host=self._config.smtp_host, timeout=self._config.timeout, context=context)
        else:
            return SMTP(timeout=self._config.timeout)
        
    def _reconnect(self):
        for attempt in range(1, self._config.reconnect_attempts + 1):
            try:
                self._close()

                self._smtp_obj.connect(host=self._config.smtp_host, port=self._config.smtp_port)
                self._is_connected = True

                if self._config.smtp_login:
                    self._smtp_obj.login(self._config.smtp_login, self._config.smtp_password)
                return  # Success
            except (SMTPServerDisconnected, SMTPHeloError, ConnectionRefusedError) as e:
                continue  # Try again
            except SMTPAuthenticationError as e:
                raise InvalidCredentialsError(str(e)) from None
            except Exception as e:
                raise ServerCommunicationError(str(e)) from None
        raise OutOfAttemptsEmailError()

    def _close(self):
        if self._is_connected:
            self._smtp_obj.close()
            self._is_connected = False

    def _send(self, message: EmailMessage):
        for attempt in range(1, self.SEND_MAIL_ATTEMPTS + 1):
            if not self._is_connected:
                self._reconnect()

            try:
                return self._smtp_obj.send_message(message)
            except (SMTPHeloError, SMTPServerDisconnected) as e:
                self._close()
            except (SMTPRecipientsRefused, SMTPSenderRefused) as e:
                raise BadEmailMessageError(str(e)) from None

        raise OutOfAttemptsEmailError()
    
    async def send_message(self, message: EmailMessage):
        """
        Method for sending email messages.
        :param message: Instance of EmailMessage to be sent
        :returns: A dictionary that describes failed recipients
        :raise InvalidCredentialsError: The exception is raised when the provided login/password are not correct
        :raise OutOfAttemptsEmailError:
        :raise ServerCommunicationError: The exception is raised when server provides invalid responses
        :raise BadEmailMessageError: The exception is raised when it is attempted to send invalid message
        """
        loop = asyncio.get_event_loop()

        def _send():
            return self._send(message)
        return await loop.run_in_executor(self._executor, _send)

    @staticmethod
    def make_multipart(from_address, to_address, subject, html_text=None,
            plain_text=None) -> MIMEMultipart:
        """
        Method for multipart email message creation
        :param from_address: 
        :param to_address: In case of multiple recepients, join the emails by ", "
        :param subject:
        :param html_text: If not None, represents html view of the message
        :param plain_text: If not None, prepresents a plain text view of the message
        """
        msg = MIMEMultipart("alternative")
        msg['Subject'] = subject
        msg['To'] = to_address
        msg['From'] = from_address

        if plain_text:
            msg.attach(MIMEText(plain_text, "plain"))
        if html_text:
            msg.attach(MIMEText(html_text, "html"))

        return msg
    
    async def send(self, from_address, to_address, subject, html_text=None, plain_text=None):
        """ Method for multipart email message sending """
        msg = EmailSender.make_multipart(from_address, to_address, subject, html_text, plain_text)
        return await self.send_message(msg)