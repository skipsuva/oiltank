"""
refill.py — Log a manual oil tank refill event.

Usage:
    venv/bin/python refill.py              # assumes 100% post-refill
    venv/bin/python refill.py --pct 0.95   # specify post-refill level (0.0–1.0)
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

CSV_PATH = Path("~/oiltank/logs/readings.csv").expanduser()
CSV_COLUMNS = ["timestamp", "level_label", "percentage", "confidence", "image_path"]


def _ensure_csv() -> None:
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=CSV_COLUMNS).writeheader()


def _append_csv(row: dict) -> None:
    with CSV_PATH.open("a", newline="") as fh:
        csv.DictWriter(fh, fieldnames=CSV_COLUMNS).writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Log a manual oil tank refill event.")
    parser.add_argument(
        "--pct",
        type=float,
        default=1.0,
        metavar="FLOAT",
        help="Post-refill tank level as a decimal 0.0–1.0 (default: 1.0)",
    )
    args = parser.parse_args()

    if not (0.0 <= args.pct <= 1.0):
        print(f"ERROR: --pct must be between 0.0 and 1.0, got {args.pct}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _ensure_csv()
    _append_csv({
        "timestamp": timestamp,
        "level_label": "REFILL",
        "percentage": args.pct,
        "confidence": "",
        "image_path": "",
    })
    print(f"Refill logged: {timestamp}  post-refill level = {round(args.pct * 100, 1)}%")


if __name__ == "__main__":
    main()
