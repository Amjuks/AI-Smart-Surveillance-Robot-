from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.config import Settings


class AlertMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_email_alert(
        self,
        subject: str,
        body: str,
        frame_bytes: bytes | None = None,
        map_bytes: bytes | None = None,
    ) -> None:
        if not self.settings.alerts.email_enabled:
            return

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.alerts.from_email
        msg["To"] = self.settings.alerts.to_email
        msg.set_content(body)

        if frame_bytes:
            msg.add_attachment(frame_bytes, maintype="image", subtype="jpeg", filename="frame.jpg")
        if map_bytes:
            msg.add_attachment(map_bytes, maintype="image", subtype="png", filename="tracking_map.png")

        with smtplib.SMTP(self.settings.alerts.smtp_host, self.settings.alerts.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(self.settings.alerts.username, self.settings.alerts.password)
            smtp.send_message(msg)
