"""Load configuration from environment variables / .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Location ID -> Name mapping
LOCATION_NAMES = {
    "3": "London - VFS, Goswell",
    "4": "London - VFS, Hounslow",
    "8": "Birmingham - VFS, Leicester",
    "9": "Birmingham - VFS, Bradford",
    "10": "Birmingham - VFS, Manchester",
    "12": "Edinburgh - VFS, Glasgow",
    "14": "Birmingham - VFS, Birmingham",
    "22": "London - VFS, Belfast",
    "23": "London - VFS, Cardiff",
    "35": "Edinburgh - VFS, Edinburgh",
}


@dataclass
class Config:
    # Appointment target
    appointment_url: str = "https://appointment.hcilondon.gov.in/appointment.php"
    apt_type: str = "Submission"
    service_id: str = "29"

    # Multiple locations and months to monitor
    location_ids: list[str] = field(default_factory=lambda: [
        "3", "4", "8", "9", "10", "14", "22", "23",
    ])
    monitor_months: list[str] = field(default_factory=lambda: ["03", "04"])
    year: str = "2026"

    # Monitoring
    check_interval: int = 300  # seconds (5 minutes)

    # Email
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""

    # Webhook
    webhook_enabled: bool = False
    webhook_url: str = ""
    webhook_headers: str = ""

    # Proxy
    proxy_url: str = ""

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080


def load_config() -> Config:
    """Load config from .env file and environment variables."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    def _bool(val: str) -> bool:
        return val.lower() in ("true", "1", "yes")

    def _list(val: str) -> list[str]:
        return [v.strip() for v in val.split(",") if v.strip()]

    # Location IDs: comma-separated list
    default_locations = "3,4,8,9,10,14,22,23"
    location_ids_raw = os.getenv("LOCATION_IDS", default_locations)
    location_ids = _list(location_ids_raw)

    # Months to monitor
    default_months = "03,04"
    monitor_months_raw = os.getenv("MONITOR_MONTHS", default_months)
    monitor_months = _list(monitor_months_raw)

    return Config(
        appointment_url=os.getenv("APPOINTMENT_URL", Config.appointment_url),
        apt_type=os.getenv("APT_TYPE", Config.apt_type),
        service_id=os.getenv("SERVICE_ID", Config.service_id),
        location_ids=location_ids,
        monitor_months=monitor_months,
        year=os.getenv("YEAR", Config.year),
        check_interval=int(os.getenv("CHECK_INTERVAL_SECONDS", str(Config.check_interval))),
        email_enabled=_bool(os.getenv("EMAIL_ENABLED", "false")),
        smtp_host=os.getenv("SMTP_HOST", Config.smtp_host),
        smtp_port=int(os.getenv("SMTP_PORT", str(Config.smtp_port))),
        smtp_use_tls=_bool(os.getenv("SMTP_USE_TLS", "true")),
        smtp_username=os.getenv("SMTP_USERNAME", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        email_from=os.getenv("EMAIL_FROM", ""),
        email_to=os.getenv("EMAIL_TO", ""),
        webhook_enabled=_bool(os.getenv("WEBHOOK_ENABLED", "false")),
        webhook_url=os.getenv("WEBHOOK_URL", ""),
        webhook_headers=os.getenv("WEBHOOK_HEADERS", ""),
        proxy_url=os.getenv("PROXY_URL", ""),
        dashboard_enabled=_bool(os.getenv("DASHBOARD_ENABLED", "true")),
        dashboard_host=os.getenv("DASHBOARD_HOST", Config.dashboard_host),
        dashboard_port=int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", str(Config.dashboard_port)))),
    )
