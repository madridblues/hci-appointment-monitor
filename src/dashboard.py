"""Web dashboard for monitoring stats with password and IP protection."""

import base64
import json
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from src.stats import tracker

logger = logging.getLogger(__name__)

# Security config from env vars
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "google123")
ALLOWED_IPS = [ip.strip() for ip in os.getenv("ALLOWED_IPS", "82.69.40.228").split(",") if ip.strip()]

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HCI Appointment Stats Tracker</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 24px 32px; border-bottom: 1px solid #334155; }
  .header h1 { font-size: 1.5rem; font-weight: 600; }
  .header-info { display: flex; gap: 24px; margin-top: 8px; font-size: 0.8rem; color: #94a3b8; }
  .header-info span { display: flex; align-items: center; gap: 4px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-green { background: #4ade80; }
  .dot-amber { background: #fbbf24; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
  .card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 6px; }
  .card-value { font-size: 1.8rem; font-weight: 700; }
  .card-value.green { color: #4ade80; }
  .card-value.blue { color: #60a5fa; }
  .card-value.amber { color: #fbbf24; }
  .card-value.red { color: #f87171; }
  .card-sub { font-size: 0.75rem; color: #64748b; margin-top: 4px; }
  .section { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
  .section h2 { font-size: 1.1rem; margin-bottom: 16px; color: #f1f5f9; }
  .loc-card { background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 16px; margin-bottom: 12px; }
  .loc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .loc-name { font-weight: 600; font-size: 1rem; }
  .loc-status { font-size: 0.75rem; padding: 3px 10px; border-radius: 12px; }
  .loc-status.available { background: #166534; color: #4ade80; }
  .loc-status.none { background: #1e293b; color: #64748b; }
  .loc-status.error { background: #7f1d1d; color: #f87171; }
  .loc-meta { display: flex; gap: 20px; font-size: 0.75rem; color: #64748b; margin-bottom: 10px; flex-wrap: wrap; }
  .loc-meta strong { color: #94a3b8; }
  .slot-grid { display: flex; flex-wrap: wrap; gap: 8px; }
  .slot-date { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px; min-width: 180px; }
  .slot-date-header { font-weight: 600; color: #4ade80; margin-bottom: 6px; font-size: 0.9rem; }
  .slot-time { font-size: 0.75rem; color: #cbd5e1; padding: 2px 0; display: flex; justify-content: space-between; }
  .slot-time .count { color: #fbbf24; font-weight: 500; }
  .no-slots { color: #64748b; font-style: italic; font-size: 0.85rem; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td { padding: 8px 12px; border-bottom: 1px solid #1e293b; font-size: 0.8rem; }
  tr:hover td { background: #1e293b80; }
  .status-ok { color: #4ade80; }
  .status-err { color: #f87171; }
  .refresh-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .refresh-btn { background: #334155; border: 1px solid #475569; color: #e2e8f0; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 0.8rem; }
  .refresh-btn:hover { background: #475569; }
  .auto-refresh { color: #64748b; font-size: 0.75rem; }
</style>
</head>
<body>
<div class="header">
  <h1>HCI Appointment Stats Tracker</h1>
  <div class="header-info">
    <span id="proxy-info">Proxy: loading...</span>
    <span id="started-info">Started: loading...</span>
    <span id="last-check-info">Last check: loading...</span>
  </div>
</div>
<div class="container">
  <div class="refresh-bar">
    <span class="auto-refresh">Auto-refreshes every 15s</span>
    <button class="refresh-btn" onclick="loadStats()">Refresh Now</button>
  </div>

  <div class="grid" id="stat-cards"></div>

  <div class="section">
    <h2>Availability by Location</h2>
    <div id="location-cards"><span class="no-slots">Loading...</span></div>
  </div>

  <div class="section">
    <h2>Recent Checks</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Time</th><th>Location</th><th>Month</th><th>Slots</th><th>Dates</th><th>Status</th></tr></thead>
        <tbody id="check-history"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <h2>Notification Log</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Time</th><th>Channel</th><th>Slots</th><th>Status</th></tr></thead>
        <tbody id="notification-log"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
function fmt(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return d.toLocaleString();
}

function fmtShort(iso) {
  if (!iso) return 'never';
  const d = new Date(iso);
  return d.toLocaleTimeString();
}

function renderLocationCards(locations) {
  if (!locations || Object.keys(locations).length === 0) {
    return '<span class="no-slots">No locations tracked yet</span>';
  }

  let html = '';
  // Sort by location name
  const sorted = Object.values(locations).sort((a,b) => (a.location_name||'').localeCompare(b.location_name||''));

  for (const loc of sorted) {
    const slots = loc.available_slots || [];
    const hasSlots = slots.length > 0;
    const hasError = loc.last_check_error;

    let statusClass = hasSlots ? 'available' : (hasError ? 'error' : 'none');
    let statusText = hasSlots ? slots.length + ' date(s) available' : (hasError ? 'Error' : 'No slots');

    html += '<div class="loc-card">';
    html += '<div class="loc-header">';
    html += '<span class="loc-name">' + (loc.location_name || loc.location_id) + '</span>';
    html += '<span class="loc-status ' + statusClass + '">' + statusText + '</span>';
    html += '</div>';

    // Meta info
    html += '<div class="loc-meta">';
    html += '<span><strong>Last check:</strong> ' + fmtShort(loc.last_check_at) + '</span>';
    html += '<span><strong>Next available:</strong> ' + (loc.next_available_date || 'none') + '</span>';
    if (loc.last_found_at) {
      html += '<span><strong>Last found:</strong> ' + loc.last_found_date + ' at ' + fmtShort(loc.last_found_at) + '</span>';
    }
    html += '<span><strong>Checks:</strong> ' + (loc.total_checks||0) + ' (' + (loc.total_errors||0) + ' errors)</span>';
    html += '</div>';

    // Slot details
    if (hasSlots) {
      html += '<div class="slot-grid">';
      for (const slot of slots) {
        html += '<div class="slot-date">';
        html += '<div class="slot-date-header">' + slot.date + '/' + slot.month + '/' + slot.year + '</div>';
        const times = slot.time_slots || [];
        if (times.length > 0) {
          for (const ts of times) {
            html += '<div class="slot-time"><span>' + ts.time + '</span><span class="count">' + ts.available + ' slot(s)</span></div>';
          }
        } else {
          html += '<div class="slot-time"><span>Time slots not checked</span></div>';
        }
        html += '</div>';
      }
      html += '</div>';
    } else if (hasError) {
      html += '<div class="no-slots" style="color:#f87171;">' + loc.last_check_error.substring(0, 80) + '</div>';
    }

    html += '</div>';
  }
  return html;
}

function loadStats() {
  fetch('/api/stats')
    .then(r => r.json())
    .then(s => {
      // Header info
      document.getElementById('proxy-info').innerHTML = '<span class="dot ' + (s.proxy_ip && s.proxy_ip !== 'unknown' ? 'dot-green' : 'dot-amber') + '"></span> Proxy IP: ' + (s.proxy_ip || 'none');
      document.getElementById('started-info').textContent = 'Started: ' + fmt(s.started_at);
      document.getElementById('last-check-info').textContent = 'Last check: ' + fmtShort(s.last_check_at);

      // Stat cards
      document.getElementById('stat-cards').innerHTML = `
        <div class="card"><div class="card-label">Total Checks</div><div class="card-value blue">${s.total_checks}</div></div>
        <div class="card"><div class="card-label">Slots Found</div><div class="card-value green">${s.total_slots_found}</div></div>
        <div class="card"><div class="card-label">Notifications</div><div class="card-value amber">${s.total_notifications_sent}</div></div>
        <div class="card"><div class="card-label">Errors</div><div class="card-value red">${s.total_errors}</div></div>
      `;

      // Location cards with slot details
      document.getElementById('location-cards').innerHTML = renderLocationCards(s.locations);

      // Check history
      const checks = (s.check_history || []).slice().reverse().slice(0, 30);
      document.getElementById('check-history').innerHTML = checks.map(c =>
        `<tr><td>${fmt(c.timestamp)}</td><td>${c.location_name || c.location_id || '-'}</td><td>${c.month}/${c.year}</td><td>${c.slots_found}</td><td>${(c.available_dates||[]).join(', ') || '-'}</td><td class="${c.error ? 'status-err' : 'status-ok'}">${c.error ? c.error.substring(0,60)+'...' : 'OK'}</td></tr>`
      ).join('');

      // Notification log
      const notifs = (s.notification_log || []).slice().reverse().slice(0, 20);
      document.getElementById('notification-log').innerHTML = notifs.map(n =>
        `<tr><td>${fmt(n.timestamp)}</td><td>${n.channel}</td><td>${n.slots_count}</td><td class="${n.success ? 'status-ok' : 'status-err'}">${n.success ? 'Sent' : 'Failed'}</td></tr>`
      ).join('');
    })
    .catch(() => {
      document.getElementById('stat-cards').innerHTML = '<div class="card"><div class="card-value red">Error loading stats</div></div>';
    });
}

loadStats();
setInterval(loadStats, 15000);
</script>
</body>
</html>"""


def _get_client_ip(handler: BaseHTTPRequestHandler) -> str:
    """Extract client IP, checking X-Forwarded-For for reverse proxies (Render)."""
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0]


def _check_ip(handler: BaseHTTPRequestHandler) -> bool:
    """Check if client IP is in the allowed list. Empty list = allow all."""
    if not ALLOWED_IPS:
        return True
    client_ip = _get_client_ip(handler)
    return client_ip in ALLOWED_IPS


def _check_auth(handler: BaseHTTPRequestHandler) -> bool:
    """Check HTTP Basic Auth against configured password."""
    if not DASHBOARD_PASSWORD:
        return True
    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        _, password = decoded.split(":", 1)
        return password == DASHBOARD_PASSWORD
    except Exception:
        return False


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # IP check first
        if not _check_ip(self):
            client_ip = _get_client_ip(self)
            logger.warning("Blocked request from IP: %s", client_ip)
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"403 Forbidden - IP not allowed")
            return

        # Password check
        if not _check_auth(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Stats Tracker"')
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"401 Unauthorized")
            return

        if self.path == "/api/stats":
            data = json.dumps(tracker.get_stats())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode())
        elif self.path == "/" or self.path == "/dashboard" or self.path == "/stats":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Start the dashboard HTTP server in a background thread."""
    server = HTTPServer((host, port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if ALLOWED_IPS:
        logger.info("Stats Tracker running at http://%s:%d (password protected, IPs: %s)",
                     host, port, ", ".join(ALLOWED_IPS))
    else:
        logger.info("Stats Tracker running at http://%s:%d (password protected)", host, port)
    return server
