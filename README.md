# HCI London Appointment Monitor

Monitors the [HCI London appointment booking page](https://appointment.hcilondon.gov.in) for available slots and sends notifications via **email** and/or **webhook**. Includes a **live web dashboard** and **proxy support**.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your settings

# 3. Run
python monitor.py          # continuous monitoring + dashboard
python monitor.py --once   # single check, no dashboard
```

Open **http://localhost:8080** to view the dashboard.

## Configuration

All settings are configured via `.env` file (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `MONTH` | Month to monitor (01-12) | `04` |
| `YEAR` | Year | `2026` |
| `APT_TYPE` | Appointment type | `Submission` |
| `LOCATION_ID` | Location ID | `8` |
| `SERVICE_ID` | Service ID | `29` |
| `MONITOR_MONTHS` | Comma-separated months (e.g., `04,05,06`) | uses `MONTH` |
| `CHECK_INTERVAL_SECONDS` | Seconds between checks | `300` |

### Email (SMTP)

Set `EMAIL_ENABLED=true` and configure SMTP credentials. For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833).

### Webhook

Set `WEBHOOK_ENABLED=true` and provide `WEBHOOK_URL`. The payload is JSON:

```json
{
  "text": "HCI London Appointment Slots Available!...",
  "slots": [{"date": "15", "month": "04", "year": "2026", ...}],
  "total_available": 1
}
```

Works with Slack incoming webhooks, Discord webhooks, n8n, Zapier, or any HTTP endpoint.

### Proxy

Set `PROXY_URL` to route requests through a proxy:

```env
# HTTP proxy
PROXY_URL=http://proxy-host:8080

# Authenticated proxy
PROXY_URL=http://user:password@proxy-host:8080

# SOCKS5 proxy (requires: pip install requests[socks])
PROXY_URL=socks5://proxy-host:1080
```

### Dashboard

The web dashboard runs on `http://localhost:8080` by default and shows:

- Total checks, slots found, notifications sent, errors
- Currently available slots
- Recent check history (last 100)
- Notification log (last 50)

Auto-refreshes every 30 seconds. Configure with:

```env
DASHBOARD_ENABLED=true
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
```

Disable with `--no-dashboard` flag or `DASHBOARD_ENABLED=false`.

## How It Works

1. Fetches the appointment calendar page (optionally through a proxy)
2. Parses HTML for available date slots (clickable links, CSS classes, onclick handlers)
3. Sends notifications only for **newly discovered** slots (avoids duplicate alerts)
4. Tracks all stats to `data/stats.json` for the dashboard
5. Repeats at the configured interval
