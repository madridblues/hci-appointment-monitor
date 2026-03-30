"""Track monitoring statistics with JSON file persistence."""

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATS_FILE = Path(__file__).resolve().parent.parent / "data" / "stats.json"


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

    def _save(self):
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(asdict(self._stats), indent=2))

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
                     fetched_via: str = "", fetched_ip: str = ""):
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

    def set_proxy_ip(self, ip: str):
        with self._lock:
            self._stats.proxy_ip = ip
            self._save()

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


# Global singleton
tracker = StatsTracker()
