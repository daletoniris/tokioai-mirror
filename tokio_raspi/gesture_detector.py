"""
TokioAI Gesture Detector — Hand gesture recognition using OpenCV.

Detects hand gestures using skin color segmentation + convex hull analysis.
No mediapipe needed — works on any platform.

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
"""
from __future__ import annotations

import math
import time
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


# Gesture reactions (what Tokio says)
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
        "Hey! Te veo!",
    ],
}

# Emoji-style icons for gestures
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
    hand_rect: tuple[int, int, int, int]  # x, y, w, h
    contour_area: float


class GestureDetector:
    """Detect hand gestures using skin segmentation + convex hull."""

    def __init__(self):
        self._last_gesture = Gesture.NONE
        self._gesture_start = 0.0
        self._stable_gesture = Gesture.NONE
        self._stable_time = 0.0
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=100, varThreshold=30, detectShadows=False
        )
        self._frame_count = 0

    def detect(self, frame: np.ndarray) -> Optional[GestureResult]:
        """
        Detect hand gesture in frame.

        Returns GestureResult or None if no hand detected.
        """
        self._frame_count += 1

        # Skip first frames (background subtractor learning)
        if self._frame_count < 10:
            self._bg_subtractor.apply(frame)
            return None

        h, w = frame.shape[:2]

        # 1. Skin detection (HSV-based)
        skin_mask = self._detect_skin(frame)

        # 2. Motion mask (helps separate hand from skin-colored background)
        motion_mask = self._bg_subtractor.apply(frame)
        motion_mask = cv2.morphologyEx(motion_mask, cv2.MORPH_OPEN,
                                        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

        # Combine: skin AND (motion OR close to camera = large area)
        combined = cv2.bitwise_and(skin_mask, motion_mask)
        # Also keep pure skin regions that are large enough (static hand)
        combined = cv2.bitwise_or(combined, skin_mask)

        # Clean up
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)

        # 3. Find largest contour (hand)
        contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        # Filter by size
        min_area = w * h * 0.01  # at least 1% of frame
        max_area = w * h * 0.5   # at most 50%
        valid = [(c, cv2.contourArea(c)) for c in contours
                 if min_area < cv2.contourArea(c) < max_area]

        if not valid:
            return None

        # Take largest
        hand_contour, area = max(valid, key=lambda x: x[1])
        x, y, bw, bh = cv2.boundingRect(hand_contour)

        # 4. Convex hull analysis
        hull = cv2.convexHull(hand_contour, returnPoints=False)
        try:
            defects = cv2.convexityDefects(hand_contour, hull)
        except cv2.error:
            return None

        # 5. Count fingers using convexity defects
        finger_count = self._count_fingers(hand_contour, defects, bh)

        # 6. Classify gesture
        gesture = self._classify_gesture(finger_count, hand_contour, x, y, bw, bh)

        # 7. Stabilize (require same gesture for 0.3s)
        now = time.monotonic()
        if gesture != self._last_gesture:
            self._last_gesture = gesture
            self._gesture_start = now

        if now - self._gesture_start > 0.3 and gesture != Gesture.NONE:
            self._stable_gesture = gesture
            self._stable_time = now

        if gesture == Gesture.NONE and now - self._stable_time > 1.0:
            self._stable_gesture = Gesture.NONE

        actual = self._stable_gesture
        confidence = min(1.0, (now - self._gesture_start) / 1.0) if actual != Gesture.NONE else 0.0

        if actual == Gesture.NONE:
            return None

        center = (x + bw // 2, y + bh // 2)
        return GestureResult(
            gesture=actual,
            confidence=confidence,
            finger_count=finger_count,
            hand_center=center,
            hand_rect=(x, y, bw, bh),
            contour_area=area,
        )

    def _detect_skin(self, frame: np.ndarray) -> np.ndarray:
        """Detect skin-colored regions."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)

        # HSV skin range (works for most skin tones)
        mask1 = cv2.inRange(hsv, (0, 30, 60), (20, 150, 255))
        mask2 = cv2.inRange(hsv, (160, 30, 60), (180, 150, 255))
        hsv_mask = cv2.bitwise_or(mask1, mask2)

        # YCrCb skin range (more robust across lighting)
        ycrcb_mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))

        # Combine both methods
        skin = cv2.bitwise_or(hsv_mask, ycrcb_mask)

        # Clean
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, kernel)
        skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, kernel)

        return skin

    def _count_fingers(self, contour, defects, hand_height: int) -> int:
        """Count raised fingers using convexity defects."""
        if defects is None:
            return 0

        finger_count = 0
        for i in range(defects.shape[0]):
            s, e, f, d = defects[i, 0]
            start = tuple(contour[s][0])
            end = tuple(contour[e][0])
            far = tuple(contour[f][0])

            # Distance of the defect point from the hull
            depth = d / 256.0

            # Angle between the two edges at the defect
            a = math.dist(start, far)
            b = math.dist(end, far)
            c = math.dist(start, end)

            if a * b == 0:
                continue

            angle = math.acos(max(-1, min(1, (a * a + b * b - c * c) / (2 * a * b))))
            angle_deg = math.degrees(angle)

            # A finger valley has: angle < 90 degrees, sufficient depth
            min_depth = hand_height * 0.15
            if angle_deg < 90 and depth > min_depth:
                finger_count += 1

        # Defects count valleys between fingers, so fingers = valleys + 1
        # But cap at 5
        if finger_count > 0:
            finger_count = min(5, finger_count + 1)

        return finger_count

    def _classify_gesture(self, fingers: int, contour, x, y, w, h) -> Gesture:
        """Classify gesture from finger count and hand shape."""
        if fingers == 0:
            # Check aspect ratio for thumbs up (tall narrow shape)
            aspect = h / w if w > 0 else 1
            if aspect > 1.8:
                return Gesture.THUMBS_UP
            return Gesture.FIST

        if fingers == 1:
            return Gesture.POINT

        if fingers == 2:
            # Could be peace or horns — check spacing
            hull_points = cv2.convexHull(contour, returnPoints=True)
            if len(hull_points) >= 2:
                # If fingers are spread wide, it's horns
                hull_width = max(p[0][0] for p in hull_points) - min(p[0][0] for p in hull_points)
                if hull_width > w * 0.7:
                    return Gesture.HORNS
            return Gesture.PEACE

        if fingers == 3:
            return Gesture.THREE

        if fingers == 4:
            return Gesture.FOUR

        if fingers == 5:
            return Gesture.OPEN

        return Gesture.NONE
