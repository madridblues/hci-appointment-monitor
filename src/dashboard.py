"""Web dashboard for monitoring stats with password and IP protection."""

import base64
import json
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from src.stats import tracker

logger = logging.getLogger(__name__)

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
  .header-info { display: flex; gap: 24px; margin-top: 8px; font-size: 0.8rem; color: #94a3b8; flex-wrap: wrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-green { background: #4ade80; }
  .dot-amber { background: #fbbf24; }
  .dot-red { background: #f87171; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 16px; }
  .card-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 4px; }
  .card-value { font-size: 1.6rem; font-weight: 700; }
  .green { color: #4ade80; } .blue { color: #60a5fa; } .amber { color: #fbbf24; } .red { color: #f87171; }
  .section { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
  .section h2 { font-size: 1.05rem; margin-bottom: 14px; color: #f1f5f9; }
  .loc-card { background: #0f172a; border: 1px solid #334155; border-radius: 10px; padding: 14px; margin-bottom: 10px; }
  .loc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .loc-name { font-weight: 600; font-size: 0.95rem; }
  .loc-badge { font-size: 0.7rem; padding: 2px 10px; border-radius: 12px; }
  .loc-badge.avail { background: #166534; color: #4ade80; }
  .loc-badge.empty { background: #1e293b; color: #64748b; }
  .loc-badge.err { background: #7f1d1d; color: #f87171; }
  .loc-meta { display: flex; gap: 16px; font-size: 0.7rem; color: #64748b; margin-bottom: 8px; flex-wrap: wrap; }
  .loc-meta strong { color: #94a3b8; }
  .slot-grid { display: flex; flex-wrap: wrap; gap: 8px; }
  .slot-card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; min-width: 170px; }
  .slot-date { font-weight: 600; color: #4ade80; margin-bottom: 4px; font-size: 0.85rem; }
  .slot-time { font-size: 0.7rem; color: #cbd5e1; padding: 1px 0; display: flex; justify-content: space-between; gap: 8px; }
  .slot-count { color: #fbbf24; font-weight: 500; }
  .no-data { color: #64748b; font-style: italic; font-size: 0.8rem; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 6px 10px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 0.65rem; text-transform: uppercase; }
  td { padding: 6px 10px; border-bottom: 1px solid #1e293b; font-size: 0.75rem; }
  tr:hover td { background: #1e293b80; }
  .ok { color: #4ade80; }
  .err { color: #f87171; }
  .refresh-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
  .refresh-btn { background: #334155; border: 1px solid #475569; color: #e2e8f0; padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 0.75rem; }
  .refresh-btn:hover { background: #475569; }
  .found-row { background: #0d2818; }
  .found-times { color: #94a3b8; font-size: 0.7rem; }
</style>
</head>
<body>
<div class="header">
  <h1>HCI Appointment Stats Tracker</h1>
  <div class="header-info">
    <span id="proxy-info"></span>
    <span id="started-info"></span>
    <span id="last-check-info"></span>
  </div>
</div>
<div class="container">
  <div class="refresh-bar">
    <span style="color:#64748b;font-size:0.7rem;">Auto-refreshes every 10s</span>
    <button class="refresh-btn" onclick="loadStats()">Refresh Now</button>
  </div>

  <div class="grid" id="stat-cards"></div>

  <div class="section">
    <h2>Availability by Location</h2>
    <div id="location-cards"><span class="no-data">Loading...</span></div>
  </div>

  <div class="section">
    <h2>Found Log (Slots Discovered)</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Found At</th><th>Location</th><th>Date</th><th>Available Time Slots</th></tr></thead>
        <tbody id="found-log"></tbody>
      </table>
    </div>
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
  return new Date(iso).toLocaleString();
}
function fmtTime(iso) {
  if (!iso) return 'never';
  return new Date(iso).toLocaleTimeString();
}

function renderLocations(locations) {
  if (!locations || Object.keys(locations).length === 0)
    return '<span class="no-data">No locations tracked yet — waiting for first check cycle</span>';

  let html = '';
  const sorted = Object.values(locations).sort((a,b) => (a.location_name||'').localeCompare(b.location_name||''));

  for (const loc of sorted) {
    const slots = loc.available_slots || [];
    const hasSlots = slots.length > 0;
    const hasErr = loc.last_check_error;
    const badge = hasSlots ? 'avail' : (hasErr ? 'err' : 'empty');
    const badgeText = hasSlots ? slots.length + ' date(s)' : (hasErr ? 'Error' : 'No slots');

    html += '<div class="loc-card">';
    html += '<div class="loc-header"><span class="loc-name">' + (loc.location_name || loc.location_id) + '</span>';
    html += '<span class="loc-badge ' + badge + '">' + badgeText + '</span></div>';

    html += '<div class="loc-meta">';
    if (loc.proxy_ip) html += '<span><strong>IP:</strong> ' + loc.proxy_ip + '</span>';
    html += '<span><strong>Checked:</strong> ' + fmtTime(loc.last_check_at) + '</span>';
    html += '<span><strong>Next:</strong> ' + (loc.next_available_date || '-') + '</span>';
    html += '<span><strong>Checks:</strong> ' + (loc.total_checks||0) + '/' + (loc.total_errors||0) + ' err</span>';
    html += '</div>';

    if (hasSlots) {
      html += '<div class="slot-grid">';
      for (const s of slots) {
        html += '<div class="slot-card"><div class="slot-date">' + s.date + '/' + s.month + '/' + s.year + '</div>';
        const times = s.time_slots || [];
        if (times.length) {
          for (const t of times)
            html += '<div class="slot-time"><span>' + t.time + '</span><span class="slot-count">' + t.available + ' slot(s)</span></div>';
        }
        html += '</div>';
      }
      html += '</div>';
    } else if (hasErr) {
      html += '<div class="no-data" style="color:#f87171;">' + loc.last_check_error.substring(0, 100) + '</div>';
    }
    html += '</div>';
  }
  return html;
}

function renderFoundLog(log) {
  if (!log || log.length === 0) return '<tr><td colspan="4" class="no-data">No slots found yet</td></tr>';
  return log.slice().reverse().slice(0, 50).map(f => {
    const times = (f.time_slots || []).map(t => t.time + ' (' + t.available + ')').join(', ');
    return '<tr class="found-row"><td>' + fmt(f.timestamp) + '</td><td>' + (f.location_name||f.location_id) +
      '</td><td>' + f.date + '/' + f.month + '/' + f.year +
      '</td><td class="found-times">' + (times || '-') + '</td></tr>';
  }).join('');
}

function loadStats() {
  fetch('/api/stats')
    .then(r => r.json())
    .then(s => {
      // Header
      const pip = s.proxy_ip && s.proxy_ip !== 'unknown';
      document.getElementById('proxy-info').innerHTML = '<span class="dot ' + (pip ? 'dot-green' : 'dot-red') + '"></span> Proxy: ' + (s.proxy_ip || 'none');
      document.getElementById('started-info').textContent = 'Started: ' + fmt(s.started_at);
      document.getElementById('last-check-info').textContent = 'Last: ' + fmtTime(s.last_check_at);

      // Cards
      document.getElementById('stat-cards').innerHTML =
        '<div class="card"><div class="card-label">Checks</div><div class="card-value blue">' + s.total_checks + '</div></div>' +
        '<div class="card"><div class="card-label">Slots Found</div><div class="card-value green">' + s.total_slots_found + '</div></div>' +
        '<div class="card"><div class="card-label">Notifications</div><div class="card-value amber">' + s.total_notifications_sent + '</div></div>' +
        '<div class="card"><div class="card-label">Errors</div><div class="card-value red">' + s.total_errors + '</div></div>';

      // Location cards
      document.getElementById('location-cards').innerHTML = renderLocations(s.locations);

      // Found log
      document.getElementById('found-log').innerHTML = renderFoundLog(s.found_log || []);

      // Check history
      const checks = (s.check_history || []).slice().reverse().slice(0, 40);
      document.getElementById('check-history').innerHTML = checks.map(c =>
        '<tr><td>' + fmt(c.timestamp) + '</td><td>' + (c.location_name||c.location_id||'-') +
        '</td><td>' + c.month + '/' + c.year + '</td><td>' + c.slots_found +
        '</td><td>' + ((c.available_dates||[]).join(', ')||'-') +
        '</td><td class="' + (c.error ? 'err' : 'ok') + '">' + (c.error ? c.error.substring(0,60)+'...' : 'OK') + '</td></tr>'
      ).join('');

      // Notifications
      const notifs = (s.notification_log || []).slice().reverse().slice(0, 20);
      document.getElementById('notification-log').innerHTML = notifs.map(n =>
        '<tr><td>' + fmt(n.timestamp) + '</td><td>' + n.channel + '</td><td>' + n.slots_count +
        '</td><td class="' + (n.success ? 'ok' : 'err') + '">' + (n.success ? 'Sent' : 'Failed') + '</td></tr>'
      ).join('');
    })
    .catch(() => {
      document.getElementById('stat-cards').innerHTML = '<div class="card"><div class="card-value red">Error loading stats</div></div>';
    });
}

loadStats();
setInterval(loadStats, 10000);
</script>
</body>
</html>"""


def _get_client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0]


def _check_ip(handler):
    if not ALLOWED_IPS:
        return True
    return _get_client_ip(handler) in ALLOWED_IPS


def _check_auth(handler):
    if not DASHBOARD_PASSWORD:
        return True
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        _, password = decoded.split(":", 1)
        return password == DASHBOARD_PASSWORD
    except Exception:
        return False


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _check_ip(self):
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"403 Forbidden")
            return

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
            self.end_headers()
            self.wfile.write(data.encode())
        elif self.path in ("/", "/dashboard", "/stats"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_dashboard(host="0.0.0.0", port=8080):
    server = HTTPServer((host, port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Stats Tracker at http://%s:%d", host, port)
    return server
