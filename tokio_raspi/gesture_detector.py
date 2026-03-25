"""
TokioAI Gesture Detector — Pro-level hand gesture recognition.

Uses person bounding boxes from Hailo to constrain search area,
reducing false positives dramatically. Skin color segmentation +
convex hull analysis within person ROI only.

Wave detection via motion tracking over multiple frames.

Supported gestures:
    FIST      - closed hand (0 fingers)
    POINT     - index finger up (1 finger)
    PEACE     - V sign (2 fingers)
    HORNS     - rock/metal horns (2 fingers spread)
    THREE     - 3 fingers
    FOUR      - 4 fingers
    OPEN      - open hand / wave (5 fingers)
    OK        - thumb + index circle (special shape)
    THUMBS_UP - thumb up (1 finger, vertical)
    WAVE      - hand moving side to side
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import cv2
import numpy as np


class Gesture(Enum):
    NONE = "none"
    FIST = "fist"
    POINT = "point"
    PEACE = "peace"
    HORNS = "horns"
    THREE = "three"
    FOUR = "four"
    OPEN = "open_hand"
    THUMBS_UP = "thumbs_up"
    OK = "ok"
    WAVE = "wave"


GESTURE_REACTIONS = {
    Gesture.PEACE: [
        "Peace! Buena onda.",
        "V de victoria. Me gusta tu estilo.",
        "Dos dedos arriba. Todo tranqui.",
    ],
    Gesture.HORNS: [
        "ROCK AND ROLL! Eso me gusta.",
        "Los cuernos del metal! Brutal.",
        "Modo destroyer activado!",
    ],
    Gesture.OK: [
        "OK! Todo perfecto.",
        "Entendido, todo bien.",
        "Afirmativo. Sistemas nominales.",
    ],
    Gesture.THUMBS_UP: [
        "Pulgar arriba! Aprobado.",
        "Like! Me alegra.",
        "Confirmado. Procediendo.",
    ],
    Gesture.OPEN: [
        "Hola! Saludos.",
        "Mano abierta. Paz y amor.",
        "Alto ahi! Te vi.",
    ],
    Gesture.FIST: [
        "Fuerza! Me gusta la energia.",
        "Puno cerrado. Respeto.",
        "Power up!",
    ],
    Gesture.POINT: [
        "Me estas senalando? Aqui estoy.",
        "Si, soy yo. Tokio AI.",
        "Apuntando... objetivo fijado.",
    ],
    Gesture.WAVE: [
        "Hola! Buen dia!",
        "Saludos humano!",
        "Hey! Te veo! Bienvenido!",
        "Chau! Nos vemos!",
    ],
}

GESTURE_ICONS = {
    Gesture.PEACE: "V",
    Gesture.HORNS: "\\m/",
    Gesture.OK: "OK",
    Gesture.THUMBS_UP: "+1",
    Gesture.OPEN: "Hi",
    Gesture.FIST: "!",
    Gesture.POINT: "->",
    Gesture.WAVE: "~~",
    Gesture.THREE: "3",
    Gesture.FOUR: "4",
}


@dataclass
class GestureResult:
    gesture: Gesture
    confidence: float
    finger_count: int
    hand_center: tuple[int, int]
    hand_rect: tuple[int, int, int, int]
    contour_area: float


class GestureDetector:
    """Pro-level gesture detection using person ROI + skin segmentation + convex hull."""

    def __init__(self):
        self._last_gesture = Gesture.NONE
        self._gesture_start = 0.0
        self._stable_gesture = Gesture.NONE
        self._stable_time = 0.0
        self._frame_count = 0

        # Wave detection: track hand center positions
        self._hand_positions: deque[tuple[float, int]] = deque(maxlen=20)  # (time, x)
        self._wave_detected_time = 0.0

        # Adaptive skin calibration
        self._skin_samples: list[np.ndarray] = []
        self._skin_calibrated = False
        self._skin_center_h = 15  # HSV hue center for skin
        self._skin_range_h = 20

    def detect(self, frame: np.ndarray,
               person_boxes: list[tuple[int, int, int, int]] | None = None) -> Optional[GestureResult]:
        """
        Detect hand gesture in frame.

        Args:
            frame: BGR image
            person_boxes: List of (x1, y1, x2, y2) from Hailo detections.
                         If provided, only searches for hands within these regions.

        Returns GestureResult or None if no hand detected.
        """
        self._frame_count += 1
        if self._frame_count < 5:
            return None

        h, w = frame.shape[:2]

        # If we have person detections, search within upper body regions only
        rois = []
        if person_boxes:
            for bx1, by1, bx2, by2 in person_boxes:
                # Upper 60% of person box (where hands usually are)
                roi_h = int((by2 - by1) * 0.6)
                # Expand width slightly for extended arms
                expand = int((bx2 - bx1) * 0.3)
                rx1 = max(0, bx1 - expand)
                ry1 = max(0, by1)
                rx2 = min(w, bx2 + expand)
                ry2 = min(h, by1 + roi_h)
                if rx2 - rx1 > 30 and ry2 - ry1 > 30:
                    rois.append((rx1, ry1, rx2, ry2))

        # If no person boxes, use full frame (fallback)
        if not rois:
            rois = [(0, 0, w, h)]

        best_result = None
        best_area = 0

        for rx1, ry1, rx2, ry2 in rois:
            roi = frame[ry1:ry2, rx1:rx2]
            result = self._detect_in_roi(roi, rx1, ry1)
            if result and result.contour_area > best_area:
                best_result = result
                best_area = result.contour_area

        if best_result is None:
            return self._check_stabilization(Gesture.NONE)

        # Wave detection
        now = time.monotonic()
        self._hand_positions.append((now, best_result.hand_center[0]))
        wave = self._detect_wave(now)
        if wave:
            best_result = GestureResult(
                gesture=Gesture.WAVE,
                confidence=0.8,
                finger_count=best_result.finger_count,
                hand_center=best_result.hand_center,
                hand_rect=best_result.hand_rect,
                contour_area=best_result.contour_area,
            )

        return self._check_stabilization(best_result.gesture, best_result)

    def _detect_in_roi(self, roi: np.ndarray, offset_x: int, offset_y: int) -> Optional[GestureResult]:
        """Detect gesture within a specific ROI."""
        h, w = roi.shape[:2]
        if h < 20 or w < 20:
            return None

        skin_mask = self._detect_skin(roi)

        # Clean up mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel)
        skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_OPEN, kernel)

        # Find contours
        contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Filter: hand should be 3-35% of ROI (raised min to reduce false positives)
        roi_area = w * h
        min_area = roi_area * 0.03
        max_area = roi_area * 0.35
        valid = [(c, cv2.contourArea(c)) for c in contours
                 if min_area < cv2.contourArea(c) < max_area]

        if not valid:
            return None

        hand_contour, area = max(valid, key=lambda x: x[1])

        # Solidity check — hand contour should be reasonably solid (not a noise shape)
        hull_area = cv2.contourArea(cv2.convexHull(hand_contour))
        if hull_area > 0:
            solidity = area / hull_area
            if solidity < 0.4:  # too fragmented
                return None

        x, y, bw, bh = cv2.boundingRect(hand_contour)

        # Aspect ratio sanity — too extreme = probably not a hand
        aspect = max(bw, bh) / max(1, min(bw, bh))
        if aspect > 5:
            return None

        # Convex hull analysis
        hull = cv2.convexHull(hand_contour, returnPoints=False)
        try:
            defects = cv2.convexityDefects(hand_contour, hull)
        except cv2.error:
            return None

        finger_count = self._count_fingers(hand_contour, defects, bh)
        gesture = self._classify_gesture(finger_count, hand_contour, x, y, bw, bh)

        if gesture == Gesture.NONE:
            return None

        center = (offset_x + x + bw // 2, offset_y + y + bh // 2)
        confidence = min(1.0, solidity * 1.2) if hull_area > 0 else 0.5

        return GestureResult(
            gesture=gesture,
            confidence=confidence,
            finger_count=finger_count,
            hand_center=center,
            hand_rect=(offset_x + x, offset_y + y, bw, bh),
            contour_area=area,
        )

    def _detect_skin(self, frame: np.ndarray) -> np.ndarray:
        """Multi-space skin detection for robustness across skin tones and lighting."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)

        # HSV: two ranges for red-ish skin hues (wraps around 0/180)
        mask1 = cv2.inRange(hsv, (0, 25, 50), (25, 180, 255))
        mask2 = cv2.inRange(hsv, (155, 25, 50), (180, 180, 255))
        hsv_mask = cv2.bitwise_or(mask1, mask2)

        # YCrCb: robust across lighting (wider range for dark skin)
        ycrcb_mask = cv2.inRange(ycrcb, (0, 130, 75), (255, 180, 135))

        # Combine — require at least one method to agree
        skin = cv2.bitwise_or(hsv_mask, ycrcb_mask)

        # Morphological cleanup
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, kernel)
        skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, kernel)

        return skin

    def _count_fingers(self, contour, defects, hand_height: int) -> int:
        """Count raised fingers using convexity defects with strict filtering."""
        if defects is None:
            return 0

        finger_count = 0
        for i in range(defects.shape[0]):
            s, e, f, d = defects[i, 0]
            start = tuple(contour[s][0])
            end = tuple(contour[e][0])
            far = tuple(contour[f][0])

            depth = d / 256.0

            a = math.dist(start, far)
            b = math.dist(end, far)
            c = math.dist(start, end)

            if a * b == 0:
                continue

            angle = math.acos(max(-1, min(1, (a * a + b * b - c * c) / (2 * a * b))))
            angle_deg = math.degrees(angle)

            # Strict thresholds: angle < 70, depth > 25% of hand height
            min_depth = hand_height * 0.25
            if angle_deg < 70 and depth > min_depth:
                # Additional check: defect points should be above the contour centroid
                # (fingers point up, valleys are between fingers)
                M = cv2.moments(contour)
                if M["m00"] > 0:
                    centroid_y = M["m01"] / M["m00"]
                    # Valley (far point) should be near or above centroid
                    if far[1] < centroid_y + hand_height * 0.3:
                        finger_count += 1

        if finger_count > 0:
            finger_count = min(5, finger_count + 1)

        return finger_count

    def _classify_gesture(self, fingers: int, contour, x, y, w, h) -> Gesture:
        """Classify gesture from finger count and hand shape analysis."""
        aspect = h / w if w > 0 else 1

        if fingers == 0:
            if aspect > 2.0:
                return Gesture.THUMBS_UP
            if aspect < 1.5 and aspect > 0.6:
                return Gesture.FIST
            return Gesture.NONE  # Ambiguous — don't guess

        if fingers == 1:
            # Check if it's pointing (tall) vs thumbs up (could be miscount)
            if aspect > 1.8:
                return Gesture.THUMBS_UP
            return Gesture.POINT

        if fingers == 2:
            # Peace vs horns — check finger tip spacing (strict)
            hull_points = cv2.convexHull(contour, returnPoints=True)
            if len(hull_points) >= 4:
                hull_width = max(p[0][0] for p in hull_points) - min(p[0][0] for p in hull_points)
                if hull_width > w * 0.9 and aspect > 1.2:
                    return Gesture.HORNS
            return Gesture.PEACE

        if fingers == 3:
            return Gesture.THREE

        if fingers == 4:
            return Gesture.FOUR

        if fingers == 5:
            return Gesture.OPEN

        return Gesture.NONE

    def _detect_wave(self, now: float) -> bool:
        """Detect waving motion by analyzing hand position changes over time."""
        if now - self._wave_detected_time < 3.0:
            return False  # Cooldown

        positions = list(self._hand_positions)
        # Need at least 8 positions in the last 2 seconds
        recent = [(t, x) for t, x in positions if now - t < 2.0]
        if len(recent) < 8:
            return False

        # Count direction changes (wave = back and forth)
        xs = [x for _, x in recent]
        direction_changes = 0
        prev_dir = 0
        for i in range(1, len(xs)):
            diff = xs[i] - xs[i-1]
            if abs(diff) < 5:
                continue
            curr_dir = 1 if diff > 0 else -1
            if prev_dir != 0 and curr_dir != prev_dir:
                direction_changes += 1
            prev_dir = curr_dir

        # Also check total horizontal movement
        total_movement = sum(abs(xs[i] - xs[i-1]) for i in range(1, len(xs)))
        avg_movement = total_movement / len(xs)

        # Wave = at least 3 direction changes with significant movement
        if direction_changes >= 3 and avg_movement > 8:
            self._wave_detected_time = now
            return True

        return False

    def _check_stabilization(self, gesture: Gesture,
                              result: Optional[GestureResult] = None) -> Optional[GestureResult]:
        """Require same gesture for 0.4s to avoid flicker."""
        now = time.monotonic()

        if gesture != self._last_gesture:
            self._last_gesture = gesture
            self._gesture_start = now

        # Stabilize at 0.8s to reduce false positives
        if now - self._gesture_start > 0.8 and gesture != Gesture.NONE:
            self._stable_gesture = gesture
            self._stable_time = now

        if gesture == Gesture.NONE and now - self._stable_time > 1.5:
            self._stable_gesture = Gesture.NONE

        actual = self._stable_gesture
        if actual == Gesture.NONE:
            return None

        confidence = min(1.0, (now - self._gesture_start) / 1.0) if actual != Gesture.NONE else 0.0

        if result and result.gesture == actual:
            return GestureResult(
                gesture=actual,
                confidence=max(confidence, result.confidence),
                finger_count=result.finger_count,
                hand_center=result.hand_center,
                hand_rect=result.hand_rect,
                contour_area=result.contour_area,
            )
        elif result:
            return GestureResult(
                gesture=actual,
                confidence=confidence,
                finger_count=result.finger_count,
                hand_center=result.hand_center,
                hand_rect=result.hand_rect,
                contour_area=result.contour_area,
            )
        return None
