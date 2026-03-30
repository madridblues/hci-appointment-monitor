"""Web dashboard for monitoring stats."""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from src.stats import tracker

logger = logging.getLogger(__name__)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Appointment Monitor Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 24px 32px; border-bottom: 1px solid #334155; }
  .header h1 { font-size: 1.5rem; font-weight: 600; }
  .header p { color: #94a3b8; margin-top: 4px; font-size: 0.875rem; }
  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
  .card-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #64748b; margin-bottom: 8px; }
  .card-value { font-size: 2rem; font-weight: 700; }
  .card-value.green { color: #4ade80; }
  .card-value.blue { color: #60a5fa; }
  .card-value.amber { color: #fbbf24; }
  .card-value.red { color: #f87171; }
  .card-sub { font-size: 0.8rem; color: #64748b; margin-top: 4px; }
  .section { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
  .section h2 { font-size: 1.1rem; margin-bottom: 16px; color: #f1f5f9; }
  .available-badge { display: inline-block; background: #166534; color: #4ade80; padding: 4px 12px; border-radius: 20px; font-size: 0.85rem; margin: 4px; font-weight: 500; }
  .no-slots { color: #64748b; font-style: italic; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 10px 12px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e293b; font-size: 0.875rem; }
  tr:hover td { background: #1e293b80; }
  .status-ok { color: #4ade80; }
  .status-err { color: #f87171; }
  .refresh-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .refresh-btn { background: #334155; border: 1px solid #475569; color: #e2e8f0; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; }
  .refresh-btn:hover { background: #475569; }
  .auto-refresh { color: #64748b; font-size: 0.8rem; }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
</head>
<body>
<div class="header">
  <h1>HCI London Appointment Monitor</h1>
  <p>Real-time monitoring dashboard</p>
</div>
<div class="container">
  <div class="refresh-bar">
    <span class="auto-refresh">Auto-refreshes every 30s</span>
    <button class="refresh-btn" onclick="loadStats()">Refresh Now</button>
  </div>

  <div class="grid" id="stat-cards"></div>

  <div class="section">
    <h2>Currently Available Slots</h2>
    <div id="available-slots"><span class="no-slots">Loading...</span></div>
  </div>

  <div class="section">
    <h2>Recent Checks</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>Time</th><th>Month</th><th>Slots Found</th><th>Dates</th><th>Status</th></tr></thead>
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

function loadStats() {
  fetch('/api/stats')
    .then(r => r.json())
    .then(s => {
      // Stat cards
      document.getElementById('stat-cards').innerHTML = `
        <div class="card"><div class="card-label">Total Checks</div><div class="card-value blue">${s.total_checks}</div><div class="card-sub">Since ${fmt(s.started_at)}</div></div>
        <div class="card"><div class="card-label">Slots Found</div><div class="card-value green">${s.total_slots_found}</div><div class="card-sub">Cumulative total</div></div>
        <div class="card"><div class="card-label">Notifications Sent</div><div class="card-value amber">${s.total_notifications_sent}</div><div class="card-sub">Email + Webhook</div></div>
        <div class="card"><div class="card-label">Errors</div><div class="card-value red">${s.total_errors}</div><div class="card-sub">Failed checks</div></div>
        <div class="card"><div class="card-label">Last Check</div><div class="card-value" style="font-size:1rem;color:#e2e8f0">${fmt(s.last_check_at)}</div><div class="card-sub">${s.last_slots_found} slot(s) found</div></div>
      `;

      // Available slots
      const avail = s.currently_available || {};
      const keys = Object.keys(avail);
      if (keys.length === 0) {
        document.getElementById('available-slots').innerHTML = '<span class="no-slots">No slots currently available</span>';
      } else {
        let html = '';
        keys.forEach(k => {
          html += '<div style="margin-bottom:8px"><strong>' + k + ':</strong> ';
          avail[k].forEach(d => { html += '<span class="available-badge">' + d + '</span>'; });
          html += '</div>';
        });
        document.getElementById('available-slots').innerHTML = html;
      }

      // Check history (newest first)
      const checks = (s.check_history || []).slice().reverse().slice(0, 20);
      document.getElementById('check-history').innerHTML = checks.map(c =>
        `<tr><td>${fmt(c.timestamp)}</td><td>${c.month}/${c.year}</td><td>${c.slots_found}</td><td>${(c.available_dates||[]).join(', ') || '-'}</td><td class="${c.error ? 'status-err' : 'status-ok'}">${c.error || 'OK'}</td></tr>`
      ).join('');

      // Notification log (newest first)
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
setInterval(loadStats, 30000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/stats":
            data = json.dumps(tracker.get_stats())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode())
        elif self.path == "/" or self.path == "/dashboard":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress request logs


def start_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """Start the dashboard HTTP server in a background thread."""
    server = HTTPServer((host, port), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Dashboard running at http://%s:%d", host, port)
    return server
