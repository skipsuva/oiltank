"""
capture.py — Capture a still image from the camera using picamera2.

Returns the saved image path and the raw numpy array for downstream processing.
"""

import sys
import numpy as np
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2


IMAGES_DIR = Path("~/oiltank/images").expanduser()


def capture_image() -> tuple[Path, np.ndarray]:
    """
    Capture a still image and save it to ~/oiltank/images/YYYYMMDD_HHMMSS.jpg.

    Returns:
        (Path, np.ndarray): The saved file path and the captured image as a
                            BGR numpy array (OpenCV-compatible).

    Raises:
        RuntimeError: If the camera fails to initialise or capture.
        OSError: If the image directory cannot be created or the file cannot be saved.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_path = IMAGES_DIR / f"{timestamp}.jpg"

    cam = Picamera2()
    try:
        # Still configuration gives the highest-quality single frame.
        config = cam.create_still_configuration()
        cam.configure(config)
        cam.start()

        # Trigger an autofocus cycle and wait for it to complete before capturing.
        cam.autofocus_cycle()

        # capture_array returns an RGB numpy array by default.
        frame_rgb = cam.capture_array()
    except Exception as exc:
        print(f"ERROR: Camera capture failed: {exc}", file=sys.stderr)
        raise RuntimeError(f"Camera capture failed: {exc}") from exc
    finally:
        # Always release the camera, even if capture raised.
        try:
            cam.stop()
            cam.close()
        except Exception as cleanup_exc:
            print(f"WARNING: Camera cleanup failed: {cleanup_exc}", file=sys.stderr)

    # Convert RGB → BGR so the array is compatible with OpenCV conventions.
    import cv2
    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    try:
        cv2.imwrite(str(image_path), frame_bgr)
    except Exception as exc:
        print(f"ERROR: Failed to save image to {image_path}: {exc}", file=sys.stderr)
        raise OSError(f"Failed to save image: {exc}") from exc

    return image_path, frame_bgr


if __name__ == "__main__":
    path, arr = capture_image()
    print(f"Captured: {path}  shape={arr.shape}")
