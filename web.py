"""
web.py — Minimal Flask dashboard for oiltank readings.

Serves a single-page Chart.js graph of tank level over time.
Run with: venv/bin/python web.py
Access at: http://<pi-ip>:8080
"""

import csv
import json
from datetime import datetime
from pathlib import Path

from flask import Flask, Response

CSV_PATH = Path("~/oiltank/logs/readings.csv").expanduser()
PORT = 8080

app = Flask(__name__)


def _load_readings() -> list[dict]:
    """Read CSV and return rows with valid numeric percentages, newest last."""
    if not CSV_PATH.exists():
        return []
    rows = []
    with CSV_PATH.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                pct = float(row["percentage"])
            except (ValueError, KeyError):
                continue  # skip FAILED rows and malformed lines
            rows.append({
                "timestamp": row.get("timestamp", ""),
                "level_label": row.get("level_label", ""),
                "percentage": round(pct * 100, 1),
                "confidence": row.get("confidence", ""),
            })
    return rows


@app.route("/")
def index() -> Response:
    rows = _load_readings()

    last = rows[-1] if rows else None

    # Build JS arrays for Chart.js
    labels = json.dumps([r["timestamp"] for r in rows])
    values = json.dumps([r["percentage"] for r in rows])

    if last:
        last_time = last["timestamp"]
        try:
            dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
            last_time = dt.strftime("%b %-d, %Y at %-I:%M %p")
        except ValueError:
            pass
        summary_html = f"""
        <div class="card">
          <div class="label">Current Level</div>
          <div class="level">{last['level_label']}</div>
          <div class="pct">{last['percentage']}%</div>
          <div class="meta">conf {last['confidence']} &nbsp;&bull;&nbsp; {last_time}</div>
        </div>"""
    else:
        summary_html = '<div class="card"><div class="label">No readings yet.</div></div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Oil Tank</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #0f172a; color: #e2e8f0; padding: 16px; }}
  h1 {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 16px;
        color: #94a3b8; letter-spacing: 0.05em; text-transform: uppercase; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 20px;
            margin-bottom: 16px; text-align: center; }}
  .label {{ font-size: 0.8rem; color: #64748b; text-transform: uppercase;
             letter-spacing: 0.08em; margin-bottom: 4px; }}
  .level {{ font-size: 2.8rem; font-weight: 700; color: #f8fafc; line-height: 1; }}
  .pct {{ font-size: 1.2rem; color: #94a3b8; margin-top: 4px; }}
  .meta {{ font-size: 0.75rem; color: #475569; margin-top: 8px; }}
  .chart-wrap {{ background: #1e293b; border-radius: 12px; padding: 16px; }}
  canvas {{ width: 100% !important; }}
</style>
</head>
<body>
<h1>Oil Tank Monitor</h1>
{summary_html}
<div class="chart-wrap">
  <canvas id="chart"></canvas>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const labels = {labels};
const values = {values};

new Chart(document.getElementById("chart"), {{
  type: "line",
  data: {{
    labels,
    datasets: [{{
      label: "Tank Level (%)",
      data: values,
      borderColor: "#38bdf8",
      backgroundColor: "rgba(56,189,248,0.1)",
      borderWidth: 2,
      pointRadius: values.length < 60 ? 4 : 0,
      pointHoverRadius: 6,
      fill: true,
      tension: 0.3,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{
        ticks: {{ color: "#64748b", maxTicksLimit: 6,
                  callback: function(val, i) {{
                    const s = labels[i] || "";
                    return s.slice(5, 16); // "MM-DD HH:MM"
                  }} }},
        grid: {{ color: "#1e293b" }}
      }},
      y: {{
        min: 0, max: 100,
        ticks: {{ color: "#64748b", callback: v => v + "%" }},
        grid: {{ color: "#334155" }}
      }}
    }},
    plugins: {{
      legend: {{ display: false }},
      annotation: {{}}
    }}
  }}
}});
</script>
</body>
</html>"""

    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
