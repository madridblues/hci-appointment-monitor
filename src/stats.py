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
class Stats:
    started_at: str = ""
    total_checks: int = 0
    total_slots_found: int = 0
    total_notifications_sent: int = 0
    total_errors: int = 0
    last_check_at: str = ""
    last_slots_found: int = 0
    last_available_dates: list[str] = field(default_factory=list)
    currently_available: dict[str, list[str]] = field(default_factory=dict)  # "location/month" -> dates
    check_history: list[dict] = field(default_factory=list)  # last 100 checks
    notification_log: list[dict] = field(default_factory=list)  # last 50 notifications


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

    def record_check(self, month: str, year: str, slots_found: int, available_dates: list[str],
                     error: str = "", location_id: str = "", location_name: str = ""):
        with self._lock:
            now = datetime.now().isoformat()
            self._stats.total_checks += 1
            self._stats.last_check_at = now
            self._stats.last_slots_found = slots_found
            self._stats.last_available_dates = available_dates

            if error:
                self._stats.total_errors += 1

            key = f"{location_name or location_id} {month}/{year}"
            if slots_found > 0:
                self._stats.total_slots_found += slots_found
                self._stats.currently_available[key] = available_dates
            else:
                self._stats.currently_available.pop(key, None)

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

    def reset_started(self):
        with self._lock:
            self._stats.started_at = datetime.now().isoformat()
            self._save()


# Global singleton
tracker = StatsTracker()
