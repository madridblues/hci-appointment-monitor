#!/usr/bin/env python3
"""
HCI London Appointment Monitor

Continuously monitors the HCI London appointment booking page across multiple
locations and months for available slots with time information, and sends
notifications via email and/or webhook.

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

from src.config import LOCATION_NAMES, load_config
from src.dashboard import start_dashboard
from src.notifier import send_email, send_webhook
from src.scraper import AvailableSlot, check_appointments, get_proxy_ip
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
    """Run a single check across all configured locations and months."""
    config = load_config()

    all_slots: list[AvailableSlot] = []
    for location_id in config.location_ids:
        location_name = LOCATION_NAMES.get(location_id, f"Location {location_id}")
        for month in config.monitor_months:
            try:
                logger.info("Checking %s for %s/%s...", location_name, month, config.year)
                slots = check_appointments(
                    month=month,
                    year=config.year,
                    apt_type=config.apt_type,
                    location_id=location_id,
                    service_id=config.service_id,
                    proxy_url=config.proxy_url,
                )
                all_slots.extend(slots)
                dates = [s.date for s in slots]
                slot_details = [
                    {
                        "date": s.date,
                        "month": s.month,
                        "year": s.year,
                        "time_slots": [{"time": ts.time, "available": ts.available} for ts in s.time_slots],
                    }
                    for s in slots
                ]
                tracker.record_check(
                    month, config.year, len(slots), dates,
                    location_id=location_id, location_name=location_name,
                    slot_details=slot_details,
                )
            except Exception as e:
                logger.exception("Error checking %s for %s/%s", location_name, month, config.year)
                tracker.record_check(
                    month, config.year, 0, [], error=str(e),
                    location_id=location_id, location_name=location_name,
                )

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

    location_names = [LOCATION_NAMES.get(lid, lid) for lid in config.location_ids]
    logger.info(
        "Monitoring %d locations: %s",
        len(config.location_ids), ", ".join(location_names),
    )
    # Detect and log proxy IP
    proxy_ip = get_proxy_ip(config.proxy_url)
    tracker.set_proxy_ip(proxy_ip)
    logger.info("Outgoing IP: %s (proxy: %s)", proxy_ip, bool(config.proxy_url))

    logger.info(
        "Months: %s/%s | Type: %s | Interval: %ds | Email: %s | Webhook: %s",
        ",".join(config.monitor_months), config.year, config.apt_type,
        config.check_interval, config.email_enabled, config.webhook_enabled,
    )

    # Start dashboard
    if not args.no_dashboard and not args.once and config.dashboard_enabled:
        start_dashboard(config.dashboard_host, config.dashboard_port)

    tracker.reset_started()

    if args.once:
        slots = run_check()
        if slots:
            for s in slots:
                loc = LOCATION_NAMES.get(s.location_id, s.location_id)
                times = ", ".join(f"{ts.time}({ts.available})" for ts in s.time_slots)
                logger.info("AVAILABLE: %s - %s/%s/%s: %s", loc, s.date, s.month, s.year, times)
            notify(slots)
        else:
            logger.info("No slots available at any location.")
        return

    # Continuous monitoring loop
    previously_found: set[str] = set()
    while True:
        try:
            slots = run_check()
            # Only notify on newly discovered slots (keyed by location+date)
            new_slots = [
                s for s in slots
                if f"{s.location_id}/{s.month}/{s.date}/{s.year}" not in previously_found
            ]

            if new_slots:
                for s in new_slots:
                    loc = LOCATION_NAMES.get(s.location_id, s.location_id)
                    times = ", ".join(f"{ts.time}({ts.available})" for ts in s.time_slots)
                    logger.info("NEW: %s - %s/%s/%s: %s", loc, s.date, s.month, s.year, times)
                    previously_found.add(f"{s.location_id}/{s.month}/{s.date}/{s.year}")
                notify(new_slots)
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
