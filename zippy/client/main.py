#! /usr/bin/env python3
"""Client that fetches new emails."""
import email
import functools
import socket
import ssl
import time

from typing import Callable, List, NamedTuple, Optional

import schedule

from imapclient import IMAPClient
from imapclient.exceptions import LoginError

from zippy.utils.config import get_config
from zippy.utils.log_handler import get_logger

CONFIG = get_config("client")
LOGGER = get_logger("client")

SSL_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
SSL_CONTEXT.verify_flags = ssl.CERT_OPTIONAL


class EmailAuthUser(NamedTuple):
    """Data class for email user."""

    email_address: str
    password: str
    name: Optional[str] = None

    def __repr__(self) -> str:
        """Return string representation for debugging purposes."""
        if self.name:
            return f"({self.name}, {self.email_address})"
        return self.email_address


def get_users(client_config) -> List[EmailAuthUser]:
    """Create users from list."""
    users_list = client_config.get("users")
    if users_list is None:
        raise KeyError("Missing 'users' config inside 'client'")
    return [
        EmailAuthUser(user["username"], user["password"], user.get("name"))
        for user in users_list
    ]


def with_logging(func: Callable):
    """Log when ever job is started and completed."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        LOGGER.info("LOG: Running job %s", func.__name__)
        result = func(*args, **kwargs)
        LOGGER.info('LOG: Job "%s" completed', func.__name__)
        return result

    return wrapper


def inject_server(
    func: Optional[Callable] = None, *, config: dict, ssl_context: ssl.SSLContext
):
    """Create server connection each time."""

    def decorator_server_inject(func):
        @functools.wraps(func)
        def wrapper_server_inject(*args, **kwargs):
            try:
                server = IMAPClient(
                    config["hostname"],
                    port=config["imap_port"],
                    ssl=True,
                    ssl_context=ssl_context,
                    timeout=config["timeout"],
                )
            except socket.error:
                LOGGER.exception("Could not connect to the mail server.")
                raise
            except ssl.SSLError:
                LOGGER.exception("Error due to ssl.")
                raise
            else:
                LOGGER.debug("Mail server created.")
                return func(server, *args, **kwargs)

        return wrapper_server_inject

    if func is None:
        return decorator_server_inject

    return decorator_server_inject(func)


@with_logging
@inject_server(config=CONFIG, ssl_context=SSL_CONTEXT)
def retrieve_new_emails(server: IMAPClient, user: EmailAuthUser):
    """Retrieve new unseen emails for the user."""
    with server:
        try:
            server.login(user.email_address, user.password)
            LOGGER.info("Logged in successful for %s", user)
        except LoginError:
            LOGGER.exception(
                "Credentials could not be verified for '%s'.", user.email_address
            )
            server.shutdown()
        except socket.error:
            LOGGER.exception("Could not connect to the mail server.")
            server.shutdown()
        else:
            server.select_folder("INBOX", readonly=True)

            messages = server.search("UNSEEN")
            LOGGER.debug("Searching for unseen emails...")
            for uid, message_data in server.fetch(messages, "RFC822").items():
                email_message = email.message_from_bytes(message_data[b"RFC822"])
                print(uid, email_message.get("From"), email_message.get("Subject"))


if __name__ == "__main__":
    for email_user in get_users(CONFIG):
        schedule.every(10).seconds.do(retrieve_new_emails, email_user)

    while True:
        schedule.run_pending()
        time.sleep(1)
