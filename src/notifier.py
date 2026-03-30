"""Email, webhook, and Telegram notification handlers."""

import json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

import requests

from src.config import LOCATION_NAMES
from src.scraper import AvailableSlot, BASE_URL

logger = logging.getLogger(__name__)


def build_booking_url(slot: AvailableSlot) -> str:
    """Build a direct booking URL that opens the date page with time slots visible."""
    params = {
        "month": slot.month,
        "year": slot.year,
        "apttype": slot.apt_type,
        "locationid": slot.location_id,
        "serviceid": slot.service_id,
        "date": slot.date,
    }
    return f"{BASE_URL}?{urlencode(params)}"


def _build_message(slots: list[AvailableSlot]) -> tuple[str, str]:
    """Build plain text and HTML notification messages with direct booking links."""
    by_location: dict[str, list[AvailableSlot]] = {}
    for slot in slots:
        loc = LOCATION_NAMES.get(slot.location_id, f"Location {slot.location_id}")
        by_location.setdefault(loc, []).append(slot)

    lines = ["HCI London Appointment Slots Available!", ""]
    html_parts = [
        "<h2>HCI London Appointment Slots Available!</h2>",
    ]

    for location, loc_slots in by_location.items():
        lines.append(f"=== {location} ===")
        html_parts.append(f"<h3>{location}</h3>")

        for slot in loc_slots:
            booking_url = build_booking_url(slot)
            lines.append(f"  Date: {slot.date}/{slot.month}/{slot.year}")
            html_parts.append(
                f"<p><strong>Date {slot.date}/{slot.month}/{slot.year}:</strong><br>"
            )
            if slot.time_slots:
                for ts in slot.time_slots:
                    lines.append(f"    {ts.time} ({ts.available} slot(s))")
                    html_parts.append(
                        f"&nbsp;&nbsp;{ts.time} — {ts.available} slot(s)<br>"
                    )
            lines.append(f"  BOOK NOW: {booking_url}")
            html_parts.append(
                f'<a href="{booking_url}" style="color:#4ade80;font-weight:bold;">'
                f'BOOK NOW &rarr;</a></p>'
            )

        lines.append("")

    plain = "\n".join(lines)
    html = "\n".join(html_parts)
    return plain, html


def _build_telegram_message(slots: list[AvailableSlot]) -> str:
    """Build a Telegram message with direct booking links (Markdown format)."""
    by_location: dict[str, list[AvailableSlot]] = {}
    for slot in slots:
        loc = LOCATION_NAMES.get(slot.location_id, f"Location {slot.location_id}")
        by_location.setdefault(loc, []).append(slot)

    parts = ["*APPOINTMENT SLOTS AVAILABLE*\n"]

    for location, loc_slots in by_location.items():
        parts.append(f"*{location}*")

        for slot in loc_slots:
            booking_url = build_booking_url(slot)
            parts.append(f"  Date: *{slot.date}/{slot.month}/{slot.year}*")
            if slot.time_slots:
                for ts in slot.time_slots:
                    parts.append(f"    {ts.time} ({ts.available} slots)")
            parts.append(f"  [BOOK NOW]({booking_url})\n")

    return "\n".join(parts)


def send_telegram(
    slots: list[AvailableSlot],
    bot_token: str,
    chat_id: str,
) -> None:
    """Send a Telegram notification with direct booking links."""
    message = _build_telegram_message(slots)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram notification sent to chat %s", chat_id)
    except Exception:
        logger.exception("Failed to send Telegram notification")
        raise


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

    # Count unique locations in the notification
    locations = set(
        LOCATION_NAMES.get(s.location_id, s.location_id) for s in slots
    )
    subject = f"Appointment Available - {len(slots)} slot(s) at {len(locations)} location(s)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
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
                "location_name": LOCATION_NAMES.get(s.location_id, ""),
                "service_id": s.service_id,
                "url": s.url,
                "time_slots": [
                    {"time": ts.time, "available": ts.available}
                    for ts in s.time_slots
                ],
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
