"""
TokioAI Drone Vision — Visual servoing from entity camera.

Uses Tokio's main camera to visually track and control the Tello drone.
The entity sees the drone flying in the room, estimates its position,
and sends RC commands to play with it autonomously.

Flow:
    1. User shows drone to camera → Tokio registers its visual profile
    2. Drone takes off → Tokio detects it in frame via color/motion/template
    3. Tokio estimates distance from apparent size (known Tello = 18cm)
    4. Tokio sends RC commands via drone proxy to control it
    5. AI brain sees the drone and comments on the action

Modes:
    - register: Learn the drone's appearance
    - track: Passive tracking (no control)
    - come_to_me: Drone approaches the camera
    - hover: Keep drone centered in frame
    - dance: Random moves, Tokio reacts
    - patrol: Drone sweeps left-right in front of camera
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import cv2
import numpy as np

try:
    import requests as _requests
except ImportError:
    _requests = None

logger = logging.getLogger("drone.vision")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TELLO_SIZE_CM = 18.0  # Tello width tip-to-tip (cm)
TELLO_BODY_CM = 14.0  # Tello body width without rotors

# Approximate focal length for a typical USB webcam at 640x480
# f = (apparent_px * distance_cm) / real_size_cm
# At 100cm, Tello (~18cm) appears ~85px wide → f ≈ 472
DEFAULT_FOCAL_LENGTH = 472.0

# Drone proxy API
DRONE_PROXY_URL = "http://127.0.0.1:5001"


class DronePlayMode(Enum):
    IDLE = "idle"
    REGISTER = "register"
    TRACK = "track"
    COME_TO_ME = "come_to_me"
    HOVER = "hover"
    DANCE = "dance"
    PATROL = "patrol"


@dataclass
class DroneVisualState:
    detected: bool = False
    center_x: int = 0       # pixel X in frame
    center_y: int = 0       # pixel Y in frame
    bbox: tuple = (0, 0, 0, 0)  # x1, y1, x2, y2
    apparent_width: int = 0  # pixels
    distance_cm: float = 0.0
    confidence: float = 0.0
    method: str = ""
    frame_w: int = 640
    frame_h: int = 480
    timestamp: float = 0.0

    @property
    def offset_x(self) -> float:
        """Horizontal offset from center, -1.0 (left) to 1.0 (right)."""
        if self.frame_w == 0:
            return 0.0
        return (self.center_x - self.frame_w / 2) / (self.frame_w / 2)

    @property
    def offset_y(self) -> float:
        """Vertical offset from center, -1.0 (top) to 1.0 (bottom)."""
        if self.frame_h == 0:
            return 0.0
        return (self.center_y - self.frame_h / 2) / (self.frame_h / 2)


# ---------------------------------------------------------------------------
# Drone Visual Detector
# ---------------------------------------------------------------------------

class KalmanTracker:
    """Kalman filter for smooth drone position tracking and prediction."""

    def __init__(self):
        # State: [cx, cy, vx, vy] — position + velocity
        self._kf = cv2.KalmanFilter(4, 2)
        self._kf.measurementMatrix = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], np.float32)
        self._kf.transitionMatrix = np.array([
            [1, 0, 1, 0],
            [0, 1, 0, 1],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], np.float32)
        self._kf.processNoiseCov = np.eye(4, dtype=np.float32) * 0.03
        self._kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 0.5
        self._initialized = False
        self._frames_without_measurement = 0
        self._max_predict_frames = 15  # predict up to 15 frames without measurement

    def update(self, cx: int, cy: int) -> tuple[int, int]:
        """Update with measurement, return smoothed position."""
        measurement = np.array([[np.float32(cx)], [np.float32(cy)]])
        if not self._initialized:
            self._kf.statePre = np.array([[cx], [cy], [0], [0]], np.float32)
            self._kf.statePost = np.array([[cx], [cy], [0], [0]], np.float32)
            self._initialized = True
        self._kf.correct(measurement)
        predicted = self._kf.predict()
        self._frames_without_measurement = 0
        return int(predicted[0][0]), int(predicted[1][0])

    def predict(self) -> Optional[tuple[int, int]]:
        """Predict next position without measurement (drone temporarily lost)."""
        if not self._initialized:
            return None
        self._frames_without_measurement += 1
        if self._frames_without_measurement > self._max_predict_frames:
            return None  # too long without measurement, stop predicting
        predicted = self._kf.predict()
        return int(predicted[0][0]), int(predicted[1][0])

    def reset(self):
        self._initialized = False
        self._frames_without_measurement = 0


class DroneDetector:
    """Detect the Tello drone in a frame using multiple methods."""

    def __init__(self):
        self._bg_sub = cv2.createBackgroundSubtractorMOG2(
            history=300, varThreshold=50, detectShadows=False
        )
        self._bg_reference: Optional[np.ndarray] = None  # background without drone
        self._bg_led_mask: Optional[np.ndarray] = None  # static LEDs in background (to exclude)
        self._template: Optional[np.ndarray] = None
        self._template_gray: Optional[np.ndarray] = None
        self._color_profile: Optional[dict] = None
        self._registered = False
        self._warmup_frames = 0
        self._kalman = KalmanTracker()

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self, frame: np.ndarray, bbox: tuple) -> bool:
        """Register the drone's appearance from a bounding box region."""
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 - x1 < 20 or y2 - y1 < 20:
            return False

        roi = frame[y1:y2, x1:x2]
        self._template = roi.copy()
        self._template_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # Extract color profile
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        self._color_profile = {
            "h_mean": float(np.mean(hsv[:, :, 0])),
            "s_mean": float(np.mean(hsv[:, :, 1])),
            "v_mean": float(np.mean(hsv[:, :, 2])),
            "h_std": float(np.std(hsv[:, :, 0])),
            "s_std": float(np.std(hsv[:, :, 1])),
            "v_std": float(np.std(hsv[:, :, 2])),
        }
        self._registered = True
        logger.info(f"Drone registered: {x2-x1}x{y2-y1}px, color profile: S={self._color_profile['s_mean']:.0f} V={self._color_profile['v_mean']:.0f}")
        return True

    def capture_background(self, frame: np.ndarray):
        """Capture a background frame (without drone) for difference-based detection.
        Also captures static LED positions (lab strips etc) to exclude from LED detection."""
        self._bg_reference = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        cv2.GaussianBlur(self._bg_reference, (21, 21), 0, dst=self._bg_reference)

        # Capture static LED mask — any bright colored points in the background
        # These are lab LED strips, indicator lights, etc — NOT the drone
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(hsv, (35, 100, 150), (85, 255, 255))
        red1 = cv2.inRange(hsv, (0, 100, 150), (15, 255, 255))
        red2 = cv2.inRange(hsv, (165, 100, 150), (180, 255, 255))
        blue = cv2.inRange(hsv, (100, 100, 150), (130, 255, 255))
        static_leds = cv2.bitwise_or(green, cv2.bitwise_or(cv2.bitwise_or(red1, red2), blue))
        # Dilate generously so nearby pixels are also excluded
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        self._bg_led_mask = cv2.dilate(static_leds, kernel, iterations=2)

        logger.info("Background reference + static LED mask captured")

    def auto_register(self, frame: np.ndarray) -> Optional[tuple]:
        """Detect the drone by difference from background reference."""
        h, w = frame.shape[:2]

        if self._bg_reference is not None:
            # Difference-based: what changed from the background = the drone
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            cv2.GaussianBlur(gray, (21, 21), 0, dst=gray)
            diff = cv2.absdiff(gray, self._bg_reference)
            diff_u8 = np.clip(diff, 0, 255).astype(np.uint8)
            _, mask = cv2.threshold(diff_u8, 25, 255, cv2.THRESH_BINARY)
        else:
            # Fallback: motion-based detection from background subtractor
            fg_mask = self._bg_sub.apply(frame)
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        min_area = (w * h) * 0.005
        max_area = (w * h) * 0.3
        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if min_area < area < max_area:
                x, y, bw, bh = cv2.boundingRect(c)
                aspect = bw / bh if bh > 0 else 0
                if 0.3 < aspect < 3.0:
                    candidates.append((area, x, y, bw, bh))

        if not candidates:
            return None

        best = max(candidates, key=lambda c: c[0])
        _, x, y, bw, bh = best
        margin = 10
        bbox = (x - margin, y - margin, x + bw + margin, y + bh + margin)
        return bbox

    def detect(self, frame: np.ndarray, person_boxes: list = None) -> Optional[DroneVisualState]:
        """Detect the drone in a frame. Returns DroneVisualState or None."""
        self._warmup_frames += 1
        if self._warmup_frames < 10:
            self._bg_sub.apply(frame)
            return None

        h, w = frame.shape[:2]
        results = []

        # Method 1: LED detection with static LED exclusion + person exclusion
        # When flying, drone LEDs are ON — subtract known static LEDs from lab
        if self._registered:
            led_result = self._detect_drone_led(frame, w, h, person_boxes or [])
            if led_result:
                results.append(led_result)

        # Method 2: Background diff (exclude persons) — fallback
        if self._registered and not results:
            bg_result = self._detect_bg_diff(frame, w, h, person_boxes or [])
            if bg_result:
                results.append(bg_result)

        # Method 3: Template matching (if registered)
        if self._registered and self._template_gray is not None:
            template_result = self._detect_template(frame, w, h)
            if template_result:
                results.append(template_result)

        if not results:
            # No detection — try Kalman prediction for a few frames
            predicted = self._kalman.predict()
            if predicted:
                px, py = predicted
                if 0 < px < w and 0 < py < h:
                    return DroneVisualState(
                        detected=True,
                        center_x=px, center_y=py,
                        bbox=(px - 40, py - 30, px + 40, py + 30),
                        apparent_width=80,
                        distance_cm=0,
                        confidence=0.2,  # low confidence = prediction only
                        method="kalman_predict",
                        frame_w=w, frame_h=h,
                        timestamp=time.time(),
                    )
            return None

        # Pick the best result (highest confidence)
        best = max(results, key=lambda r: r.confidence)

        # Smooth position with Kalman filter
        smoothed_cx, smoothed_cy = self._kalman.update(best.center_x, best.center_y)
        best.center_x = smoothed_cx
        best.center_y = smoothed_cy

        return best

    def _detect_led(self, frame: np.ndarray, w: int, h: int) -> Optional[DroneVisualState]:
        """Detect Tello drone by its LED lights (green front, red/blue status).

        Tello LEDs are tiny, very bright, saturated points — NOT ambient LED strips.
        Higher V threshold and strict size limits to avoid false positives from
        decorative LED strips in the lab.
        """
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Green LED (front of Tello) — H: 35-85, very high S and V (tiny bright point)
        green_mask = cv2.inRange(hsv, (35, 130, 200), (85, 255, 255))
        # Red/yellow LED (status) — H: 0-15 or 165-180
        red_mask1 = cv2.inRange(hsv, (0, 130, 200), (15, 255, 255))
        red_mask2 = cv2.inRange(hsv, (165, 130, 200), (180, 255, 255))
        # Blue LED (some states)
        blue_mask = cv2.inRange(hsv, (100, 130, 200), (130, 255, 255))

        red_mask = cv2.bitwise_or(red_mask1, red_mask2)
        led_mask = cv2.bitwise_or(green_mask, cv2.bitwise_or(red_mask, blue_mask))

        # Small dilate to connect very close LED points (not huge kernel)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        led_mask = cv2.dilate(led_mask, kernel, iterations=1)

        contours, _ = cv2.findContours(led_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Tello LEDs are tiny points — max ~150px area even at close range
        # Lab LED strips are 190-320px area, must be excluded
        min_area = 15
        max_area = 150
        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if min_area < area < max_area:
                x, y, bw, bh = cv2.boundingRect(c)
                # LED should be compact, not a long strip (aspect ratio close to 1)
                aspect = max(bw, bh) / max(1, min(bw, bh))
                if aspect < 5.0 and bw > 2 and bh > 2:
                    candidates.append((area, x, y, bw, bh))

        if not candidates:
            return None

        # If multiple LED clusters are close together, that's likely the drone
        # Group nearby clusters (within 100px)
        if len(candidates) >= 2:
            # Try to find two clusters close together (green + status LED)
            candidates.sort(key=lambda c: c[0], reverse=True)
            for i in range(len(candidates)):
                for j in range(i + 1, len(candidates)):
                    ci = candidates[i]
                    cj = candidates[j]
                    cx_i = ci[1] + ci[3] // 2
                    cy_i = ci[2] + ci[4] // 2
                    cx_j = cj[1] + cj[3] // 2
                    cy_j = cj[2] + cj[4] // 2
                    dist = math.sqrt((cx_i - cx_j) ** 2 + (cy_i - cy_j) ** 2)
                    if dist < 60:  # two LEDs within 60px = drone (Tello LEDs are close together)
                        # Bounding box around both LEDs
                        x1 = min(ci[1], cj[1])
                        y1 = min(ci[2], cj[2])
                        x2 = max(ci[1] + ci[3], cj[1] + cj[3])
                        y2 = max(ci[2] + ci[4], cj[2] + cj[4])
                        # Expand to approximate drone body
                        pad = max(x2 - x1, y2 - y1)
                        x1 = max(0, x1 - pad)
                        y1 = max(0, y1 - pad // 2)
                        x2 = min(w, x2 + pad)
                        y2 = min(h, y2 + pad // 2)
                        cx = (x1 + x2) // 2
                        cy = (y1 + y2) // 2
                        return DroneVisualState(
                            detected=True,
                            center_x=cx, center_y=cy,
                            bbox=(x1, y1, x2, y2),
                            apparent_width=x2 - x1,
                            distance_cm=0,
                            confidence=0.75,
                            method="led_pair",
                            frame_w=w, frame_h=h,
                            timestamp=time.time(),
                        )

        # Single LED — still useful but lower confidence
        best = max(candidates, key=lambda c: c[0])
        _, x, y, bw, bh = best
        # Expand around LED to approximate drone size
        pad = max(40, bw * 2)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad // 2)
        x2 = min(w, x + bw + pad)
        y2 = min(h, y + bh + pad // 2)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        return DroneVisualState(
            detected=True,
            center_x=cx, center_y=cy,
            bbox=(x1, y1, x2, y2),
            apparent_width=x2 - x1,
            distance_cm=0,
            confidence=0.45,
            method="led_single",
            frame_w=w, frame_h=h,
            timestamp=time.time(),
        )

    def _detect_drone_led(self, frame: np.ndarray, w: int, h: int,
                          person_boxes: list) -> Optional[DroneVisualState]:
        """Detect drone LEDs, excluding static LEDs captured during registration.

        Static lab LEDs (strips, indicators) were captured in self._bg_led_mask.
        Person regions from Hailo are also excluded.
        Only NEW LEDs not in the background = drone.
        """
        if self._bg_led_mask is None:
            return None

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Detect all bright colored points (same ranges as background capture)
        green = cv2.inRange(hsv, (35, 100, 150), (85, 255, 255))
        red1 = cv2.inRange(hsv, (0, 100, 150), (15, 255, 255))
        red2 = cv2.inRange(hsv, (165, 100, 150), (180, 255, 255))
        blue = cv2.inRange(hsv, (100, 100, 150), (130, 255, 255))
        all_leds = cv2.bitwise_or(green, cv2.bitwise_or(cv2.bitwise_or(red1, red2), blue))

        # SUBTRACT static LEDs from background — only NEW LEDs remain
        drone_leds = cv2.bitwise_and(all_leds, cv2.bitwise_not(self._bg_led_mask))

        # Exclude person regions
        for (x1, y1, x2, y2) in person_boxes:
            margin = int(max(x2 - x1, y2 - y1) * 0.1)
            drone_leds[max(0, y1 - margin):min(h, y2 + margin),
                       max(0, x1 - margin):min(w, x2 + margin)] = 0

        # Small dilate to connect nearby LED points
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        drone_leds = cv2.dilate(drone_leds, kernel, iterations=1)

        contours, _ = cv2.findContours(drone_leds, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Filter by size — drone LED clusters are small
        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if 15 < area < 2000:
                x, y, bw, bh = cv2.boundingRect(c)
                if bw > 2 and bh > 2:
                    candidates.append((area, x, y, bw, bh))

        if not candidates:
            return None

        # Best = largest LED cluster
        best = max(candidates, key=lambda c: c[0])
        _, x, y, bw, bh = best
        # Expand to approximate drone body
        pad = max(50, bw * 2, bh * 2)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad // 2)
        x2 = min(w, x + bw + pad)
        y2 = min(h, y + bh + pad // 2)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        conf = 0.85 if len(candidates) >= 2 else 0.6

        return DroneVisualState(
            detected=True,
            center_x=cx, center_y=cy,
            bbox=(x1, y1, x2, y2),
            apparent_width=x2 - x1,
            distance_cm=0,
            confidence=conf,
            method="drone_led",
            frame_w=w, frame_h=h,
            timestamp=time.time(),
        )

    def _detect_bg_diff(self, frame: np.ndarray, w: int, h: int,
                        person_boxes: list) -> Optional[DroneVisualState]:
        """Detect drone by difference from registered background.

        Uses the fixed background reference captured during registration.
        Masks out person regions detected by Hailo so we only see the drone.
        """
        if self._bg_reference is None:
            return None

        # Difference from fixed background
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        cv2.GaussianBlur(gray, (21, 21), 0, dst=gray)
        diff = cv2.absdiff(gray, self._bg_reference)
        diff_u8 = np.clip(diff, 0, 255).astype(np.uint8)
        _, fg_mask = cv2.threshold(diff_u8, 30, 255, cv2.THRESH_BINARY)

        # BLACK OUT person regions from Hailo — they are NOT the drone
        for (x1, y1, x2, y2) in person_boxes:
            # Add margin around person to cover arms/hands
            margin = int(max(x2 - x1, y2 - y1) * 0.15)
            px1 = max(0, x1 - margin)
            py1 = max(0, y1 - margin)
            px2 = min(w, x2 + margin)
            py2 = min(h, y2 + margin)
            fg_mask[py1:py2, px1:px2] = 0

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Drone-sized contours only
        frame_area = w * h
        min_area = frame_area * 0.003   # 0.3% of frame
        max_area = frame_area * 0.12    # 12% — drone is small

        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if min_area < area < max_area:
                x, y, bw, bh = cv2.boundingRect(c)
                aspect = bw / bh if bh > 0 else 0
                if 0.4 < aspect < 3.5:
                    candidates.append((area, x, y, bw, bh))

        if not candidates:
            return None

        best = max(candidates, key=lambda c: c[0])
        area, x, y, bw, bh = best
        cx = x + bw // 2
        cy = y + bh // 2
        conf = min(0.8, area / (frame_area * 0.04))

        return DroneVisualState(
            detected=True,
            center_x=cx, center_y=cy,
            bbox=(x, y, x + bw, y + bh),
            apparent_width=bw,
            distance_cm=0,
            confidence=conf,
            method="bg_diff",
            frame_w=w, frame_h=h,
            timestamp=time.time(),
        )

    def _detect_template(self, frame: np.ndarray, w: int, h: int) -> Optional[DroneVisualState]:
        """Detect drone via multi-scale template matching."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        th, tw = self._template_gray.shape[:2]
        best_val = 0
        best_loc = None
        best_scale = 1.0

        for scale in [0.25, 0.35, 0.5, 0.7, 1.0, 1.3, 1.6, 2.0, 2.5]:
            new_w = int(tw * scale)
            new_h = int(th * scale)
            if new_w >= w or new_h >= h or new_w < 15 or new_h < 15:
                continue
            resized = cv2.resize(self._template_gray, (new_w, new_h))
            result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        if best_val < 0.3 or best_loc is None:
            return None

        x, y = best_loc
        sw = int(tw * best_scale)
        sh = int(th * best_scale)

        return DroneVisualState(
            detected=True,
            center_x=x + sw // 2,
            center_y=y + sh // 2,
            bbox=(x, y, x + sw, y + sh),
            apparent_width=sw,
            distance_cm=0,  # calculated later
            confidence=best_val,
            method="template",
            frame_w=w,
            frame_h=h,
            timestamp=time.time(),
        )

    def _find_best_contour(self, mask: np.ndarray, w: int, h: int,
                            method: str) -> Optional[DroneVisualState]:
        """Find the best drone-shaped contour in a mask."""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        min_area = (w * h) * 0.002
        max_area = (w * h) * 0.25

        candidates = []
        for c in contours:
            area = cv2.contourArea(c)
            if min_area < area < max_area:
                x, y, bw, bh = cv2.boundingRect(c)
                aspect = bw / bh if bh > 0 else 0
                if 0.3 < aspect < 3.0:
                    candidates.append((area, x, y, bw, bh))

        if not candidates:
            return None

        best = max(candidates, key=lambda c: c[0])
        area, x, y, bw, bh = best
        cx = x + bw // 2
        cy = y + bh // 2
        conf = min(1.0, area / (w * h * 0.04))

        return DroneVisualState(
            detected=True,
            center_x=cx, center_y=cy,
            bbox=(x, y, x + bw, y + bh),
            apparent_width=bw,
            distance_cm=0,
            confidence=conf,
            method=method,
            frame_w=w, frame_h=h,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Visual Servo Controller
# ---------------------------------------------------------------------------

class VisualServo:
    """PID-like controller that converts visual error to drone RC commands."""

    def __init__(self):
        # Dead zones (don't correct if error is small)
        self.dead_zone_x = 0.08   # 8% of frame
        self.dead_zone_y = 0.08
        self.dead_zone_dist = 15  # cm

        # Gains (conservative — indoor flight)
        self.kp_yaw = 20        # lr strafe for horizontal correction
        self.kp_throttle = 20   # throttle for vertical correction
        self.kp_pitch = 15      # forward/back for distance correction

        # Target distance
        self.target_distance_cm = 120.0  # 1.2m from camera

        # Smoothing
        self._prev_yaw = 0
        self._prev_throttle = 0
        self._prev_pitch = 0
        self._alpha = 0.6  # smoothing factor

    def compute(self, state: DroneVisualState) -> dict:
        """Compute RC commands from visual state.

        Fixed camera looking at drone. Drone assumed facing camera.
        Image coords: Y=0 top, X=0 left.
        offset_x negative = drone is LEFT in image = drone should strafe RIGHT (lr positive)
        offset_y negative = drone is HIGH in image = drone should go DOWN (ud negative)
        """
        # Horizontal error → lr (strafe), not yaw
        # Drone facing camera: its right = camera's left, so negate
        if abs(state.offset_x) > self.dead_zone_x:
            lr = int(-state.offset_x * self.kp_yaw)
        else:
            lr = 0

        # Vertical error → throttle
        # offset_y negative = drone high in image = physically high = should descend
        if abs(state.offset_y) > self.dead_zone_y:
            throttle = int(state.offset_y * self.kp_throttle)
        else:
            throttle = 0

        # Distance error → pitch (forward/back)
        if state.distance_cm > 0:
            dist_error = state.distance_cm - self.target_distance_cm
            if abs(dist_error) > self.dead_zone_dist:
                # Too far (positive error) = drone should come closer = negative fb
                pitch = int(-dist_error / self.target_distance_cm * self.kp_pitch)
            else:
                pitch = 0
        else:
            pitch = 0

        # Clamp
        lr = max(-60, min(60, lr))
        throttle = max(-50, min(50, throttle))
        pitch = max(-40, min(40, pitch))

        # Smooth
        lr = int(self._alpha * lr + (1 - self._alpha) * self._prev_yaw)
        throttle = int(self._alpha * throttle + (1 - self._alpha) * self._prev_throttle)
        pitch = int(self._alpha * pitch + (1 - self._alpha) * self._prev_pitch)
        self._prev_yaw = lr
        self._prev_throttle = throttle
        self._prev_pitch = pitch

        return {"lr": lr, "fb": pitch, "ud": throttle, "yaw": 0}


# ---------------------------------------------------------------------------
# Autonomous Drone Player
# ---------------------------------------------------------------------------

class DroneVisionPlayer:
    """
    Main class: uses entity camera to track and control the drone.

    Integrated into main.py — receives frames from VisionEngine,
    sends commands to drone proxy.
    """

    def __init__(self, drone_proxy_url: str = DRONE_PROXY_URL):
        self._proxy_url = drone_proxy_url
        self._detector = DroneDetector()
        self._servo = VisualServo()
        self._lock = threading.Lock()

        # State
        self._mode = DronePlayMode.IDLE
        self._running = False
        self._visual_state: Optional[DroneVisualState] = None
        self._focal_length = DEFAULT_FOCAL_LENGTH
        self._last_command_time = 0.0
        self._command_interval = 0.5  # send RC every 500ms (avoid rate limiting)
        self._lost_since = 0.0
        self._lost_timeout = 3.0  # auto-land if lost for 3s

        # Dance mode state
        self._dance_step = 0
        self._dance_time = 0.0
        self._dance_moves = [
            {"fb": 30, "yaw": 0, "ud": 0, "lr": 0, "dur": 1.5},
            {"fb": 0, "yaw": 40, "ud": 0, "lr": 0, "dur": 1.0},
            {"fb": -30, "yaw": 0, "ud": 0, "lr": 0, "dur": 1.5},
            {"fb": 0, "yaw": -40, "ud": 0, "lr": 0, "dur": 1.0},
            {"fb": 0, "yaw": 0, "ud": 30, "lr": 0, "dur": 1.0},
            {"fb": 0, "yaw": 0, "ud": -30, "lr": 0, "dur": 1.0},
            {"fb": 0, "yaw": 0, "ud": 0, "lr": 30, "dur": 1.0},
            {"fb": 0, "yaw": 0, "ud": 0, "lr": -30, "dur": 1.0},
        ]

        # Patrol state
        self._patrol_direction = 1
        self._patrol_time = 0.0

        # History for UI
        self._history: deque = deque(maxlen=60)
        self._callback = None  # callback(event_type, message)

        # Registration state
        self._register_frames = 0
        self._register_best_bbox = None
        self._register_best_area = 0

        # Search pattern when drone is lost (slow yaw rotation to find it)
        self._search_pattern_step = 0
        self._search_start_time = 0.0

    @property
    def mode(self) -> DronePlayMode:
        return self._mode

    @property
    def registered(self) -> bool:
        return self._detector.registered

    @property
    def visual_state(self) -> Optional[DroneVisualState]:
        with self._lock:
            return self._visual_state

    def set_callback(self, callback):
        """Set callback: callback(event_type, message)"""
        self._callback = callback

    def set_mode(self, mode: str):
        """Set play mode."""
        try:
            self._mode = DronePlayMode(mode)
            logger.info(f"Drone vision mode: {self._mode.value}")
            if self._callback:
                self._callback("mode_change", f"Modo drone: {self._mode.value}")
        except ValueError:
            logger.warning(f"Unknown mode: {mode}")

    def start(self):
        """Start the vision player background thread."""
        self._running = True
        threading.Thread(target=self._control_loop, daemon=True).start()
        logger.info("DroneVisionPlayer started")

    def stop(self):
        self._running = False

    def process_frame(self, frame: np.ndarray, person_boxes: list = None) -> Optional[DroneVisualState]:
        """Process a frame from the entity camera. Called from main loop."""
        if self._mode == DronePlayMode.IDLE:
            return None

        if self._mode == DronePlayMode.REGISTER:
            return self._process_register(frame)

        # Detect drone (exclude person regions from Hailo)
        state = self._detector.detect(frame, person_boxes=person_boxes or [])

        if state and state.detected:
            # Estimate distance
            if state.apparent_width > 0:
                state.distance_cm = (TELLO_SIZE_CM * self._focal_length) / state.apparent_width

            with self._lock:
                self._visual_state = state
                self._lost_since = 0.0

            self._history.append({
                "t": time.time(),
                "x": state.center_x, "y": state.center_y,
                "dist": round(state.distance_cm, 1),
                "conf": round(state.confidence, 2),
            })
        else:
            now = time.time()
            with self._lock:
                if self._visual_state and self._visual_state.detected:
                    self._lost_since = now
                    self._visual_state = DroneVisualState(detected=False,
                                                          frame_w=frame.shape[1],
                                                          frame_h=frame.shape[0])

        return self._visual_state

    def _process_register(self, frame: np.ndarray) -> Optional[DroneVisualState]:
        """Registration: two-phase background subtraction.

        Phase 1 (frames 1-20): Capture background WITHOUT drone.
        Phase 2 (frames 21-65): User shows drone. Detect by difference.
        """
        self._register_frames += 1
        h, w = frame.shape[:2]

        # Phase 1: Learn background (no drone in frame)
        if self._register_frames <= 20:
            if self._register_frames == 20:
                self._detector.capture_background(frame)
                if self._callback:
                    self._callback("register_phase2", "Fondo capturado. Ahora mostra el drone!")
                logger.info("Background captured, waiting for drone")
            state = DroneVisualState(detected=False, frame_w=w, frame_h=h)
            state.confidence = self._register_frames / 65.0
            state.method = "capturing_background"
            with self._lock:
                self._visual_state = state
            return state

        # Phase 2: Detect drone by difference from background
        bbox = self._detector.auto_register(frame)
        if bbox:
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area > self._register_best_area:
                self._register_best_area = area
                self._register_best_bbox = bbox

        # After 65 frames total, register the best detection
        if self._register_frames >= 65 and self._register_best_bbox:
            success = self._detector.register(frame, self._register_best_bbox)
            if success:
                self._mode = DronePlayMode.TRACK
                if self._callback:
                    self._callback("registered", "Drone registrado! Lo reconozco ahora.")
                logger.info("Drone registered via background subtraction")
                self._register_frames = 0
                self._register_best_bbox = None
                self._register_best_area = 0
                return DroneVisualState(detected=True, frame_w=w, frame_h=h)

        # Show progress
        state = DroneVisualState(detected=False, frame_w=w, frame_h=h)
        if self._register_best_bbox:
            x1, y1, x2, y2 = self._register_best_bbox
            state.detected = True
            state.center_x = (x1 + x2) // 2
            state.center_y = (y1 + y2) // 2
            state.bbox = self._register_best_bbox
            state.confidence = self._register_frames / 65.0
            state.method = "registering"
        with self._lock:
            self._visual_state = state
        return state

    def _control_loop(self):
        """Background loop that sends RC commands based on visual state."""
        while self._running:
            try:
                now = time.time()
                if now - self._last_command_time < self._command_interval:
                    time.sleep(0.05)
                    continue

                with self._lock:
                    state = self._visual_state
                    lost_since = self._lost_since

                if state is None:
                    time.sleep(0.1)
                    continue

                # Check if drone is lost
                if lost_since > 0 and now - lost_since > self._lost_timeout:
                    if self._mode in (DronePlayMode.COME_TO_ME, DronePlayMode.HOVER,
                                       DronePlayMode.DANCE, DronePlayMode.PATROL):
                        lost_time = now - lost_since
                        if lost_time < 8.0:
                            # Search pattern: slow yaw to scan for drone
                            self._search_pattern_step += 1
                            yaw_dir = 25 if (self._search_pattern_step // 20) % 2 == 0 else -25
                            self._send_rc(0, 0, 0, yaw_dir)
                            if self._search_pattern_step % 40 == 1:
                                if self._callback:
                                    self._callback("searching", "Buscando drone... rotando")
                        else:
                            # Too long lost — stop and report
                            self._send_rc(0, 0, 0, 0)
                            self._search_pattern_step = 0
                            if self._callback:
                                self._callback("lost", "Perdí al drone de vista!")
                    continue
                else:
                    self._search_pattern_step = 0

                if not state.detected:
                    time.sleep(0.1)
                    continue

                # Execute mode-specific control
                if self._mode == DronePlayMode.COME_TO_ME:
                    self._control_come_to_me(state)
                elif self._mode == DronePlayMode.HOVER:
                    self._control_hover(state)
                elif self._mode == DronePlayMode.DANCE:
                    self._control_dance(state, now)
                elif self._mode == DronePlayMode.PATROL:
                    self._control_patrol(state, now)

                self._last_command_time = now

            except Exception as e:
                logger.error(f"Control loop error: {e}")
                time.sleep(0.5)

    def _control_come_to_me(self, state: DroneVisualState):
        """Move drone toward the camera."""
        self._servo.target_distance_cm = 80.0  # come close
        rc = self._servo.compute(state)
        self._send_rc(rc["lr"], rc["fb"], rc["ud"], rc["yaw"])

        if state.distance_cm > 0 and state.distance_cm < 90:
            if self._callback:
                self._callback("close", f"Drone a {state.distance_cm:.0f}cm, ya casi llega!")

    def _control_hover(self, state: DroneVisualState):
        """Keep drone centered in frame at target distance."""
        self._servo.target_distance_cm = 120.0
        rc = self._servo.compute(state)
        self._send_rc(rc["lr"], rc["fb"], rc["ud"], rc["yaw"])

    def _control_dance(self, state: DroneVisualState, now: float):
        """Execute dance moves."""
        if now - self._dance_time > self._dance_moves[self._dance_step]["dur"]:
            self._dance_step = (self._dance_step + 1) % len(self._dance_moves)
            self._dance_time = now
            if self._callback and self._dance_step == 0:
                self._callback("dance", "Bailando con el drone!")

        move = self._dance_moves[self._dance_step]
        self._send_rc(move["lr"], move["fb"], move["ud"], move["yaw"])

    def _control_patrol(self, state: DroneVisualState, now: float):
        """Sweep left-right in front of camera."""
        if now - self._patrol_time > 3.0:
            self._patrol_direction *= -1
            self._patrol_time = now

        lr = 25 * self._patrol_direction
        # Also maintain centered vertically and at distance
        self._servo.target_distance_cm = 150.0
        rc = self._servo.compute(state)
        self._send_rc(lr, rc["fb"], rc["ud"], 0)

    def _send_rc(self, lr: int, fb: int, ud: int, yaw: int):
        """Send RC command to drone proxy."""
        if _requests is None:
            return
        try:
            _requests.post(
                f"{self._proxy_url}/drone/command",
                json={
                    "command": "rc",
                    "params": {"lr": lr, "fb": fb, "ud": ud, "yaw": yaw}
                },
                timeout=1,
            )
        except Exception:
            pass

    # --- API methods ---

    def reset(self):
        """Reset all vision state for fresh registration."""
        self._detector = DroneDetector()
        self._mode = DronePlayMode.IDLE
        self._visual_state = None
        self._register_frames = 0
        self._register_best_bbox = None
        self._register_best_area = 0
        self._lost_since = 0.0
        self._search_pattern_step = 0
        logger.info("DroneVisionPlayer reset — ready for fresh registration")

    def get_status(self) -> dict:
        with self._lock:
            vs = self._visual_state
        return {
            "mode": self._mode.value,
            "registered": self._detector.registered,
            "detected": vs.detected if vs else False,
            "center": (vs.center_x, vs.center_y) if vs and vs.detected else None,
            "distance_cm": round(vs.distance_cm, 1) if vs and vs.detected else None,
            "confidence": round(vs.confidence, 2) if vs and vs.detected else 0,
            "method": vs.method if vs else None,
            "offset": (round(vs.offset_x, 2), round(vs.offset_y, 2)) if vs and vs.detected else None,
            "history_len": len(self._history),
        }

    def get_overlay_info(self) -> Optional[dict]:
        """Get info for drawing on entity UI."""
        with self._lock:
            vs = self._visual_state
        if not vs or not vs.detected:
            return None
        return {
            "bbox": vs.bbox,
            "center": (vs.center_x, vs.center_y),
            "distance_cm": vs.distance_cm,
            "confidence": vs.confidence,
            "mode": self._mode.value,
            "offset_x": vs.offset_x,
            "offset_y": vs.offset_y,
        }
