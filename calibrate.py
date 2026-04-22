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
    "tube_left_x": None,
    "tube_right_x": None,
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
    tube_left_x: int | None = None,
    tube_right_x: int | None = None,
) -> np.ndarray:
    """Return annotated copy of img_bgr with rulers, tube box, and HSV mask."""
    out = img_bgr.copy()
    h, w = out.shape[:2]

    left_x = tube_left_x if tube_left_x is not None else 0
    right_x = tube_right_x if tube_right_x is not None else w - 1

    # HSV mask — detected pixels painted red.
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(hsv_lower, dtype=np.uint8),
        np.array(hsv_upper, dtype=np.uint8),
    )
    out[mask > 0] = (0, 0, 255)

    # Y-axis ruler: tick + label every 50 px along the left edge.
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

    # X-axis ruler: tick + label every 50 px along the bottom edge.
    for x in range(0, w, 50):
        cv2.line(out, (x, h - 1), (x, h - 18), (255, 255, 255), 1)
        label_x = min(x + 2, w - 35)
        cv2.putText(
            out, str(x), (label_x, h - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.30, (0, 0, 0), 2,
        )
        cv2.putText(
            out, str(x), (label_x, h - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.30, (255, 255, 255), 1,
        )

    # Tube extent box (y extents + x bounds).
    cv2.rectangle(out, (left_x, tube_top_y), (right_x, tube_bottom_y), (0, 255, 0), 2)

    # Labels for y extents.
    for y, tag in ((tube_top_y, f"F  tube_top_y={tube_top_y}"),
                   (tube_bottom_y, f"E  tube_bottom_y={tube_bottom_y}")):
        text_y = max(y - 6, 12)
        cv2.putText(out, tag, (left_x + 5, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3)
        cv2.putText(out, tag, (left_x + 5, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)

    # Labels for x extents (drawn just inside the box, near the top).
    for x, tag in ((left_x, f"L={left_x}"), (right_x, f"R={right_x}")):
        text_x = max(x - 5, 2) if x == right_x else x + 5
        cv2.putText(out, tag, (text_x, tube_top_y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
        cv2.putText(out, tag, (text_x, tube_top_y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

    return out


def _prompt_int(prompt: str, default: int) -> int:
    val = input(f"  {prompt} [{default}]: ").strip()
    return int(val) if val else default


def _prompt_optional_int(prompt: str, default: int | None) -> int | None:
    default_str = str(default) if default is not None else "none"
    val = input(f"  {prompt} [{default_str}] (Enter to keep, 'none' to clear): ").strip().lower()
    if val in ("", ):
        return default
    if val == "none":
        return None
    return int(val)


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
        cal.get("tube_left_x"),
        cal.get("tube_right_x"),
    )
    cv2.imwrite(str(ruler_path), ruler_img)

    print(f"\nRuler image saved: {ruler_path}")
    print("Green box = current tube region.  Red pixels = current yellow mask.")
    print("Y-axis ruler runs along the left edge; X-axis ruler along the bottom.")
    print("SCP to your machine to view:")
    print(_scp_hint(ruler_path))
    input("\nPress Enter when ready to enter calibration values...")

    # -----------------------------------------------------------------------
    # Phase 2: Collect values interactively.
    # -----------------------------------------------------------------------
    print("\nTube extents (y=0 is top of frame, larger y is lower):")
    tube_top_y    = _prompt_int("tube_top_y   (Full mark — smaller number)", cal["tube_top_y"])
    tube_bottom_y = _prompt_int("tube_bottom_y (Empty mark — larger number)", cal["tube_bottom_y"])

    print("\nTube horizontal bounds (x=0 is left edge of frame):")
    print("  Restrict detection to the tube column range to ignore background objects.")
    print("  Leave as 'none' to search the full image width.")
    tube_left_x  = _prompt_optional_int("tube_left_x  (left edge of tube)", cal.get("tube_left_x"))
    tube_right_x = _prompt_optional_int("tube_right_x (right edge of tube)", cal.get("tube_right_x"))

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
        "tube_left_x": tube_left_x,
        "tube_right_x": tube_right_x,
    }

    # Generate validation image with new settings.
    val_path = IMAGES_DIR / "calibration_validation.jpg"
    val_img = _make_ruler_image(
        img,
        tube_top_y,
        tube_bottom_y,
        new_cal["hsv_lower"],
        new_cal["hsv_upper"],
        tube_left_x,
        tube_right_x,
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
