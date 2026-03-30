#!/usr/bin/env python3
"""
HCI London Appointment Monitor

Monitors multiple locations concurrently for available appointment slots
with time information, and sends notifications via email and/or webhook.
All requests routed through Crawlbase rotating proxy.
"""

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config import LOCATION_NAMES, load_config
from src.dashboard import start_dashboard
from datetime import datetime, timezone
from src.notifier import send_email, send_webhook, send_telegram
from src.scraper import AvailableSlot, check_appointments, save_snapshot, build_url, _detect_proxy_ip, health_check
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

    if config.telegram_enabled:
        try:
            send_telegram(
                slots,
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
            )
            tracker.record_notification("telegram", len(slots), success=True)
        except Exception:
            tracker.record_notification("telegram", len(slots), success=False)
            logger.error("Telegram notification failed, continuing...")


def check_location(location_id: str, months: list[str], year: str,
                   apt_type: str, service_id: str, proxy_url: str,
                   progress_cb=None, done_cb=None) -> list[AvailableSlot]:
    """Check all months for a single location. Runs in a thread."""
    location_name = LOCATION_NAMES.get(location_id, f"Location {location_id}")
    location_slots: list[AvailableSlot] = []

    for month in months:
        try:
            if progress_cb:
                progress_cb(location_name, month)
            logger.info("Checking %s for %s/%s...", location_name, month, year)
            result = check_appointments(
                month=month,
                year=year,
                apt_type=apt_type,
                location_id=location_id,
                service_id=service_id,
                proxy_url=proxy_url,
            )
            slots_with_times = [s for s in result.slots if s.time_slots]
            location_slots.extend(slots_with_times)

            dates = [s.date for s in slots_with_times]
            slot_details = [
                {
                    "date": s.date,
                    "month": s.month,
                    "year": s.year,
                    "time_slots": [{"time": ts.time, "available": ts.available} for ts in s.time_slots],
                    "fetched_via": s.fetched_via,
                    "fetched_ip": s.fetched_ip,
                }
                for s in slots_with_times
            ]
            req_url = build_url(month, year, apt_type, location_id, service_id)
            tracker.record_check(
                month, year, len(slots_with_times), dates,
                location_id=location_id, location_name=location_name,
                slot_details=slot_details,
                fetched_via=result.fetched_via, fetched_ip=result.fetched_ip,
                request_url=req_url,
                response_snippet=result.response_snippet,
                green_dates_found=result.green_dates_found,
                dates_checked=result.dates_checked,
            )
            if done_cb:
                done_cb(location_name)
        except Exception as e:
            logger.exception("Error checking %s for %s/%s", location_name, month, year)
            req_url = build_url(month, year, apt_type, location_id, service_id)
            tracker.record_check(
                month, year, 0, [], error=str(e),
                location_id=location_id, location_name=location_name,
                request_url=req_url,
            )
            if done_cb:
                done_cb(location_name)

    return location_slots


def get_check_interval(config) -> int:
    """Return check interval based on time of day (peak vs off-peak)."""
    now_utc = datetime.now(timezone.utc).hour
    if config.peak_start_utc <= now_utc < config.peak_end_utc:
        return config.peak_interval
    return config.offpeak_interval


def run_check() -> list[AvailableSlot]:
    """Run checks across all locations concurrently."""
    config = load_config()

    if not config.proxy_url:
        logger.error("PROXY_URL not configured — all requests must go via proxy")
        return []

    all_slots: list[AvailableSlot] = []
    n_locs = len(config.location_ids)
    n_months = len(config.monitor_months)
    total_tasks = n_locs * n_months
    done_count = 0
    active_set: set[str] = set()
    count_lock = threading.Lock()

    def on_start(location_name, month):
        """Called when a location+month starts fetching."""
        with count_lock:
            active_set.add(location_name)
            active_names = ", ".join(sorted(active_set))
            tracker.set_monitor_state(
                "checking",
                detail=f"{n_locs} locations concurrent: {active_names}",
                progress=f"{done_count}/{total_tasks} done",
            )

    def on_done(location_name):
        """Called when a location+month finishes."""
        nonlocal done_count
        with count_lock:
            done_count += 1
            active_set.discard(location_name)
            if active_set:
                active_names = ", ".join(sorted(active_set))
                detail = f"{len(active_set)} active: {active_names}"
            else:
                detail = "Finishing up..."
            tracker.set_monitor_state(
                "checking",
                detail=detail,
                progress=f"{done_count}/{total_tasks} done",
            )

    tracker.set_monitor_state("checking", detail=f"Starting {n_locs} locations...", progress=f"0/{total_tasks} done")

    with ThreadPoolExecutor(max_workers=n_locs) as executor:
        futures = {
            executor.submit(
                check_location, loc_id, config.monitor_months, config.year,
                config.apt_type, config.service_id, config.proxy_url,
                progress_cb=on_start, done_cb=on_done,
            ): loc_id
            for loc_id in config.location_ids
        }

        for future in as_completed(futures):
            loc_id = futures[future]
            try:
                slots = future.result()
                all_slots.extend(slots)
            except Exception:
                logger.exception("Thread failed for location %s", loc_id)

    return all_slots


def main() -> None:
    parser = argparse.ArgumentParser(description="HCI London Appointment Monitor")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard")
    args = parser.parse_args()

    config = load_config()

    if not config.proxy_url:
        logger.error("PROXY_URL is required. Set it in .env or environment.")
        sys.exit(1)

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
    logger.info(
        "Months: %s/%s | Type: %s | Peak: %ds (%d-%dUTC) | Off-peak: %ds | Proxy: ON",
        ",".join(config.monitor_months), config.year, config.apt_type,
        config.peak_interval, config.peak_start_utc, config.peak_end_utc,
        config.offpeak_interval,
    )
    logger.info(
        "Notifications: Email=%s | Webhook=%s | Telegram=%s",
        config.email_enabled, config.webhook_enabled, config.telegram_enabled,
    )

    # Start dashboard
    if not args.no_dashboard and not args.once and config.dashboard_enabled:
        start_dashboard(config.dashboard_host, config.dashboard_port)

    tracker.soft_reset()
    tracker.set_proxy_ip("proxy-rotated")
    tracker.set_monitor_state("starting", detail="Initializing...")

    # Restore found_log from env backup (survives redeploys)
    found_backup = os.getenv("FOUND_LOG_BACKUP", "")
    if found_backup:
        tracker.restore_from_env(found_backup)
        logger.info("Restored found_log from FOUND_LOG_BACKUP env var")

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
            # Health check: ping one appointment URL before full cycle
            tracker.set_monitor_state("health_check", detail="Pinging HCI site...")
            hc = health_check(
                config.proxy_url,
                location_id=config.location_ids[0],
                month=config.monitor_months[0],
                year=config.year,
            )
            tracker.record_health_check(hc)
            tracker.set_proxy_ip(hc.get("proxy_ip", "unknown"))

            interval = get_check_interval(config)

            if hc["status"] not in ("up",):
                logger.warning("=== Site not available: %s (%s) — skipping cycle, next in %ds ===",
                               hc["status"], hc.get("error") or hc.get("blocked") or "", interval)
                _sleep_with_state(interval, f"Site {hc['status']} — waiting")
                continue

            logger.info("=== Site UP (%s, %ss) — starting check cycle (proxy IP: %s) ===",
                        hc["http_code"], hc["response_time"], hc.get("proxy_ip", "?"))
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
                    logger.info("NEW: %s - %s/%s/%s: %s (via %s)",
                                loc, s.date, s.month, s.year, times, s.fetched_via)
                    previously_found.add(f"{s.location_id}/{s.month}/{s.date}/{s.year}")
                    try:
                        save_snapshot(s)
                    except Exception:
                        logger.warning("Failed to save snapshot for %s %s/%s",
                                       loc, s.date, s.month)
                notify(new_slots)

            # Remove from previously_found if no longer available
            current_keys = {f"{s.location_id}/{s.month}/{s.date}/{s.year}" for s in slots}
            stale = previously_found - current_keys
            if stale:
                previously_found -= stale
                logger.info("Removed %d stale slot(s) from tracking", len(stale))

            now_utc = datetime.now(timezone.utc).hour
            mode = "PEAK" if config.peak_start_utc <= now_utc < config.peak_end_utc else "OFF-PEAK"
            logger.info("=== Check cycle complete. %d available | %s mode | next in %ds ===",
                        len(slots), mode, interval)

        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            sys.exit(0)
        except Exception:
            logger.exception("Unexpected error during check")

        try:
            interval = get_check_interval(config)
            _sleep_with_state(interval, "Waiting for next cycle")
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            sys.exit(0)


def _sleep_with_state(seconds: int, reason: str):
    """Sleep while updating monitor state with countdown. Wakes early on force check."""
    next_at = datetime.now(timezone.utc).isoformat()
    from datetime import timedelta
    next_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    tracker.set_next_check_at(next_at)

    remaining = seconds
    while remaining > 0:
        mins, secs = divmod(remaining, 60)
        tracker.set_monitor_state(
            "sleeping",
            detail=f"{reason} — next in {mins}m {secs}s",
        )
        # Check for force check request every second
        if tracker.consume_force_check():
            logger.info("Force check requested — waking up early!")
            tracker.set_monitor_state("checking", detail="Force check triggered...")
            return
        time.sleep(1)
        remaining -= 1


if __name__ == "__main__":
    main()
