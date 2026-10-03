from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import Settings

log = logging.getLogger("puzmania.notify")


def send_summary(settings: Settings, prefs: dict, subject: str, body: str, urgent: bool = False) -> None:
    log.info("%s\n%s", subject, body)
    if prefs.get("notifyEmail") and settings.smtp_host and settings.smtp_to:
        _email(settings, subject, body)
    if prefs.get("notifySlack") and settings.slack_webhook_url:
        _slack(settings.slack_webhook_url, f"*{subject}*\n{body}")
    if urgent and prefs.get("alertOnFailure") and settings.slack_webhook_url:
        _slack(settings.slack_webhook_url, f"*Alert:* {subject}\n{body}")


def _email(settings: Settings, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = settings.smtp_to
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        log.warning("Email notification failed: %s", exc)


def _slack(url: str, text: str) -> None:
    try:
        httpx.post(url, json={"text": text}, timeout=15.0)
    except httpx.HTTPError as exc:
        log.warning("Slack notification failed: %s", exc)
