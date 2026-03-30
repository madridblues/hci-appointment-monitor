"""Email and webhook notification handlers."""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from src.scraper import AvailableSlot

logger = logging.getLogger(__name__)


def _build_message(slots: list[AvailableSlot]) -> tuple[str, str]:
    """Build plain text and HTML notification messages."""
    grouped: dict[str, list[AvailableSlot]] = {}
    for slot in slots:
        key = f"{slot.month}/{slot.year}"
        grouped.setdefault(key, []).append(slot)

    lines = ["HCI London Appointment Slots Available!", ""]
    html_parts = [
        "<h2>HCI London Appointment Slots Available!</h2>",
    ]

    for period, period_slots in grouped.items():
        dates = ", ".join(s.date for s in period_slots)
        url = period_slots[0].url
        apt_type = period_slots[0].apt_type

        lines.append(f"Month: {period} | Type: {apt_type}")
        lines.append(f"Available dates: {dates}")
        lines.append(f"Book now: {url}")
        lines.append("")

        html_parts.append(
            f"<p><strong>Month:</strong> {period} | <strong>Type:</strong> {apt_type}<br>"
            f"<strong>Available dates:</strong> {dates}<br>"
            f'<a href="{url}">Book Now &rarr;</a></p>'
        )

    plain = "\n".join(lines)
    html = "\n".join(html_parts)
    return plain, html


def send_email(
    slots: list[AvailableSlot],
    smtp_host: str,
    smtp_port: int,
    smtp_use_tls: bool,
    smtp_username: str,
    smtp_password: str,
    email_from: str,
    email_to: str,
) -> None:
    """Send an email notification about available slots."""
    plain, html = _build_message(slots)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Appointment Available - HCI London ({len(slots)} slot(s))"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if smtp_use_tls:
                server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(email_from, email_to.split(","), msg.as_string())
        logger.info("Email sent to %s", email_to)
    except Exception:
        logger.exception("Failed to send email")
        raise


def send_webhook(
    slots: list[AvailableSlot],
    webhook_url: str,
    webhook_headers: str = "",
) -> None:
    """Send a webhook notification about available slots."""
    plain, _ = _build_message(slots)

    payload = {
        "text": plain,
        "slots": [
            {
                "date": s.date,
                "month": s.month,
                "year": s.year,
                "apt_type": s.apt_type,
                "location_id": s.location_id,
                "service_id": s.service_id,
                "url": s.url,
            }
            for s in slots
        ],
        "total_available": len(slots),
    }

    headers = {"Content-Type": "application/json"}
    if webhook_headers:
        try:
            extra = json.loads(webhook_headers)
            headers.update(extra)
        except json.JSONDecodeError:
            logger.warning("Invalid WEBHOOK_HEADERS JSON, ignoring extra headers")

    try:
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        logger.info("Webhook sent to %s (status %d)", webhook_url, resp.status_code)
    except Exception:
        logger.exception("Failed to send webhook")
        raise
