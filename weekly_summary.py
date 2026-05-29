"""
weekly_summary.py — Send a weekly oil + furnace summary via ntfy.sh.

Designed to be run every Sunday morning via cron:
    0 8 * * 0  cd /home/skipsuva/oiltank && venv/bin/python weekly_summary.py >> logs/cron.log 2>&1
"""

import csv
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from notify import send_raw

CSV_PATH = Path("~/oiltank/logs/readings.csv").expanduser()
FURNACE_API_URL = "http://100.106.202.5:8080/api/burns?days=30"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _load_readings() -> list[dict]:
    """Read CSV; return rows with valid percentage (0–100 float); skip FAILED rows."""
    if not CSV_PATH.exists():
        return []
    rows = []
    try:
        with CSV_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    pct = float(row["percentage"])
                except (ValueError, KeyError):
                    continue  # skip FAILED and malformed rows
                rows.append({
                    "timestamp": row.get("timestamp", ""),
                    "percentage": round(pct * 100, 1),  # store as 0–100
                    "is_refill": row.get("level_label", "") == "REFILL",
                })
    except OSError as exc:
        print(f"WARNING: weekly_summary could not read readings.csv ({exc})", file=sys.stderr)
    return rows


def _load_furnace_sessions() -> list[dict]:
    """Fetch burn sessions from furnace monitor Pi. Returns [] on any error."""
    try:
        with urllib.request.urlopen(FURNACE_API_URL, timeout=5) as resp:
            payload = json.loads(resp.read())
        return payload.get("sessions", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Oil consumption
# ---------------------------------------------------------------------------

def _oil_used_in_window(
    rows: list[dict], window_start: datetime, window_end: datetime
) -> float | None:
    """Return percentage-point drop in [window_start, window_end), or None if insufficient data."""
    in_window = []
    for r in rows:
        try:
            dt = _parse_ts(r["timestamp"])
        except ValueError:
            continue
        if window_start <= dt < window_end:
            in_window.append((dt, r))

    # Check for a REFILL within the window; use the last one as baseline.
    last_refill = None
    for dt, r in in_window:
        if r["is_refill"]:
            last_refill = (dt, r)

    if last_refill is not None:
        refill_dt, refill_row = last_refill
        post = [(dt, r) for dt, r in in_window if not r["is_refill"] and dt > refill_dt]
        if not post:
            return None
        baseline_pct = refill_row["percentage"]
        end_pct = post[-1][1]["percentage"]
    else:
        non_refill_in = [(dt, r) for dt, r in in_window if not r["is_refill"]]
        if not non_refill_in:
            return None

        # Find reading closest to (but not after) window_start as baseline.
        before = []
        for r in rows:
            try:
                dt = _parse_ts(r["timestamp"])
            except ValueError:
                continue
            if dt < window_start and not r["is_refill"]:
                before.append((dt, r))

        if not before:
            if len(non_refill_in) < 2:
                return None
            baseline_pct = non_refill_in[0][1]["percentage"]
        else:
            baseline_pct = before[-1][1]["percentage"]

        end_pct = non_refill_in[-1][1]["percentage"]

    drop = round(baseline_pct - end_pct, 1)
    return drop if drop >= 0 else None


# ---------------------------------------------------------------------------
# Furnace stats
# ---------------------------------------------------------------------------

def _furnace_stats_in_window(
    sessions: list[dict], window_start: datetime, window_end: datetime
) -> dict:
    """Return total_seconds and longest_seconds for sessions starting in [window_start, window_end)."""
    total = 0
    longest = 0
    for s in sessions:
        raw = s.get("start_time", "")
        # start_time may be ISO format with T separator
        try:
            dt = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            continue
        if window_start <= dt < window_end:
            dur = int(s.get("duration_seconds", 0))
            total += dur
            if dur > longest:
                longest = dur
    return {"total_seconds": total, "longest_seconds": longest}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:.1f}%"


def _fmt_duration(seconds: float) -> str:
    if not seconds:
        return "—"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _week_label(start: datetime, end: datetime) -> str:
    """'May 26 – Jun 1' style label."""
    fmt_start = start.strftime("%-d %b") if start.month != end.month else start.strftime("%-d")
    fmt_end = (end - timedelta(seconds=1)).strftime("%-d %b")
    return f"{fmt_start} - {fmt_end}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_message(now: datetime | None = None) -> tuple[str, str]:
    """Return (title, body). Pass `now` explicitly for testing."""
    if now is None:
        now = datetime.now()

    # Calendar week boundaries (Monday-anchored).
    days_since_monday = now.weekday()  # Mon=0 … Sun=6
    this_week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_week_start = this_week_start - timedelta(days=7)

    rows = _load_readings()
    sessions = _load_furnace_sessions()

    current_pct = rows[-1]["percentage"] if rows else None

    oil_this = _oil_used_in_window(rows, this_week_start, now)
    oil_last = _oil_used_in_window(rows, last_week_start, this_week_start)

    burn_this = _furnace_stats_in_window(sessions, this_week_start, now)
    burn_last = _furnace_stats_in_window(sessions, last_week_start, this_week_start)

    title = f"Oil Tank - Weekly Summary ({_week_label(last_week_start, this_week_start)})"

    lines = [
        "Oil used",
        f"  This week: {_fmt_pct(oil_this)}",
        f"  Last week: {_fmt_pct(oil_last)}",
        "",
        "Furnace runtime",
        f"  This week: {_fmt_duration(burn_this['total_seconds'])}",
        f"  Last week: {_fmt_duration(burn_last['total_seconds'])}",
        "",
        "Longest burn",
        f"  This week: {_fmt_duration(burn_this['longest_seconds'])}",
        f"  Last week: {_fmt_duration(burn_last['longest_seconds'])}",
    ]
    if current_pct is not None:
        lines += ["", f"Current level: {current_pct:.1f}%"]

    return title, "\n".join(lines)


def main() -> None:
    title, body = build_message()
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} weekly_summary: sending — {title}")
    send_raw(title, body)


if __name__ == "__main__":
    main()
