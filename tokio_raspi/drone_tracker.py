"""
TokioAI Drone Visual Tracker — Camera-based safety boundary enforcement.

Uses the Raspi USB camera (+ optional Hailo-8L) to visually track the drone
and enforce a 2m x 2m safe zone. If the drone is detected outside the zone
or lost from view, triggers auto-land via the Safety Proxy.

The tracker runs as a background thread alongside the Safety Proxy.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("drone.tracker")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class TrackerConfig:
    camera_id: int = 0
    frame_width: int = 640
    frame_height: int = 480
    fps: int = 30
    # Safe zone as fraction of frame (center-based)
    # The camera should be positioned so the 2m x 2m area fills most of the frame
    safe_zone_margin: float = 0.10  # 10% margin from edges = 80% center is safe
    # If drone is lost for this many seconds, trigger auto-land
    lost_timeout_s: float = 3.0
    # Minimum detection confidence
    min_confidence: float = 0.3
    # How often to check (seconds)
    check_interval_s: float = 0.1
    # Snapshot directory
    snapshot_dir: str = "/tmp/drone_tracker"


# ---------------------------------------------------------------------------
# Drone detector — color + motion based (no ML needed, fast)
# ---------------------------------------------------------------------------

class DroneDetector:
    """
    Detect the Tello drone in frame using color and motion analysis.

    DJI Tello is white/light gray — we detect it by:
    1. Background subtraction (motion)
    2. Color filtering (white/light objects)
    3. Size filtering (drone-sized blob)
    4. Optional: Hailo YOLO ("airplane" class detection)
    """

    def __init__(self, use_hailo: bool = False):
        self.use_hailo = use_hailo
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=40, detectShadows=False
        )
        self._hailo_engine = None
        if use_hailo:
            self._init_hailo()

    def _init_hailo(self):
        """Try to use Hailo for better detection."""
        try:
            from .vision_engine import VisionEngine
            self._hailo_engine = VisionEngine(camera_id=-1, model="detect")
            logger.info("Hailo engine loaded for drone detection")
        except Exception as e:
            logger.warning(f"Hailo not available, using color/motion: {e}")
            self._hailo_engine = None

    def detect(self, frame: np.ndarray) -> Optional[dict]:
        """
        Detect drone in frame.

        Returns dict with:
            - center: (x, y) pixel coordinates
            - bbox: (x1, y1, x2, y2)
            - area: pixel area
            - confidence: 0-1
            - method: "motion", "hailo", or "color"
        Or None if not detected.
        """
        # Try Hailo first (most accurate)
        if self._hailo_engine:
            result = self._detect_hailo(frame)
            if result:
                return result

        # Motion + color detection (fast, no ML)
        return self._detect_motion_color(frame)

    def _detect_hailo(self, frame: np.ndarray) -> Optional[dict]:
        """Detect using Hailo YOLO — looks for 'airplane' class."""
        if not self._hailo_engine:
            return None
        try:
            from .vision_engine import _hailo_nms_postprocess
            # Would need the hailo pipeline running — skip for now
            return None
        except Exception:
            return None

    def _detect_motion_color(self, frame: np.ndarray) -> Optional[dict]:
        """Detect drone using motion + white color filtering."""
        h, w = frame.shape[:2]

        # 1. Motion detection
        fg_mask = self._bg_subtractor.apply(frame)
        # Clean up noise
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

        # 2. Color filter — Tello is white/light gray
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # White/light objects: low saturation, high value
        white_mask = cv2.inRange(hsv, (0, 0, 180), (180, 60, 255))
        # Light gray
        gray_mask = cv2.inRange(hsv, (0, 0, 140), (180, 40, 200))
        color_mask = cv2.bitwise_or(white_mask, gray_mask)

        # 3. Combine: moving AND white/gray
        combined = cv2.bitwise_and(fg_mask, color_mask)
        # Also try motion-only for when drone is against dark background
        motion_only = fg_mask.copy()

        # Try combined first, fall back to motion-only
        for mask, method in [(combined, "motion+color"), (motion_only, "motion")]:
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue

            # Filter by size — drone should be a reasonable size in frame
            # At 2m distance, Tello (~18cm) is roughly 5-15% of frame width
            min_area = (w * h) * 0.002   # 0.2% of frame
            max_area = (w * h) * 0.25    # 25% of frame

            candidates = []
            for c in contours:
                area = cv2.contourArea(c)
                if min_area < area < max_area:
                    x, y, bw, bh = cv2.boundingRect(c)
                    # Aspect ratio filter — drone is roughly square-ish from above
                    aspect = bw / bh if bh > 0 else 0
                    if 0.3 < aspect < 3.0:
                        candidates.append((c, area, x, y, bw, bh))

            if candidates:
                # Take the largest candidate
                best = max(candidates, key=lambda c: c[1])
                c, area, x, y, bw, bh = best
                cx = x + bw // 2
                cy = y + bh // 2
                confidence = min(1.0, area / (w * h * 0.05))  # normalize

                return {
                    "center": (cx, cy),
                    "bbox": (x, y, x + bw, y + bh),
                    "area": area,
                    "confidence": confidence,
                    "method": method,
                }

        return None


# ---------------------------------------------------------------------------
# Visual Tracker (background thread)
# ---------------------------------------------------------------------------

class DroneVisualTracker:
    """
    Background thread that watches the drone via camera.

    If drone exits safe zone or is lost, calls the safety proxy to auto-land.
    """

    def __init__(self, safety_proxy, config: TrackerConfig = None):
        self.config = config or TrackerConfig()
        self.proxy = safety_proxy
        self.detector = DroneDetector(use_hailo=False)

        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # State
        self._last_detection: Optional[dict] = None
        self._last_detection_time: float = 0
        self._frame: Optional[np.ndarray] = None
        self._annotated_frame: Optional[np.ndarray] = None
        self._tracking_active = False
        self._lost_count = 0
        self._boundary_violations = 0
        self._total_detections = 0

        # Safe zone (pixel coordinates, set when camera opens)
        self._safe_x1 = 0
        self._safe_y1 = 0
        self._safe_x2 = 0
        self._safe_y2 = 0

    def start(self):
        """Start the visual tracker."""
        if self._running:
            return
        # Try V4L2 backend explicitly (required on Pi 5)
        self._cap = cv2.VideoCapture(self.config.camera_id, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            # Fallback: try device path
            self._cap = cv2.VideoCapture(f"/dev/video{self.config.camera_id}", cv2.CAP_V4L2)
        if not self._cap.isOpened():
            # Last try: default backend
            self._cap = cv2.VideoCapture(self.config.camera_id)
        if not self._cap.isOpened():
            logger.error(f"Cannot open camera {self.config.camera_id}")
            return
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.frame_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.frame_height)
        self._cap.set(cv2.CAP_PROP_FPS, self.config.fps)

        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        margin = self.config.safe_zone_margin
        self._safe_x1 = int(w * margin)
        self._safe_y1 = int(h * margin)
        self._safe_x2 = int(w * (1 - margin))
        self._safe_y2 = int(h * (1 - margin))

        logger.info(f"Camera {self.config.camera_id}: {w}x{h}, "
                    f"safe zone: ({self._safe_x1},{self._safe_y1})-({self._safe_x2},{self._safe_y2})")

        self._running = True
        self._thread = threading.Thread(target=self._track_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the tracker."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._cap:
            self._cap.release()
            self._cap = None

    def activate(self):
        """Start actively tracking (call after takeoff)."""
        self._tracking_active = True
        self._lost_count = 0
        logger.info("Visual tracking ACTIVATED")

    def deactivate(self):
        """Stop active tracking (call after land)."""
        self._tracking_active = False
        logger.info("Visual tracking deactivated")

    def _track_loop(self):
        """Background tracking loop."""
        while self._running:
            try:
                self._track_frame()
            except Exception as e:
                logger.error(f"Tracker error: {e}")
            time.sleep(self.config.check_interval_s)

    def _track_frame(self):
        """Process one frame."""
        if not self._cap or not self._cap.isOpened():
            return

        ret, frame = self._cap.read()
        if not ret:
            return

        detection = self.detector.detect(frame)
        annotated = frame.copy()

        # Draw safe zone
        cv2.rectangle(annotated,
                      (self._safe_x1, self._safe_y1),
                      (self._safe_x2, self._safe_y2),
                      (0, 255, 0), 2)
        cv2.putText(annotated, "SAFE ZONE (2m x 2m)", (self._safe_x1, self._safe_y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        now = time.monotonic()

        if detection:
            self._last_detection = detection
            self._last_detection_time = now
            self._total_detections += 1
            self._lost_count = 0

            cx, cy = detection["center"]
            x1, y1, x2, y2 = detection["bbox"]

            # Draw detection
            in_zone = self._is_in_safe_zone(cx, cy)
            color = (0, 255, 0) if in_zone else (0, 0, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"DRONE {detection['confidence']:.0%}"
            cv2.putText(annotated, label, (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            # Cross-hair on drone
            cv2.drawMarker(annotated, (cx, cy), color,
                           cv2.MARKER_CROSS, 20, 2)

            # Check boundary
            if self._tracking_active and not in_zone:
                self._boundary_violations += 1
                logger.warning(f"DRONE OUTSIDE SAFE ZONE at ({cx},{cy})! Violation #{self._boundary_violations}")
                cv2.putText(annotated, "!! BOUNDARY VIOLATION !!", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                if self._boundary_violations >= 3:
                    logger.critical("3 boundary violations — AUTO LANDING")
                    self.proxy.audit.log("vision_tracker", "AUTO_LAND",
                                        {"reason": "boundary_violation", "pos": (cx, cy)},
                                        "Drone left safe zone", blocked=False)
                    self.proxy.execute("land", {}, "vision_tracker")
                    self._tracking_active = False
            else:
                self._boundary_violations = max(0, self._boundary_violations - 1)
        else:
            # No detection
            if self._tracking_active:
                elapsed = now - self._last_detection_time if self._last_detection_time > 0 else 0
                if elapsed > self.config.lost_timeout_s:
                    self._lost_count += 1
                    logger.warning(f"DRONE LOST for {elapsed:.1f}s! Count: {self._lost_count}")
                    cv2.putText(annotated, f"DRONE LOST ({elapsed:.1f}s)", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                    if self._lost_count >= 2:
                        logger.critical("Drone lost too long — AUTO LANDING")
                        self.proxy.audit.log("vision_tracker", "AUTO_LAND",
                                            {"reason": "drone_lost", "seconds": elapsed},
                                            "Drone lost from camera", blocked=False)
                        self.proxy.execute("land", {}, "vision_tracker")
                        self._tracking_active = False

        # Status overlay
        status = "TRACKING" if self._tracking_active else "STANDBY"
        status_color = (0, 255, 255) if self._tracking_active else (128, 128, 128)
        cv2.putText(annotated, f"[{status}]", (self.config.frame_width - 160, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)

        with self._lock:
            self._frame = frame
            self._annotated_frame = annotated

    def _is_in_safe_zone(self, cx: int, cy: int) -> bool:
        """Check if drone center is within the safe zone."""
        return (self._safe_x1 <= cx <= self._safe_x2 and
                self._safe_y1 <= cy <= self._safe_y2)

    def get_frame(self) -> Optional[bytes]:
        """Get latest annotated frame as JPEG."""
        with self._lock:
            if self._annotated_frame is None:
                return None
            _, buf = cv2.imencode(".jpg", self._annotated_frame,
                                  [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buf.tobytes()

    def get_status(self) -> dict:
        """Get tracker status."""
        with self._lock:
            return {
                "running": self._running,
                "tracking_active": self._tracking_active,
                "last_detection": {
                    "center": self._last_detection["center"] if self._last_detection else None,
                    "confidence": self._last_detection["confidence"] if self._last_detection else 0,
                    "method": self._last_detection["method"] if self._last_detection else None,
                    "age_s": round(time.monotonic() - self._last_detection_time, 1) if self._last_detection_time > 0 else None,
                },
                "safe_zone": {
                    "x1": self._safe_x1, "y1": self._safe_y1,
                    "x2": self._safe_x2, "y2": self._safe_y2,
                },
                "total_detections": self._total_detections,
                "boundary_violations": self._boundary_violations,
            }
