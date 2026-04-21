# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

- Raspberry Pi Zero 2W, Raspberry Pi OS Lite 64-bit, headless (no display)
- Python venv at `~/oiltank/venv/` (created with `--system-site-packages` to access system picamera2)
- Always run Python as `venv/bin/python <script>` from `~/oiltank/`

## Running the pipeline

```bash
# Full run (capture → detect → log → notify → purge)
venv/bin/python run.py

# Dry run (prints result only, skips CSV write and notification)
venv/bin/python run.py --dry-run

# Test capture only
venv/bin/python capture.py

# Test detection against a saved image (no camera needed)
python - <<'EOF'
import cv2
from detect import detect_level
img = cv2.imread("images/YOURFILE.jpg")
result = detect_level(img)
print(result["level_label"], result["percentage"], result["confidence"])
cv2.imwrite("images/test_annotated.jpg", result["annotated_image"])
EOF
```

## Architecture

Three-module pipeline, each independently usable:

| Module | Role |
|--------|------|
| `capture.py` | Camera only — returns `(Path, np.ndarray BGR)` |
| `detect.py` | CV only — takes a BGR array, returns a `DetectionResult` dict |
| `run.py` | Orchestrator — calls capture → detect → CSV → notify → purge |

**`detect.py`** reads `~/oiltank/calibration.json` for HSV yellow bounds and tube extent pixel rows (`tube_top_y`, `tube_bottom_y`). Missing or malformed calibration falls back to hardcoded defaults — no crash. Confidence is a 50/50 blend of blob area score and circularity (float is roughly round).

**`run.py`** retries once after 60 s if confidence < 0.5. A `FAILED` row is written to CSV and a failure notification sent only after both attempts fail. Images older than 14 days are purged on every successful run.

**`notify.py`** sends push alerts via ntfy.sh. Fires on detection failure (always) or when `percentage <= low_threshold` (default 0.25). Config in `config.json`. Silent no-op if topic is not configured.

## Key files and directories

- `calibration.json` — HSV bounds + tube pixel extents; missing = defaults apply
- `config.json` — ntfy topic URL and low-level alert threshold; missing = notifications silently disabled
- `logs/readings.csv` — auto-created with header on first write; columns: `timestamp, level_label, percentage, confidence, image_path`
- `logs/cron.log` — stdout/stderr from scheduled cron runs
- `images/` — raw captures (`YYYYMMDD_HHMMSS.jpg`) and annotated pairs (`*_annotated.jpg`)

## Running the dashboard

```bash
# Start the web dashboard (normally managed by systemd)
venv/bin/python web.py
# Access at http://<pi-ip>:8080
```

The dashboard is kept running by the `oiltank-web` systemd service and is accessible remotely via Tailscale.

## Dependencies

- `picamera2` — via system packages (do not install in venv)
- `opencv-python` (`cv2`) — image processing
- `numpy` — array handling
- `flask` — web dashboard (`venv/bin/pip install flask`)
- Standard library only beyond those four
