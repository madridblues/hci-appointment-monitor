"""Load configuration from environment variables / .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    # Appointment target
    appointment_url: str = "https://appointment.hcilondon.gov.in/appointment.php"
    month: str = "04"
    year: str = "2026"
    apt_type: str = "Submission"
    location_id: str = "8"
    service_id: str = "29"
    monitor_months: list[str] = field(default_factory=list)

    # Monitoring
    check_interval: int = 300  # seconds

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
    proxy_url: str = ""  # e.g., http://user:pass@host:port or socks5://host:port

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

    monitor_months_raw = os.getenv("MONITOR_MONTHS", "")
    monitor_months = [m.strip() for m in monitor_months_raw.split(",") if m.strip()]

    return Config(
        appointment_url=os.getenv("APPOINTMENT_URL", Config.appointment_url),
        month=os.getenv("MONTH", Config.month),
        year=os.getenv("YEAR", Config.year),
        apt_type=os.getenv("APT_TYPE", Config.apt_type),
        location_id=os.getenv("LOCATION_ID", Config.location_id),
        service_id=os.getenv("SERVICE_ID", Config.service_id),
        monitor_months=monitor_months,
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
