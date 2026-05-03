"""
prices.py — Fetch and store EIA heating oil prices.

Reads eia_api_key and eia_series from config.json.
Upserts weekly prices into logs/prices.csv (idempotent).
Silent no-op if API key is not configured or any error occurs.
"""

import csv
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG_PATH = Path("~/oiltank/config.json").expanduser()
PRICES_CSV_PATH = Path("~/oiltank/logs/prices.csv").expanduser()
PRICES_CSV_COLUMNS = ["period", "price_usd_per_gallon", "series", "fetched_at"]

EIA_API_URL = (
    "https://api.eia.gov/v2/seriesid/{series}"
    "?api_key={key}&data[0]=value&length=8"
    "&sort[0][column]=period&sort[0][direction]=desc"
)


def _load_config() -> dict:
    try:
        with CONFIG_PATH.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _existing_periods() -> set[str]:
    """Return set of period strings already in prices.csv."""
    if not PRICES_CSV_PATH.exists():
        return set()
    periods = set()
    try:
        with PRICES_CSV_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                p = row.get("period", "").strip()
                if p:
                    periods.add(p)
    except OSError:
        pass
    return periods


def fetch_and_store_prices() -> float | None:
    """Fetch latest EIA heating oil prices, upsert to logs/prices.csv.

    Returns the most recent price ($/gal) or None if unavailable/unconfigured.
    """
    try:
        cfg = _load_config()
        api_key = cfg.get("eia_api_key", "").strip()
        if not api_key:
            return None

        series = cfg.get("eia_series", "PET.W_EPD2F_PRS_R10_DPG.W").strip()
        url = EIA_API_URL.format(series=series, key=api_key)

        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read())

        data_rows = payload.get("response", {}).get("data", [])
        if not data_rows:
            print("WARNING: EIA response contained no data rows.", file=sys.stderr)
            return None

        existing = _existing_periods()
        fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        new_rows = []
        for entry in data_rows:
            period = str(entry.get("period", "")).strip()
            raw_val = entry.get("value")
            if not period or raw_val is None:
                continue
            if period in existing:
                continue
            try:
                price = float(raw_val)
            except (TypeError, ValueError):
                continue
            new_rows.append({
                "period": period,
                "price_usd_per_gallon": round(price, 3),
                "series": series,
                "fetched_at": fetched_at,
            })

        if new_rows:
            PRICES_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_header = not PRICES_CSV_PATH.exists()
            with PRICES_CSV_PATH.open("a", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=PRICES_CSV_COLUMNS)
                if write_header:
                    writer.writeheader()
                writer.writerows(new_rows)
            print(f"Stored {len(new_rows)} new price row(s) in prices.csv.")

        # Return the most recent price from the full fetched set
        all_rows = sorted(data_rows, key=lambda r: r.get("period", ""))
        for entry in reversed(all_rows):
            try:
                return float(entry["value"])
            except (KeyError, TypeError, ValueError):
                continue

        return None

    except Exception as exc:
        print(f"WARNING: Price fetch failed: {exc}", file=sys.stderr)
        return None


def get_latest_price() -> float | None:
    """Read the most recent price from logs/prices.csv without hitting the network.

    Returns None if file is missing, empty, or API key is not configured.
    """
    cfg = _load_config()
    if not cfg.get("eia_api_key", "").strip():
        return None
    if not PRICES_CSV_PATH.exists():
        return None
    latest_period = ""
    latest_price = None
    try:
        with PRICES_CSV_PATH.open(newline="") as fh:
            for row in csv.DictReader(fh):
                period = row.get("period", "")
                if period > latest_period:
                    try:
                        latest_price = float(row["price_usd_per_gallon"])
                        latest_period = period
                    except (ValueError, KeyError):
                        continue
    except OSError:
        return None
    return latest_price
