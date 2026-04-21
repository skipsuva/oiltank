"""
detect.py — Detect the yellow float in a sight-glass image and estimate oil level.

Calibration data lives in ~/oiltank/calibration.json and controls:
  - HSV bounds for yellow masking
  - Pixel y-coordinates for the E and F marks (tube extents)

If the file is missing or malformed, conservative defaults are used so the
system degrades gracefully rather than crashing.
"""

import sys
import json
import math
import numpy as np
import cv2
from pathlib import Path
from typing import TypedDict

CALIBRATION_PATH = Path("~/oiltank/calibration.json").expanduser()

# ---------------------------------------------------------------------------
# Level labels (bottom → top) and the fractional positions they represent.
# ---------------------------------------------------------------------------
LEVEL_LABELS = ["E", "1/4", "1/2", "3/4", "F"]
LEVEL_FRACTIONS = [0.0, 0.25, 0.50, 0.75, 1.0]

# How close (in fraction units) the reading must be to snap to a named label.
LABEL_SNAP_TOLERANCE = 0.125


# ---------------------------------------------------------------------------
# Default calibration — works on a typical 640×480 capture with the tube
# running roughly the full height of the frame.
# ---------------------------------------------------------------------------
DEFAULT_CALIBRATION: dict = {
    # HSV lower/upper bounds for yellow (hue 20–35, high sat & val).
    "hsv_lower": [20, 100, 100],
    "hsv_upper": [35, 255, 255],
    # Pixel row for the Empty mark (bottom of travel).
    "tube_bottom_y": 420,
    # Pixel row for the Full mark (top of travel).
    "tube_top_y": 60,
    # Minimum blob area (px²) to be considered a real float detection.
    "min_blob_area": 200,
}


class DetectionResult(TypedDict):
    y_px: int | None
    level_label: str
    percentage: float
    confidence: float
    annotated_image: np.ndarray


def _load_calibration() -> dict:
    """Load calibration.json, falling back to defaults on any failure."""
    if not CALIBRATION_PATH.exists():
        print(
            f"WARNING: {CALIBRATION_PATH} not found — using default calibration.",
            file=sys.stderr,
        )
        return DEFAULT_CALIBRATION.copy()

    try:
        with CALIBRATION_PATH.open() as fh:
            data = json.load(fh)
        # Fill in any keys that are absent (partial calibration files are OK).
        merged = DEFAULT_CALIBRATION.copy()
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"WARNING: Could not read {CALIBRATION_PATH} ({exc}) — using defaults.",
            file=sys.stderr,
        )
        return DEFAULT_CALIBRATION.copy()


def _fraction_to_label(fraction: float) -> str:
    """Map a 0-1 fill fraction to the nearest named level label."""
    best_label = "UNKNOWN"
    best_dist = float("inf")
    for label, frac in zip(LEVEL_LABELS, LEVEL_FRACTIONS):
        dist = abs(fraction - frac)
        if dist < best_dist:
            best_dist = dist
            best_label = label
    # Only snap if we're within tolerance; otherwise call it by the nearest name anyway
    # (the caller already has the raw percentage for precision).
    return best_label


def _circularity(area: float, perimeter: float) -> float:
    """
    Standard circularity metric: 1.0 = perfect circle, lower = more elongated.
    Returns 0.0 if perimeter is zero to avoid division errors.
    """
    if perimeter == 0:
        return 0.0
    return (4 * math.pi * area) / (perimeter ** 2)


def detect_level(image_bgr: np.ndarray) -> DetectionResult:
    """
    Detect the yellow float in *image_bgr* and return level information.

    Args:
        image_bgr: OpenCV BGR image as a numpy array.

    Returns:
        DetectionResult dict with keys:
            y_px           — centroid row of the largest yellow blob, or None
            level_label    — one of E / 1/4 / 1/2 / 3/4 / F / UNKNOWN
            percentage     — fill fraction 0.0–1.0 (0.0 when unknown)
            confidence     — 0.0–1.0 based on blob area and circularity
            annotated_image — copy of input with detection overlay drawn
    """
    cal = _load_calibration()

    hsv_lower = np.array(cal["hsv_lower"], dtype=np.uint8)
    hsv_upper = np.array(cal["hsv_upper"], dtype=np.uint8)
    tube_bottom_y: int = int(cal["tube_bottom_y"])
    tube_top_y: int = int(cal["tube_top_y"])
    min_blob_area: int = int(cal["min_blob_area"])

    annotated = image_bgr.copy()

    # ------------------------------------------------------------------
    # 1. Convert to HSV and threshold for yellow.
    # ------------------------------------------------------------------
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, hsv_lower, hsv_upper)

    # ------------------------------------------------------------------
    # 2. Morphological cleanup: erode removes noise, dilate restores size.
    # ------------------------------------------------------------------
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.erode(mask, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=2)

    # ------------------------------------------------------------------
    # 3. Find contours and pick the largest blob.
    # ------------------------------------------------------------------
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        _draw_no_detection(annotated, tube_top_y, tube_bottom_y)
        return DetectionResult(
            y_px=None,
            level_label="UNKNOWN",
            percentage=0.0,
            confidence=0.0,
            annotated_image=annotated,
        )

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)

    if area < min_blob_area:
        _draw_no_detection(annotated, tube_top_y, tube_bottom_y)
        return DetectionResult(
            y_px=None,
            level_label="UNKNOWN",
            percentage=0.0,
            confidence=0.0,
            annotated_image=annotated,
        )

    # ------------------------------------------------------------------
    # 4. Compute centroid and level fraction.
    # ------------------------------------------------------------------
    M = cv2.moments(largest)
    if M["m00"] == 0:
        # Degenerate contour — treat as no detection.
        _draw_no_detection(annotated, tube_top_y, tube_bottom_y)
        return DetectionResult(
            y_px=None,
            level_label="UNKNOWN",
            percentage=0.0,
            confidence=0.0,
            annotated_image=annotated,
        )

    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])

    # Fraction of travel: 0.0 at bottom (tube_bottom_y), 1.0 at top (tube_top_y).
    # Note: larger y = lower in frame, so we invert.
    tube_span = tube_bottom_y - tube_top_y  # positive pixels
    if tube_span <= 0:
        print(
            "WARNING: tube_bottom_y <= tube_top_y in calibration — check calibration.json.",
            file=sys.stderr,
        )
        fraction = 0.0
    else:
        fraction = (tube_bottom_y - cy) / tube_span
        fraction = max(0.0, min(1.0, fraction))  # clamp to [0, 1]

    level_label = _fraction_to_label(fraction)

    # ------------------------------------------------------------------
    # 5. Confidence: blend of area score and circularity.
    #    Area score saturates at 10× the minimum blob area.
    #    Circularity score is the raw circularity (0–1).
    # ------------------------------------------------------------------
    perimeter = cv2.arcLength(largest, True)
    circ = _circularity(area, perimeter)

    area_score = min(area / (min_blob_area * 10), 1.0)
    confidence = 0.5 * area_score + 0.5 * circ
    confidence = max(0.0, min(1.0, confidence))

    # ------------------------------------------------------------------
    # 6. Draw annotation overlay.
    # ------------------------------------------------------------------
    _draw_detection(annotated, largest, cx, cy, fraction, level_label, confidence,
                    tube_top_y, tube_bottom_y)

    return DetectionResult(
        y_px=cy,
        level_label=level_label,
        percentage=round(fraction, 4),
        confidence=round(confidence, 4),
        annotated_image=annotated,
    )


# ---------------------------------------------------------------------------
# Annotation helpers
# ---------------------------------------------------------------------------

def _draw_detection(
    img: np.ndarray,
    contour,
    cx: int,
    cy: int,
    fraction: float,
    label: str,
    confidence: float,
    tube_top_y: int,
    tube_bottom_y: int,
) -> None:
    """Draw contour, centroid, level line, and text onto *img* in-place."""
    # Tube extent markers
    h, w = img.shape[:2]
    cv2.line(img, (0, tube_top_y), (w, tube_top_y), (0, 255, 0), 1)
    cv2.line(img, (0, tube_bottom_y), (w, tube_bottom_y), (0, 255, 0), 1)

    # Detected blob outline
    cv2.drawContours(img, [contour], -1, (0, 165, 255), 2)

    # Centroid dot
    cv2.circle(img, (cx, cy), 6, (0, 0, 255), -1)

    # Horizontal level line
    cv2.line(img, (0, cy), (w, cy), (0, 0, 255), 1)

    # Text overlay
    text = f"{label}  {fraction * 100:.1f}%  conf={confidence:.2f}"
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 1)


def _draw_no_detection(img: np.ndarray, tube_top_y: int, tube_bottom_y: int) -> None:
    """Draw a 'no detection' banner onto *img* in-place."""
    h, w = img.shape[:2]
    cv2.line(img, (0, tube_top_y), (w, tube_top_y), (0, 255, 0), 1)
    cv2.line(img, (0, tube_bottom_y), (w, tube_bottom_y), (0, 255, 0), 1)
    text = "NO DETECTION"
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 1)
