"""Mini live dashboard. Tails tripwire_audit.jsonl + renders current state.

Zero frontend build — single HTML page polls a small JSON endpoint.

Usage:
    python3 -m tripwire_ai.dashboard --persist-dir ./tripwire_state --port 8765
    open http://localhost:8765
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import List, Optional

from tripwire_ai.persist import load_recent_audit, load_state


HTML = """<!doctype html><html><head><meta charset=utf-8><title>Tripwire</title>
<style>
body{font-family:ui-monospace,Menlo,monospace;background:#0B1020;color:#E6E8EF;margin:0;padding:24px}
h1{color:#00E5A8;font-size:22px;margin:0 0 18px;font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1.2fr;gap:20px}
.card{background:#151B2E;border-radius:10px;padding:16px 18px;border-top:3px solid #00E5A8}
.card h2{margin:0 0 12px;font-size:13px;color:#8A93A6;text-transform:uppercase;letter-spacing:1px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{padding:6px 8px;text-align:left;border-bottom:1px solid #222840}
th{color:#8A93A6;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;font-size:10.5px}
.status{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.green{background:#00E5A8}.yellow{background:#FFB020}.red{background:#FF6B6B}.black{background:#4A4A4A}
.dim{color:#8A93A6}
.event-STATUS{color:#FFB020}.event-REGIME{color:#7C5CFF}.event-FILL{color:#C8CEDD}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;background:#222840;font-size:10.5px;margin-right:6px}
.regime-pill{background:#7C5CFF;color:#0B1020;padding:4px 10px;border-radius:12px;font-weight:600;font-size:11px}
.kpi{display:flex;gap:24px;margin-bottom:8px}
.kpi div{flex:1}
.kpi .n{font-size:28px;font-weight:700;color:#00E5A8}
.kpi .l{font-size:10.5px;color:#8A93A6;text-transform:uppercase;letter-spacing:1px}
</style></head><body>
<h1>🧠 Tripwire — Live Governance</h1>
<div id=root>loading…</div>
<script>
const COLORS={green:"green",yellow:"yellow",red:"red",black:"black"};
async function tick(){
  const r=await fetch("/state.json"); const s=await r.json();
  const kpi = `<div class=kpi>
    <div><div class=n>${s.totals.green}</div><div class=l>Green</div></div>
    <div><div class=n>${s.totals.yellow}</div><div class=l>Yellow</div></div>
    <div><div class=n style="color:#FF6B6B">${s.totals.red}</div><div class=l>Red</div></div>
    <div><div class=n style="color:#4A4A4A">${s.totals.black}</div><div class=l>Black</div></div>
    <div><div class=n>${s.totals.kills_24h}</div><div class=l>Kills 24h</div></div>
  </div>
  <div style="margin-top:10px">Current regime: <span class=regime-pill>${s.regime||"init"}</span></div>`;
  const rows = s.health.map(h => `<tr>
    <td><span class="status ${COLORS[h.status]}"></span>${h.status}</td>
    <td>${h.source}/${h.series}</td>
    <td class=dim>${h.kill_reason||"—"}</td>
    <td class=dim>${h.samples}</td></tr>`).join("");
  const events = s.events.slice(-40).reverse().map(e => `<tr>
    <td class=dim>${new Date(e.at*1000).toISOString().slice(11,19)}</td>
    <td><span class="tag event-${e.event}">${e.event}</span></td>
    <td>${e.source?`${e.source}/${e.series}`:""}</td>
    <td class=dim>${e.reason||e.from?`${e.from||""}→${e.to||""}`:""}</td></tr>`).join("");
  document.getElementById("root").innerHTML = `<div class=card>${kpi}</div>
    <div class=grid style="margin-top:20px">
      <div class=card><h2>Strategies</h2><table><tr><th>Status</th><th>Strategy</th><th>Reason</th><th>Samples</th></tr>${rows}</table></div>
      <div class=card><h2>Recent Events</h2><table><tr><th>Time</th><th>Event</th><th>Strategy</th><th>Detail</th></tr>${events}</table></div>
    </div>`;
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


def build_state(persist_dir: Path) -> dict:
    state = load_state(persist_dir / "tripwire_state.json")
    events = load_recent_audit(persist_dir / "tripwire_audit.jsonl", n=200)

    health_rows = []
    totals = {"green": 0, "yellow": 0, "red": 0, "black": 0}
    if state:
        for (source, series), sh in state.health.items():
            status = sh.status.value
            totals[status] = totals.get(status, 0) + 1
            samples = sh.scorecard.execution.samples + sh.scorecard.edge.samples
            health_rows.append({
                "source": source, "series": series, "status": status,
                "kill_reason": sh.kill_reason, "samples": samples,
            })
        health_rows.sort(key=lambda r: (-["green", "yellow", "red", "black"].index(r["status"]), r["series"]))

    import time as _t
    cutoff = _t.time() - 86400
    kills_24h = sum(1 for e in events
                    if e.get("event") == "STATUS" and e.get("to") in ("red", "black")
                    and e.get("at", 0) >= cutoff)
    totals["kills_24h"] = kills_24h

    regime = None
    if state and state.regime:
        regime = f"vol={state.regime.vol_bucket} liq={state.regime.liquidity_bucket} time={state.regime.time_bucket}"

    return {
        "regime": regime,
        "totals": totals,
        "health": health_rows,
        "events": events,
    }


def make_handler(persist_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                body = HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/state.json":
                body = json.dumps(build_state(persist_dir)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)
    return Handler


def serve(persist_dir: Path, port: int = 8765):
    srv = HTTPServer(("127.0.0.1", port), make_handler(persist_dir))
    print(f"Tripwire dashboard: http://127.0.0.1:{port}")
    print(f"Watching: {persist_dir}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persist-dir", default="tripwire_state")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    serve(Path(args.persist_dir), args.port)


if __name__ == "__main__":
    main()
