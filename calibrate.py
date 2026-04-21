"""
calibrate.py — Interactive calibration helper for oiltank detection.

Phase 1: Generates a ruler image with the current HSV mask and tube extent
         lines overlaid. SCP this to your machine to read pixel coordinates.

Phase 2: Prompts for calibration values (press Enter to keep current),
         generates a validation image with the new settings, then optionally
         writes calibration.json.

Usage:
    venv/bin/python calibrate.py [<image_path>]
"""

import sys
import json
import socket
import numpy as np
import cv2
from pathlib import Path

CALIBRATION_PATH = Path("~/oiltank/calibration.json").expanduser()
IMAGES_DIR = Path("~/oiltank/images").expanduser()

DEFAULT_CALIBRATION: dict = {
    "hsv_lower": [20, 100, 100],
    "hsv_upper": [35, 255, 255],
    "tube_bottom_y": 420,
    "tube_top_y": 60,
    "min_blob_area": 200,
}


def _load_calibration() -> dict:
    cal = DEFAULT_CALIBRATION.copy()
    if CALIBRATION_PATH.exists():
        try:
            with CALIBRATION_PATH.open() as fh:
                cal.update(json.load(fh))
        except (json.JSONDecodeError, OSError):
            pass
    return cal


def _make_ruler_image(
    img_bgr: np.ndarray,
    tube_top_y: int,
    tube_bottom_y: int,
    hsv_lower: list,
    hsv_upper: list,
) -> np.ndarray:
    """Return annotated copy of img_bgr with ruler, tube lines, and HSV mask."""
    out = img_bgr.copy()
    h, w = out.shape[:2]

    # HSV mask — detected pixels painted red.
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(hsv_lower, dtype=np.uint8),
        np.array(hsv_upper, dtype=np.uint8),
    )
    out[mask > 0] = (0, 0, 255)

    # Y-axis ruler: tick + label every 50 px.
    for y in range(0, h, 50):
        cv2.line(out, (0, y), (28, y), (255, 255, 255), 1)
        label_y = max(y + 10, 10)
        cv2.putText(
            out, str(y), (2, label_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 0), 2,
        )
        cv2.putText(
            out, str(y), (2, label_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1,
        )

    # Tube extent lines.
    for y, tag in ((tube_top_y, f"F  tube_top_y={tube_top_y}"),
                   (tube_bottom_y, f"E  tube_bottom_y={tube_bottom_y}")):
        cv2.line(out, (0, y), (w, y), (0, 255, 0), 2)
        text_y = max(y - 6, 12)
        cv2.putText(out, tag, (35, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(out, tag, (35, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)

    return out


def _prompt_int(prompt: str, default: int) -> int:
    val = input(f"  {prompt} [{default}]: ").strip()
    return int(val) if val else default


def _scp_hint(path: Path) -> str:
    hostname = socket.gethostname()
    return f"  scp {hostname}:{path} ."


def main() -> None:
    # Resolve image to use.
    if len(sys.argv) > 1:
        img_path = Path(sys.argv[1])
    else:
        candidates = sorted(
            (p for p in IMAGES_DIR.glob("*.jpg")
             if "_annotated" not in p.name
             and "calibration" not in p.name),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            print("No images found in images/. Run capture.py first.", file=sys.stderr)
            sys.exit(1)
        img_path = candidates[-1]

    img = cv2.imread(str(img_path))
    if img is None:
        print(f"ERROR: Cannot read {img_path}", file=sys.stderr)
        sys.exit(1)

    h, w = img.shape[:2]
    print(f"Image: {img_path}  ({w}x{h})")

    cal = _load_calibration()

    # -----------------------------------------------------------------------
    # Phase 1: Generate ruler image with current settings.
    # -----------------------------------------------------------------------
    ruler_path = IMAGES_DIR / "calibration_ruler.jpg"
    ruler_img = _make_ruler_image(
        img,
        cal["tube_top_y"],
        cal["tube_bottom_y"],
        cal["hsv_lower"],
        cal["hsv_upper"],
    )
    cv2.imwrite(str(ruler_path), ruler_img)

    print(f"\nRuler image saved: {ruler_path}")
    print("Green lines = current tube extents.  Red pixels = current yellow mask.")
    print("SCP to your machine to view:")
    print(_scp_hint(ruler_path))
    input("\nPress Enter when ready to enter calibration values...")

    # -----------------------------------------------------------------------
    # Phase 2: Collect values interactively.
    # -----------------------------------------------------------------------
    print("\nTube extents (y=0 is top of frame, larger y is lower):")
    tube_top_y    = _prompt_int("tube_top_y   (Full mark — smaller number)", cal["tube_top_y"])
    tube_bottom_y = _prompt_int("tube_bottom_y (Empty mark — larger number)", cal["tube_bottom_y"])

    print("\nHSV yellow bounds (hue 0-180, saturation/value 0-255):")
    print("  Yellow hue is roughly 20-35. Adjust if the float colour differs.")
    hsv_hue_lo  = _prompt_int("hsv_lower hue", cal["hsv_lower"][0])
    hsv_sat_lo  = _prompt_int("hsv_lower saturation", cal["hsv_lower"][1])
    hsv_val_lo  = _prompt_int("hsv_lower value", cal["hsv_lower"][2])
    hsv_hue_hi  = _prompt_int("hsv_upper hue", cal["hsv_upper"][0])

    print("\nNoise rejection:")
    min_blob_area = _prompt_int("min_blob_area (px²)", cal["min_blob_area"])

    new_cal = {
        "hsv_lower": [hsv_hue_lo, hsv_sat_lo, hsv_val_lo],
        "hsv_upper": [hsv_hue_hi, 255, 255],
        "tube_top_y": tube_top_y,
        "tube_bottom_y": tube_bottom_y,
        "min_blob_area": min_blob_area,
    }

    # Generate validation image with new settings.
    val_path = IMAGES_DIR / "calibration_validation.jpg"
    val_img = _make_ruler_image(
        img,
        tube_top_y,
        tube_bottom_y,
        new_cal["hsv_lower"],
        new_cal["hsv_upper"],
    )
    cv2.imwrite(str(val_path), val_img)

    print(f"\nValidation image saved: {val_path}")
    print("SCP to verify the mask and tube lines look correct:")
    print(_scp_hint(val_path))

    confirm = input("\nSave calibration.json? [Y/n]: ").strip().lower()
    if confirm in ("", "y", "yes"):
        with CALIBRATION_PATH.open("w") as fh:
            json.dump(new_cal, fh, indent=2)
        print(f"Saved: {CALIBRATION_PATH}")
    else:
        print("Calibration not saved.")


if __name__ == "__main__":
    main()
