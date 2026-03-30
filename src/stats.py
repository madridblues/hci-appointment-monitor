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


@dataclass
class LocationAvailability:
    """Tracks availability state for a single location."""
    location_id: str
    location_name: str
    proxy_ip: str = ""
    # Current available slots: list of {date, month, year, time_slots: [{time, available}]}
    available_slots: list[dict] = field(default_factory=list)
    # Next available date info
    next_available_date: str = ""      # e.g. "04/05/2026"
    next_available_times: list[dict] = field(default_factory=list)  # [{time, available}]
    # Last time slots were found
    last_found_at: str = ""            # ISO timestamp
    last_found_date: str = ""          # e.g. "04/05/2026"
    last_found_times: list[dict] = field(default_factory=list)
    # Last check
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
    # Per-location availability
    locations: dict[str, dict] = field(default_factory=dict)  # location_id -> LocationAvailability as dict
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
        """Get or create location tracking entry."""
        if location_id not in self._stats.locations:
            loc = LocationAvailability(location_id=location_id, location_name=location_name)
            self._stats.locations[location_id] = asdict(loc)
        else:
            # Update name if changed
            self._stats.locations[location_id]["location_name"] = location_name
        return self._stats.locations[location_id]

    def record_check(self, month: str, year: str, slots_found: int, available_dates: list[str],
                     error: str = "", location_id: str = "", location_name: str = "",
                     slot_details: list[dict] | None = None, proxy_ip: str = ""):
        """
        Record a check result.

        slot_details: list of {date, month, year, time_slots: [{time, available}]}
        """
        with self._lock:
            now = datetime.now().isoformat()
            self._stats.total_checks += 1
            self._stats.last_check_at = now

            if error:
                self._stats.total_errors += 1

            # Update per-location state
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

            if slots_found > 0 and slot_details:
                self._stats.total_slots_found += slots_found

                # Update current availability for this location+month
                # Merge with existing slots from other months
                existing = loc.get("available_slots", [])
                # Remove old entries for this month
                existing = [s for s in existing if s.get("month") != month]
                existing.extend(slot_details)
                # Sort by date
                existing.sort(key=lambda s: (s.get("year", ""), s.get("month", ""), s.get("date", "").zfill(2)))
                loc["available_slots"] = existing

                # Update next available (earliest date with slots)
                if existing:
                    earliest = existing[0]
                    loc["next_available_date"] = f"{earliest['date']}/{earliest['month']}/{earliest['year']}"
                    loc["next_available_times"] = earliest.get("time_slots", [])

                # Update last found
                loc["last_found_at"] = now
                loc["last_found_date"] = f"{slot_details[0]['date']}/{slot_details[0]['month']}/{slot_details[0]['year']}"
                loc["last_found_times"] = slot_details[0].get("time_slots", [])
            else:
                # No slots for this month — remove this month's entries
                existing = loc.get("available_slots", [])
                existing = [s for s in existing if s.get("month") != month]
                loc["available_slots"] = existing

                # Recalculate next available from remaining
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
                error=error,
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

    def get_stats(self) -> dict:
        with self._lock:
            return asdict(self._stats)

    def set_proxy_ip(self, ip: str):
        with self._lock:
            self._stats.proxy_ip = ip
            self._save()

    def reset_started(self):
        with self._lock:
            self._stats.started_at = datetime.now().isoformat()
            # Clear stale availability data on restart
            self._stats.locations = {}
            self._stats.total_checks = 0
            self._stats.total_slots_found = 0
            self._stats.total_errors = 0
            self._stats.total_notifications_sent = 0
            self._stats.check_history = []
            self._stats.notification_log = []
            self._save()


# Global singleton
tracker = StatsTracker()
