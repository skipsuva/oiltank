"""
web.py — Minimal Flask dashboard for oiltank readings.

Serves a single-page Chart.js graph of tank level over time.
Run with: venv/bin/python web.py
Access at: http://<pi-ip>:8080
"""

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory

CSV_PATH = Path("~/oiltank/logs/readings.csv").expanduser()
PRICES_CSV_PATH = Path("~/oiltank/logs/prices.csv").expanduser()
CONFIG_PATH = Path("~/oiltank/config.json").expanduser()
IMAGES_DIR = Path("~/oiltank/images").expanduser()
PORT = 8080

app = Flask(__name__)


def _load_config() -> dict:
    try:
        with CONFIG_PATH.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_prices() -> list[dict]:
    """Return all price rows sorted by period ascending. Empty list on any error."""
    cfg = _load_config()
    if not cfg.get("eia_api_key", "").strip():
        return []
    if not PRICES_CSV_PATH.exists():
        return []
    rows = []
    try:
        with PRICES_CSV_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                period = row.get("period", "")
                try:
                    rows.append({"period": period, "price": float(row["price_usd_per_gallon"])})
                except (ValueError, KeyError):
                    continue
    except OSError:
        return []
    rows.sort(key=lambda r: r["period"])
    return rows


def _load_latest_price() -> float | None:
    rows = _load_prices()
    return rows[-1]["price"] if rows else None


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
                "image_path": row.get("image_path", ""),
            })
    return rows


def _consumption_since(rows: list[dict], hours: float) -> float | None:
    """Return percentage-point drop over the past `hours` hours, or None if insufficient data."""
    if len(rows) < 2:
        return None
    last = rows[-1]
    try:
        current_dt = datetime.strptime(last["timestamp"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    target_dt = current_dt - timedelta(hours=hours)
    min_age_hours = hours * 0.5
    best, best_diff = None, None
    for r in rows[:-1]:
        try:
            dt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        age_hours = (current_dt - dt).total_seconds() / 3600
        if age_hours < min_age_hours:
            continue
        diff = abs((dt - target_dt).total_seconds())
        if best_diff is None or diff < best_diff:
            best, best_diff = r, diff
    if best is None:
        return None
    return round(best["percentage"] - last["percentage"], 1)


LOW_THRESHOLD = 25.0  # percentage points


def _days_to_threshold(rows: list[dict], week_usage: float | None) -> str:
    """Estimate days until tank hits LOW_THRESHOLD using 7-day burn rate."""
    if not rows or week_usage is None or week_usage <= 0:
        return "—"
    current_pct = rows[-1]["percentage"]
    if current_pct <= LOW_THRESHOLD:
        return "Now"
    daily_rate = week_usage / 7
    days = (current_pct - LOW_THRESHOLD) / daily_rate
    if days > 180:
        return ">6 mo"
    return f"~{round(days)} days"


@app.route("/")
def index() -> Response:
    rows = _load_readings()

    last = rows[-1] if rows else None

    day_usage = _consumption_since(rows, 24) if rows else None
    week_usage = _consumption_since(rows, 24 * 7) if rows else None
    refill_in = _days_to_threshold(rows, week_usage)

    cfg = _load_config()
    tank_capacity = float(cfg.get("tank_capacity_gallons", 275))
    price = _load_latest_price()

    def _fmt_usage(val: float | None) -> str:
        if val is None:
            return "—"
        if val > 0:
            return f"▼ {val}%"
        if val < 0:
            return f"▲ {abs(val)}%"
        return "stable"

    day_html = _fmt_usage(day_usage)
    week_html = _fmt_usage(week_usage)

    price_html = f"${price:.2f}/gal" if price is not None else "—"
    if price is not None and last:
        gallons_remaining = (last["percentage"] / 100) * tank_capacity
        gallons_needed = tank_capacity - gallons_remaining
        refill_cost_html = f"~${gallons_needed * price:,.0f}"
    else:
        refill_cost_html = "—"

    # Build JS arrays for Chart.js
    labels = json.dumps([r["timestamp"] for r in rows])
    values = json.dumps([r["percentage"] for r in rows])

    prices_rows = _load_prices()
    price_labels = json.dumps([r["period"][5:] for r in prices_rows])
    price_values = json.dumps([r["price"] for r in prices_rows])

    annotated_url = None
    if last and last.get("image_path"):
        raw = Path(last["image_path"])
        annotated = raw.with_name(raw.stem + "_annotated" + raw.suffix)
        if annotated.exists():
            annotated_url = f"/images/{annotated.name}"

    if last:
        last_time = last["timestamp"]
        short_time = ""
        try:
            dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
            last_time = dt.strftime("%b %-d, %Y at %-I:%M %p")
            short_time = dt.strftime("%-m/%-d, %-I:%M%p").lower()
        except ValueError:
            pass
        summary_html = f"""
        <div class="summary-row">
          <div class="card summary-main">
            <div class="label">Current Level</div>
            <div class="level">{last['level_label']}</div>
            <div class="pct">{last['percentage']}%</div>
            <div class="meta">conf {last['confidence']} &nbsp;&bull;&nbsp; {last_time}</div>
          </div>
          <div class="summary-side">
            <div class="stat-card">
              <div class="label">Past 24 h</div>
              <div class="stat-val">{day_html}</div>
            </div>
            <div class="stat-card">
              <div class="label">Past 7 days</div>
              <div class="stat-val">{week_html}</div>
            </div>
          </div>
        </div>
        <div class="summary-bottom">
          <div class="stat-card">
            <div class="label">Refill in</div>
            <div class="stat-val">{refill_in}</div>
          </div>
          <div class="stat-card">
            <div class="label">Oil price</div>
            <div class="stat-val">{price_html}</div>
          </div>
          <div class="stat-card">
            <div class="label">Est. refill</div>
            <div class="stat-val">{refill_cost_html}</div>
          </div>
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
  .header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
  h1 {{ font-size: 1.2rem; font-weight: 600;
        color: #94a3b8; letter-spacing: 0.05em; text-transform: uppercase; }}
  .btn-capture {{ font-size: 0.8rem; font-weight: 600; padding: 6px 14px;
                  border-radius: 8px; border: none; cursor: pointer;
                  background: #38bdf8; color: #0f172a; transition: opacity 0.15s; }}
  .btn-capture:disabled {{ opacity: 0.5; cursor: not-allowed; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 20px;
            margin-bottom: 16px; text-align: center; }}
  .label {{ font-size: 0.8rem; color: #64748b; text-transform: uppercase;
             letter-spacing: 0.08em; margin-bottom: 4px; }}
  .level {{ font-size: 2.8rem; font-weight: 700; color: #f8fafc; line-height: 1; }}
  .pct {{ font-size: 1.2rem; color: #94a3b8; margin-top: 4px; }}
  .meta {{ font-size: 0.75rem; color: #475569; margin-top: 8px; }}
  .summary-row {{ display: flex; gap: 12px; align-items: stretch; margin-bottom: 12px; flex-wrap: wrap; }}
  .summary-main {{ flex: 1 1 200px; margin-bottom: 0; }}
  .summary-side {{ display: flex; flex-direction: column; gap: 12px; min-width: 120px; flex: 0 0 auto; }}
  .summary-bottom {{ display: flex; gap: 12px; margin-bottom: 16px; }}
  .summary-bottom .stat-card {{ flex: 1; }}
  .stat-card {{ background: #1e293b; border-radius: 12px; padding: 10px 16px;
               text-align: center; flex: 1; display: flex; flex-direction: column;
               justify-content: center; }}
  .stat-val {{ font-size: 1.1rem; font-weight: 700; color: #f8fafc; margin-top: 4px; }}
  .chart-wrap {{ background: #1e293b; border-radius: 12px; padding: 16px; }}
  canvas {{ width: 100% !important; }}
  .chart-controls {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }}
  .btn-toggle {{ font-size:0.8rem; font-weight:600; padding:6px 14px; border-radius:8px;
                border:none; cursor:pointer; background:#38bdf8; color:#0f172a; }}
  .btn-toggle.inactive {{ background:#1e293b; color:#64748b; border:1px solid #334155; }}
  .btn-reset-zoom {{ font-size:0.8rem; cursor:pointer; background:none; border:none;
                    color:#475569; text-decoration:underline; margin-left:auto; padding:0; }}
  .image-wrap {{ background: #1e293b; border-radius: 12px; padding: 16px;
                 margin-top: 16px; text-align: center; }}
  .image-wrap img {{ max-width: 100%; border-radius: 8px; display: block; margin: 0 auto; }}
</style>
</head>
<body>
<div class="header">
  <h1>Oil Tank Monitor</h1>
  <button class="btn-capture" id="captureBtn" onclick="captureNow()">Capture level now</button>
</div>
{summary_html}
<div class="chart-wrap">
  <div class="chart-controls">
    <button class="btn-toggle" id="btnDaily" onclick="setMode('daily')">Daily avg</button>
    <button class="btn-toggle inactive" id="btnAll" onclick="setMode('all')">All readings</button>
    <button class="btn-toggle inactive" id="btnPrices" onclick="setMode('prices')">Oil prices</button>
    <button class="btn-reset-zoom" onclick="activeChart().resetZoom()">Reset zoom</button>
  </div>
  <canvas id="chartLevel"></canvas>
  <canvas id="chartPrices" style="display:none"></canvas>
</div>
{"" if not annotated_url else f'''<div class="image-wrap">
  <div class="label" style="margin-bottom:10px">Latest Detection &nbsp;&bull;&nbsp; {short_time}</div>
  <img src="{annotated_url}" alt="Latest annotated detection">
</div>'''}
<script>
async function captureNow() {{
  const btn = document.getElementById("captureBtn");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {{
    const res = await fetch("/capture", {{ method: "POST" }});
    const data = await res.json();
    if (data.ok) {{
      location.reload();
    }} else {{
      btn.textContent = "Failed";
      setTimeout(() => {{ btn.disabled = false; btn.textContent = "Capture level now"; }}, 3000);
    }}
  }} catch (e) {{
    btn.textContent = "Error";
    setTimeout(() => {{ btn.disabled = false; btn.textContent = "Capture level now"; }}, 3000);
  }}
}}
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2/hammer.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2/dist/chartjs-plugin-zoom.min.js"></script>
<script>
const rawLabels = {labels};
const rawValues = {values};
const priceLabels = {price_labels};
const priceValues = {price_values};

function computeDailyData(lbs, vals) {{
  const buckets = {{}};
  lbs.forEach(function(ts, i) {{
    const day = ts.slice(0, 10);
    if (!buckets[day]) buckets[day] = [];
    buckets[day].push(vals[i]);
  }});
  const days = Object.keys(buckets).sort();
  return {{
    labels: days.map(d => d.slice(5)),
    values: days.map(function(d) {{
      const a = buckets[d];
      return Math.round(a.reduce((s, v) => s + v, 0) / a.length * 10) / 10;
    }})
  }};
}}

function yRange(vals) {{
  const mn = Math.min(...vals);
  const mx = Math.max(...vals);
  const pad = Math.max((mx - mn) * 0.25, 6);
  return {{
    min: Math.max(0,   Math.round((mn - pad) * 10) / 10),
    max: Math.min(100, Math.round((mx + pad) * 10) / 10)
  }};
}}

const allLabels = rawLabels.map(s => s.slice(5, 10));
const initDaily = computeDailyData(rawLabels, rawValues);
const initRange = yRange(initDaily.values);
let currentMode = "daily";

function makeZoomPlugin() {{
  return {{ pan: {{ enabled: true, mode: "x" }}, zoom: {{ wheel: {{ enabled: true }}, pinch: {{ enabled: true }}, mode: "x" }} }};
}}

const chartLevel = new Chart(document.getElementById("chartLevel"), {{
  type: "line",
  data: {{
    labels: initDaily.labels,
    datasets: [{{
      label: "Tank Level (%)",
      data: initDaily.values,
      borderColor: "#38bdf8",
      backgroundColor: "rgba(56,189,248,0.1)",
      borderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 6,
      fill: true,
      tension: 0.3,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ ticks: {{ color: "#64748b", maxTicksLimit: 8 }}, grid: {{ color: "#1e293b" }} }},
      y: {{
        min: initRange.min,
        max: initRange.max,
        ticks: {{ color: "#64748b", callback: v => v + "%" }},
        grid: {{ color: "#334155" }}
      }}
    }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ title: function(items) {{
        const i = items[0].dataIndex;
        return currentMode === "all" ? rawLabels[i].slice(0, 16) : chartLevel.data.labels[i];
      }} }} }},
      zoom: makeZoomPlugin()
    }}
  }}
}});

const chartPrices = new Chart(document.getElementById("chartPrices"), {{
  type: "line",
  data: {{
    labels: priceLabels,
    datasets: [{{
      label: "Heating Oil ($/gal)",
      data: priceValues,
      borderColor: "#fb923c",
      backgroundColor: "rgba(251,146,60,0.1)",
      borderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 6,
      fill: true,
      tension: 0.3,
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ ticks: {{ color: "#64748b", maxTicksLimit: 8 }}, grid: {{ color: "#1e293b" }} }},
      y: {{
        ticks: {{ color: "#64748b", callback: v => "$" + v.toFixed(2) }},
        grid: {{ color: "#334155" }}
      }}
    }},
    plugins: {{
      legend: {{ display: false }},
      zoom: makeZoomPlugin()
    }}
  }}
}});

function activeChart() {{
  return currentMode === "prices" ? chartPrices : chartLevel;
}}

function setMode(mode) {{
  if (mode === currentMode) return;
  currentMode = mode;

  if (mode === "prices") {{
    document.getElementById("chartLevel").style.display = "none";
    document.getElementById("chartPrices").style.display = "block";
  }} else {{
    document.getElementById("chartPrices").style.display = "none";
    document.getElementById("chartLevel").style.display = "block";
    let newLabels, newValues;
    if (mode === "daily") {{
      const d = computeDailyData(rawLabels, rawValues);
      newLabels = d.labels; newValues = d.values;
    }} else {{
      newLabels = allLabels; newValues = rawValues;
    }}
    const range = yRange(newValues);
    chartLevel.data.labels = newLabels;
    chartLevel.data.datasets[0].data = newValues;
    chartLevel.data.datasets[0].pointRadius = newValues.length < 60 ? 4 : 0;
    chartLevel.options.scales.y.min = range.min;
    chartLevel.options.scales.y.max = range.max;
    chartLevel.resetZoom();
    chartLevel.update();
  }}

  document.getElementById("btnAll").classList.toggle("inactive", mode !== "all");
  document.getElementById("btnDaily").classList.toggle("inactive", mode !== "daily");
  document.getElementById("btnPrices").classList.toggle("inactive", mode !== "prices");
}}
</script>
</body>
</html>"""

    return Response(html, mimetype="text/html")


OILTANK_DIR = Path("~/oiltank").expanduser()


@app.route("/capture", methods=["POST"])
def capture() -> Response:
    try:
        result = subprocess.run(
            [str(OILTANK_DIR / "venv/bin/python"), "run.py"],
            cwd=OILTANK_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr or result.stdout}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Timed out after 3 minutes"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/images/<path:filename>")
def serve_image(filename: str) -> Response:
    return send_from_directory(IMAGES_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
