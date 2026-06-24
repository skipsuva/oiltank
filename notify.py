"""
notify.py — Send push notifications via ntfy.sh.

Config lives in ~/oiltank/config.json:
  ntfy_topic      — full ntfy URL, e.g. "https://ntfy.sh/your-secret-topic"
  low_threshold   — 0.0–1.0 fraction below which a low-level alert fires (default 0.25)
  warn_thresholds — list of 0.0–1.0 fractions for additional warning alerts (default [0.75, 0.50])

Notification logic:
  failure=True  → always alert (detection failed)
  failure=False → alert when percentage <= low_threshold OR any warn_thresholds entry; silent otherwise
"""

import json
import sys
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("~/oiltank/config.json").expanduser()
NOTIFIED_STATE_PATH = Path("~/oiltank/logs/notified_thresholds.json").expanduser()
DEFAULT_LOW_THRESHOLD = 0.25
DEFAULT_WARN_THRESHOLDS = [0.75, 0.50]


def _load_notified() -> set[float]:
    try:
        with NOTIFIED_STATE_PATH.open() as fh:
            return set(json.load(fh))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_notified(notified: set[float]) -> None:
    NOTIFIED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with NOTIFIED_STATE_PATH.open("w") as fh:
        json.dump(sorted(notified), fh)


def _load_config() -> dict:
    try:
        with CONFIG_PATH.open() as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: notify.py could not read config.json ({exc})", file=sys.stderr)
        return {}


def _post(topic: str, title: str, message: str) -> None:
    data = message.encode("utf-8")
    req = urllib.request.Request(
        topic,
        data=data,
        headers={
            "Title": title,
            "Content-Type": "text/plain",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 201):
            print(
                f"WARNING: ntfy returned HTTP {resp.status}", file=sys.stderr
            )


def send_raw(title: str, message: str) -> None:
    """Send an arbitrary titled notification. Silent no-op if topic is not configured."""
    config = _load_config()
    topic = config.get("ntfy_topic", "").strip()
    if not topic or topic.startswith("https://ntfy.sh/your-secret"):
        return
    _post(topic, title, message)


def send_notification(result: dict | None, *, failure: bool = False) -> None:
    """
    Send a push notification via ntfy.sh.

    Args:
        result:  DetectionResult dict, or None if capture/detection failed entirely.
        failure: True when both reading attempts failed; False on a successful reading.
    """
    config = _load_config()
    topic = config.get("ntfy_topic", "").strip()

    if not topic or topic.startswith("https://ntfy.sh/your-secret"):
        # Not configured — skip silently.
        return

    low_threshold = float(config.get("low_threshold", DEFAULT_LOW_THRESHOLD))
    warn_thresholds = [float(t) for t in config.get("warn_thresholds", DEFAULT_WARN_THRESHOLDS)]

    if failure:
        title = "Oil tank - detection failed"
        message = "Could not read the sight glass after two attempts. Check the camera."
        _post(topic, title, message)
        return

    # Successful reading — alert if level is at or below any configured threshold.
    if result is None:
        return

    pct = result.get("percentage", 0.0)
    label = result.get("level_label", "?")
    conf = result.get("confidence", 0.0)

    notified = _load_notified()
    # Reset any thresholds the level has risen back above.
    notified = {t for t in notified if pct <= t}

    fired_threshold = None
    if pct <= low_threshold:
        if low_threshold not in notified:
            title = "Oil tank low"
            message = f"Level: {label} ({pct * 100:.1f}%) - conf {conf:.2f}"
            _post(topic, title, message)
            fired_threshold = low_threshold
    else:
        for threshold in sorted(warn_thresholds, reverse=True):
            if pct <= threshold:
                if threshold not in notified:
                    title = f"Oil tank at {threshold * 100:.0f}%"
                    message = f"Level: {label} ({pct * 100:.1f}%) - conf {conf:.2f}"
                    _post(topic, title, message)
                    fired_threshold = threshold
                break

    if fired_threshold is not None:
        notified.add(fired_threshold)
    _save_notified(notified)
