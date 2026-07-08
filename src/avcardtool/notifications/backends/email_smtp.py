"""
Plain-text email notifications over SMTP.

Stdlib only (smtplib/ssl/email). Connection security follows the config:

  - use_tls with port 465        -> implicit TLS (SMTP_SSL)
  - use_tls with any other port  -> STARTTLS (the common case, port 587)
  - no TLS                       -> allowed only for localhost relays
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Dict, List

from avcardtool.notifications.base import (
    NotificationBackend,
    NotificationEvent,
    NotificationResult,
    Severity,
)

_SUBJECT_PREFIX = {
    Severity.INFO: "[AVCardTool]",
    Severity.WARNING: "[AVCardTool WARNING]",
    Severity.ERROR: "[AVCardTool ERROR]",
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class EmailBackend(NotificationBackend):
    name = "email"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.smtp_host = config.get("smtp_host", "")
        self.smtp_port = int(config.get("smtp_port", 587))
        self.use_tls = bool(config.get("use_tls", True))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.from_addr = config.get("from_addr", "")
        to_addrs = config.get("to_addrs", [])
        if isinstance(to_addrs, str):
            to_addrs = [to_addrs]
        self.to_addrs = [addr for addr in to_addrs if addr]
        self.timeout = float(config.get("timeout", 10.0))

    def validate_config(self) -> List[str]:
        errors = []
        if not self.smtp_host:
            errors.append("email: smtp_host is required")
        if not self.from_addr:
            errors.append("email: from_addr is required")
        if not self.to_addrs:
            errors.append("email: to_addrs must list at least one recipient")
        if not self.use_tls and self.smtp_host not in _LOCAL_HOSTS:
            errors.append(
                "email: plaintext SMTP is only allowed for localhost relays — set use_tls: true"
            )
        return errors

    def send(self, event: NotificationEvent) -> NotificationResult:
        errors = self.validate_config()
        if errors:
            return NotificationResult(self.name, False, "; ".join(errors))

        msg = EmailMessage()
        prefix = _SUBJECT_PREFIX.get(event.severity, "[AVCardTool]")
        msg["Subject"] = f"{prefix} {event.title}"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        body = event.body
        if event.timestamp:
            body = f"{body}\n\n-- \nAVCardTool {event.event_type} at {event.timestamp}"
        msg.set_content(body)

        smtp = None
        try:
            smtp = self._connect()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)
            return NotificationResult(
                self.name, True, f"sent to {len(self.to_addrs)} recipient(s)"
            )
        except Exception as e:
            return NotificationResult(self.name, False, str(e))
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    pass

    def _connect(self) -> smtplib.SMTP:
        if self.use_tls and self.smtp_port == 465:
            return smtplib.SMTP_SSL(
                self.smtp_host,
                self.smtp_port,
                timeout=self.timeout,
                context=ssl.create_default_context(),
            )
        smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout)
        if self.use_tls:
            smtp.starttls(context=ssl.create_default_context())
        return smtp
