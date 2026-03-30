"""Track monitoring statistics with JSON file persistence and Render env var backup."""

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests as _requests

logger = logging.getLogger(__name__)

STATS_FILE = Path(__file__).resolve().parent.parent / "data" / "stats.json"

# Render API for persisting found_log across deploys
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_SERVICE_ID = os.getenv("RENDER_SERVICE_ID", "")


@dataclass
class CheckRecord:
    timestamp: str
    month: str
    year: str
    slots_found: int
    available_dates: list[str]
    location_id: str = ""
    location_name: str = ""
    error: str = ""
    fetched_via: str = ""
    fetched_ip: str = ""
    request_url: str = ""
    response_snippet: str = ""  # first 500 chars of response for debugging
    green_dates_found: int = 0  # how many green dates on calendar
    dates_checked: int = 0      # how many date pages were fetched


@dataclass
class FoundRecord:
    """A record of when slots were found at a location."""
    timestamp: str
    location_id: str
    location_name: str
    date: str      # e.g. "5"
    month: str
    year: str
    time_slots: list[dict] = field(default_factory=list)  # [{time, available}]
    fetched_via: str = ""   # "proxy" or "direct"
    fetched_ip: str = ""    # IP used


@dataclass
class LocationState:
    """Current state for a single location (from latest check only)."""
    location_id: str
    location_name: str
    proxy_ip: str = ""
    # Current available slots from LATEST check only
    available_slots: list[dict] = field(default_factory=list)
    # Next available
    next_available_date: str = ""
    next_available_times: list[dict] = field(default_factory=list)
    # Last check info
    last_check_at: str = ""
    last_check_error: str = ""
    total_checks: int = 0
    total_errors: int = 0


@dataclass
class Stats:
    started_at: str = ""
    proxy_ip: str = ""
    total_checks: int = 0
    total_slots_found: int = 0
    total_notifications_sent: int = 0
    total_errors: int = 0
    last_check_at: str = ""
    # Site health
    site_status: str = ""          # "up", "down", "blocked", "timeout"
    last_health_check: str = ""
    health_history: list[dict] = field(default_factory=list)
    # Live monitor state (not persisted to disk)
    monitor_state: str = "starting"   # starting/health_check/checking/sleeping/idle
    monitor_detail: str = ""          # e.g. "Checking Birmingham 04/2026"
    monitor_progress: str = ""        # e.g. "3/18"
    next_check_at: str = ""           # ISO timestamp of next check
    force_check_requested: bool = False
    # Per-location state (latest check only)
    locations: dict[str, dict] = field(default_factory=dict)
    # Found log — every time slots were found (persistent history)
    found_log: list[dict] = field(default_factory=list)
    check_history: list[dict] = field(default_factory=list)
    notification_log: list[dict] = field(default_factory=list)


class StatsTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._stats = Stats(started_at=datetime.now().isoformat())
        self._load()

    def _load(self):
        if STATS_FILE.exists():
            try:
                data = json.loads(STATS_FILE.read_text())
                self._stats = Stats(**{
                    k: v for k, v in data.items() if k in Stats.__dataclass_fields__
                })
            except Exception:
                logger.warning("Could not load stats file, starting fresh")

    # Fields that are transient (not saved to disk)
    _TRANSIENT_FIELDS = {"monitor_state", "monitor_detail", "monitor_progress",
                         "next_check_at", "force_check_requested"}

    def _save(self):
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self._stats).items()
                if k not in self._TRANSIENT_FIELDS}
        STATS_FILE.write_text(json.dumps(data, indent=2))

    def _backup_found_log_to_render(self):
        """Push found_log to FOUND_LOG_BACKUP env var via Render API (non-blocking)."""
        if not RENDER_API_KEY or not RENDER_SERVICE_ID:
            return
        def _do_backup():
            try:
                found_json = json.dumps(self._stats.found_log)
                # Use PATCH to update just FOUND_LOG_BACKUP without wiping other vars
                url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars/FOUND_LOG_BACKUP"
                _requests.put(
                    url,
                    headers={"Authorization": f"Bearer {RENDER_API_KEY}",
                             "Content-Type": "application/json"},
                    json={"value": found_json},
                    timeout=10,
                )
                logger.info("Backed up found_log (%d entries) to Render env var", len(self._stats.found_log))
            except Exception as e:
                logger.debug("Found log backup failed (non-critical): %s", e)
        threading.Thread(target=_do_backup, daemon=True).start()

    def _get_location(self, location_id: str, location_name: str) -> dict:
        """Get or create location state."""
        if location_id not in self._stats.locations:
            loc = LocationState(location_id=location_id, location_name=location_name)
            self._stats.locations[location_id] = asdict(loc)
        else:
            self._stats.locations[location_id]["location_name"] = location_name
        return self._stats.locations[location_id]

    def record_check(self, month: str, year: str, slots_found: int, available_dates: list[str],
                     error: str = "", location_id: str = "", location_name: str = "",
                     slot_details: list[dict] | None = None, proxy_ip: str = "",
                     fetched_via: str = "", fetched_ip: str = "", request_url: str = "",
                     response_snippet: str = "", green_dates_found: int = 0,
                     dates_checked: int = 0):
        with self._lock:
            now = datetime.now().isoformat()
            self._stats.total_checks += 1
            self._stats.last_check_at = now

            if error:
                self._stats.total_errors += 1

            loc = self._get_location(location_id, location_name)
            loc["last_check_at"] = now
            loc["total_checks"] = loc.get("total_checks", 0) + 1

            if proxy_ip:
                loc["proxy_ip"] = proxy_ip

            if error:
                loc["last_check_error"] = error
                loc["total_errors"] = loc.get("total_errors", 0) + 1
            else:
                loc["last_check_error"] = ""

            # Replace availability for this month (latest check only)
            existing = loc.get("available_slots", [])
            existing = [s for s in existing if s.get("month") != month]

            if slots_found > 0 and slot_details:
                self._stats.total_slots_found += slots_found
                existing.extend(slot_details)

                # Add to found log
                for sd in slot_details:
                    found = FoundRecord(
                        timestamp=now,
                        location_id=location_id,
                        location_name=location_name,
                        date=sd["date"],
                        month=sd["month"],
                        year=sd["year"],
                        time_slots=sd.get("time_slots", []),
                        fetched_via=sd.get("fetched_via", fetched_via),
                        fetched_ip=sd.get("fetched_ip", fetched_ip),
                    )
                    self._stats.found_log.append(asdict(found))

                self._stats.found_log = self._stats.found_log[-100:]
                # Auto-backup found_log to Render env var (persists across deploys)
                self._backup_found_log_to_render()

            existing.sort(key=lambda s: (s.get("year", ""), s.get("month", ""), s.get("date", "").zfill(2)))
            loc["available_slots"] = existing

            # Update next available
            if existing:
                earliest = existing[0]
                loc["next_available_date"] = f"{earliest['date']}/{earliest['month']}/{earliest['year']}"
                loc["next_available_times"] = earliest.get("time_slots", [])
            else:
                loc["next_available_date"] = ""
                loc["next_available_times"] = []

            # Check history
            record = CheckRecord(
                timestamp=now, month=month, year=year,
                slots_found=slots_found, available_dates=available_dates,
                location_id=location_id, location_name=location_name,
                error=error, fetched_via=fetched_via, fetched_ip=fetched_ip,
                request_url=request_url,
                response_snippet=response_snippet,
                green_dates_found=green_dates_found,
                dates_checked=dates_checked,
            )
            self._stats.check_history.append(asdict(record))
            self._stats.check_history = self._stats.check_history[-200:]

            self._save()

    def record_notification(self, channel: str, slots_count: int, success: bool):
        with self._lock:
            if success:
                self._stats.total_notifications_sent += 1
            self._stats.notification_log.append({
                "timestamp": datetime.now().isoformat(),
                "channel": channel,
                "slots_count": slots_count,
                "success": success,
            })
            self._stats.notification_log = self._stats.notification_log[-50:]
            self._save()

    def record_health_check(self, hc: dict):
        with self._lock:
            now = datetime.now().isoformat()
            self._stats.site_status = hc.get("status", "unknown")
            self._stats.last_health_check = now
            entry = {
                "timestamp": now,
                "status": hc.get("status"),
                "http_code": hc.get("http_code"),
                "response_time": hc.get("response_time"),
                "proxy_ip": hc.get("proxy_ip"),
                "error": hc.get("error"),
                "blocked": hc.get("blocked"),
                "url": hc.get("url"),
            }
            self._stats.health_history.append(entry)
            self._stats.health_history = self._stats.health_history[-50:]
            self._save()

    def set_proxy_ip(self, ip: str):
        with self._lock:
            self._stats.proxy_ip = ip
            self._save()

    def set_monitor_state(self, state: str, detail: str = "", progress: str = ""):
        """Update the live monitor state (not persisted to disk)."""
        with self._lock:
            self._stats.monitor_state = state
            self._stats.monitor_detail = detail
            self._stats.monitor_progress = progress

    def set_next_check_at(self, iso_time: str):
        with self._lock:
            self._stats.next_check_at = iso_time

    def request_force_check(self):
        with self._lock:
            self._stats.force_check_requested = True

    def consume_force_check(self) -> bool:
        with self._lock:
            if self._stats.force_check_requested:
                self._stats.force_check_requested = False
                return True
            return False

    def get_stats(self) -> dict:
        with self._lock:
            return asdict(self._stats)

    def reset_started(self):
        with self._lock:
            self._stats.started_at = datetime.now().isoformat()
            self._stats.locations = {}
            self._stats.total_checks = 0
            self._stats.total_slots_found = 0
            self._stats.total_errors = 0
            self._stats.total_notifications_sent = 0
            self._stats.check_history = []
            self._stats.notification_log = []
            self._stats.found_log = []
            self._save()

    def soft_reset(self):
        """Reset current session counters but preserve found_log history."""
        with self._lock:
            self._stats.started_at = datetime.now().isoformat()
            # Clear current location state (will be rebuilt from new checks)
            self._stats.locations = {}
            # Reset session counters
            self._stats.total_checks = 0
            self._stats.total_slots_found = 0
            self._stats.total_errors = 0
            self._stats.total_notifications_sent = 0
            # Clear current session logs
            self._stats.check_history = []
            self._stats.notification_log = []
            # PRESERVE found_log — this is the persistent history
            # PRESERVE health_history — useful for debugging
            self._save()

    def backup_to_env(self) -> str:
        """Export found_log as JSON string (for env var backup)."""
        with self._lock:
            return json.dumps(self._stats.found_log)

    def restore_from_env(self, found_log_json: str):
        """Import found_log from JSON string (from env var)."""
        with self._lock:
            try:
                imported = json.loads(found_log_json)
                if isinstance(imported, list):
                    # Merge: add any entries not already present
                    existing_keys = {
                        f"{e.get('location_id')}/{e.get('date')}/{e.get('month')}/{e.get('year')}"
                        for e in self._stats.found_log
                    }
                    for entry in imported:
                        key = f"{entry.get('location_id')}/{entry.get('date')}/{entry.get('month')}/{entry.get('year')}"
                        if key not in existing_keys:
                            self._stats.found_log.append(entry)
                    self._stats.found_log = self._stats.found_log[-200:]
                    self._save()
                    logger.info("Restored %d found_log entries from backup", len(imported))
            except Exception as e:
                logger.warning("Failed to restore found_log: %s", e)


# Global singleton
tracker = StatsTracker()
