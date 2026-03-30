#!/usr/bin/env python3
"""
HCI London Appointment Monitor

Continuously monitors the HCI London appointment booking page for available
slots and sends notifications via email and/or webhook.

Usage:
    # Copy .env.example to .env and configure
    cp .env.example .env

    # Run the monitor
    python monitor.py

    # Single check (no loop)
    python monitor.py --once

    # Disable dashboard
    python monitor.py --no-dashboard
"""

import argparse
import logging
import sys
import time

from src.config import load_config
from src.dashboard import start_dashboard
from src.notifier import send_email, send_webhook
from src.scraper import AvailableSlot, check_appointments
from src.stats import tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def notify(slots: list[AvailableSlot]) -> None:
    """Send notifications through all enabled channels."""
    config = load_config()

    if config.email_enabled:
        try:
            send_email(
                slots,
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                smtp_use_tls=config.smtp_use_tls,
                smtp_username=config.smtp_username,
                smtp_password=config.smtp_password,
                email_from=config.email_from,
                email_to=config.email_to,
            )
            tracker.record_notification("email", len(slots), success=True)
        except Exception:
            tracker.record_notification("email", len(slots), success=False)
            logger.error("Email notification failed, continuing...")

    if config.webhook_enabled:
        try:
            send_webhook(
                slots,
                webhook_url=config.webhook_url,
                webhook_headers=config.webhook_headers,
            )
            tracker.record_notification("webhook", len(slots), success=True)
        except Exception:
            tracker.record_notification("webhook", len(slots), success=False)
            logger.error("Webhook notification failed, continuing...")


def run_check() -> list[AvailableSlot]:
    """Run a single check across all configured months."""
    config = load_config()
    months = config.monitor_months if config.monitor_months else [config.month]

    all_slots: list[AvailableSlot] = []
    for month in months:
        try:
            slots = check_appointments(
                month=month,
                year=config.year,
                apt_type=config.apt_type,
                location_id=config.location_id,
                service_id=config.service_id,
                proxy_url=config.proxy_url,
            )
            all_slots.extend(slots)
            dates = [s.date for s in slots]
            tracker.record_check(month, config.year, len(slots), dates)
        except Exception as e:
            logger.exception("Error checking month %s/%s", month, config.year)
            tracker.record_check(month, config.year, 0, [], error=str(e))

    return all_slots


def main() -> None:
    parser = argparse.ArgumentParser(description="HCI London Appointment Monitor")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard")
    args = parser.parse_args()

    config = load_config()

    if not config.email_enabled and not config.webhook_enabled:
        logger.warning(
            "No notification channels enabled. "
            "Set EMAIL_ENABLED=true and/or WEBHOOK_ENABLED=true in .env"
        )

    months = config.monitor_months if config.monitor_months else [config.month]
    logger.info(
        "Monitoring appointments: months=%s, year=%s, type=%s, location=%s, service=%s",
        months, config.year, config.apt_type, config.location_id, config.service_id,
    )
    logger.info("Check interval: %ds | Email: %s | Webhook: %s | Proxy: %s",
                config.check_interval, config.email_enabled, config.webhook_enabled,
                bool(config.proxy_url))

    # Start dashboard
    if not args.no_dashboard and not args.once and config.dashboard_enabled:
        start_dashboard(config.dashboard_host, config.dashboard_port)

    tracker.reset_started()

    if args.once:
        slots = run_check()
        if slots:
            dates = [f"{s.month}/{s.date}/{s.year}" for s in slots]
            logger.info("AVAILABLE: %s", ", ".join(dates))
            notify(slots)
        else:
            logger.info("No slots available.")
        return

    # Continuous monitoring loop
    previously_found: set[str] = set()
    while True:
        try:
            slots = run_check()
            # Only notify on newly discovered slots
            new_slots = [s for s in slots if f"{s.month}/{s.date}/{s.year}" not in previously_found]

            if new_slots:
                dates = [f"{s.month}/{s.date}/{s.year}" for s in new_slots]
                logger.info("NEW SLOTS FOUND: %s", ", ".join(dates))
                notify(new_slots)
                for s in new_slots:
                    previously_found.add(f"{s.month}/{s.date}/{s.year}")
            else:
                logger.info("No new slots. Next check in %ds...", config.check_interval)

        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            sys.exit(0)
        except Exception:
            logger.exception("Unexpected error during check")

        try:
            time.sleep(config.check_interval)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            sys.exit(0)


if __name__ == "__main__":
    main()
