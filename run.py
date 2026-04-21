"""
run.py — Orchestrator: capture → detect → log → notify → housekeep.

Usage:
    python run.py             # normal run
    python run.py --dry-run   # print result only, skip CSV write and notification
"""

import sys
import time
import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import cv2

from capture import capture_image
from detect import detect_level

# Stub import — notify.py is implemented separately.
try:
    from notify import send_notification
except ImportError:
    def send_notification(result: dict | None, *, failure: bool = False) -> None:
        """Placeholder until notify.py is available."""
        pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOGS_DIR = Path("~/oiltank/logs").expanduser()
IMAGES_DIR = Path("~/oiltank/images").expanduser()
CSV_PATH = LOGS_DIR / "readings.csv"
CSV_COLUMNS = ["timestamp", "level_label", "percentage", "confidence", "image_path"]

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.5
RETRY_DELAY_SECONDS = 60
IMAGE_RETENTION_DAYS = 14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_csv() -> None:
    """Create readings.csv with a header row if it doesn't exist yet."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def _append_csv(row: dict) -> None:
    """Append one row to readings.csv."""
    with CSV_PATH.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writerow(row)


def _save_annotated(annotated_image, original_path: Path) -> Path:
    """
    Save the annotated image next to the original with an '_annotated' suffix.
    Returns the path of the saved file.
    """
    annotated_path = original_path.with_stem(original_path.stem + "_annotated")
    try:
        cv2.imwrite(str(annotated_path), annotated_image)
    except Exception as exc:
        print(f"WARNING: Could not save annotated image: {exc}", file=sys.stderr)
    return annotated_path


def _purge_old_images() -> None:
    """Delete images (original and annotated) older than IMAGE_RETENTION_DAYS."""
    cutoff = datetime.now() - timedelta(days=IMAGE_RETENTION_DAYS)
    removed = 0
    for img_path in IMAGES_DIR.glob("*.jpg"):
        try:
            mtime = datetime.fromtimestamp(img_path.stat().st_mtime)
            if mtime < cutoff:
                img_path.unlink()
                removed += 1
        except OSError as exc:
            print(f"WARNING: Could not remove {img_path}: {exc}", file=sys.stderr)
    if removed:
        print(f"Purged {removed} image(s) older than {IMAGE_RETENTION_DAYS} days.")


def _attempt_reading() -> tuple[Path | None, dict | None]:
    """
    Capture one image and run detection.

    Returns:
        (image_path, result_dict) on success, or (None, None) if any step fails.
    """
    try:
        image_path, frame = capture_image()
    except Exception as exc:
        print(f"ERROR: Capture failed: {exc}", file=sys.stderr)
        return None, None

    try:
        result = detect_level(frame)
    except Exception as exc:
        print(f"ERROR: Detection failed: {exc}", file=sys.stderr)
        return image_path, None

    return image_path, result


def _print_summary(result: dict, image_path: Path) -> None:
    label = result["level_label"]
    pct = result["percentage"] * 100
    conf = result["confidence"]
    y_px = result["y_px"]
    print(f"Level : {label}  ({pct:.1f}%)")
    print(f"Conf  : {conf:.2f}  |  float centroid y={y_px}px")
    print(f"Image : {image_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Capture and log oil tank level.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print result only — skip CSV write and notification.",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- First attempt ---
    image_path, result = _attempt_reading()
    low_confidence = result is None or result["confidence"] < CONFIDENCE_THRESHOLD

    if low_confidence:
        reason = "no result" if result is None else f"confidence={result['confidence']:.2f}"
        print(f"Low-quality reading ({reason}). Waiting {RETRY_DELAY_SECONDS}s before retry…")
        time.sleep(RETRY_DELAY_SECONDS)

        # --- Retry ---
        image_path, result = _attempt_reading()
        low_confidence = result is None or result["confidence"] < CONFIDENCE_THRESHOLD

    # --- Both attempts failed ---
    if low_confidence:
        print("ERROR: Both reading attempts failed or had low confidence.", file=sys.stderr)

        if not args.dry_run:
            _ensure_csv()
            _append_csv({
                "timestamp": timestamp,
                "level_label": "FAILED",
                "percentage": "",
                "confidence": result["confidence"] if result else "",
                "image_path": str(image_path) if image_path else "",
            })
            try:
                send_notification(result, failure=True)
            except Exception as exc:
                print(f"WARNING: Notification failed: {exc}", file=sys.stderr)
        else:
            print("[dry-run] Would log FAILED row and send failure notification.")

        sys.exit(1)

    # --- Successful reading ---
    annotated_path = _save_annotated(result["annotated_image"], image_path)

    if args.dry_run:
        print("[dry-run] Skipping CSV write and notification.")
        _print_summary(result, image_path)
        return

    _ensure_csv()
    _append_csv({
        "timestamp": timestamp,
        "level_label": result["level_label"],
        "percentage": result["percentage"],
        "confidence": result["confidence"],
        "image_path": str(image_path),
    })

    try:
        send_notification(result, failure=False)
    except Exception as exc:
        print(f"WARNING: Notification failed: {exc}", file=sys.stderr)

    _purge_old_images()
    _print_summary(result, image_path)


if __name__ == "__main__":
    main()
