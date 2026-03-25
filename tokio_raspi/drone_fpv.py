"""
TokioAI Drone FPV — First Person View + Autonomous Tracking.

Receives the Tello's 720p H264 video stream, detects persons,
estimates distances, and generates RC commands to follow a target
while avoiding obstacles.

The drone becomes Tokio's flying eye — a second camera perspective
shown as PiP on the entity screen.

Architecture:
    Tello (UDP 11111 H264) --> drone_fpv.py --> decoded frames
                                    |
                                    +--> person detection (OpenCV HOG)
                                    +--> distance estimation (bbox size)
                                    +--> RC commands --> drone proxy
                                    +--> frames for UI PiP overlay
"""
from __future__ import annotations

import logging
import math
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

try:
    import requests as _requests
except ImportError:
    _requests = None

logger = logging.getLogger("drone.fpv")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TELLO_VIDEO_PORT = 11111
PERSON_REAL_HEIGHT_CM = 170.0  # average person height for distance estimation
FRAME_W, FRAME_H = 960, 720   # Tello 720p stream

# Safety thresholds (percentage of frame height occupied by person bbox)
# Tello hovers at ~80cm height. From this low angle, a person at 2m fills ~65% of frame.
# Wide comfort zone needed — only move forward/back at extremes.
OBSTACLE_CLOSE_PCT = 0.92    # > 92% = person overflowing frame, too close
OBSTACLE_WARN_PCT = 0.85     # > 85% = back up gently
TARGET_IDEAL_PCT = 0.50      # ~50% is sweet spot (~2m at eye level)
TARGET_MIN_PCT = 0.12        # < 12% = too far, approach

# Drone proxy
DRONE_PROXY_URL = "http://127.0.0.1:5001"


@dataclass
class FPVPerson:
    """A person detected in the drone's camera frame."""
    bbox: tuple  # (x1, y1, x2, y2)
    center_x: int
    center_y: int
    height_px: int
    width_px: int
    distance_cm: float  # estimated distance
    frame_pct: float    # percentage of frame height
    is_target: bool = False
    is_obstacle: bool = False
    name: Optional[str] = None


@dataclass
class FPVState:
    """Current state of the FPV system."""
    streaming: bool = False
    persons: list = None  # list of FPVPerson
    target: Optional[FPVPerson] = None
    obstacle_ahead: bool = False
    closest_obstacle_cm: float = 999.0
    frame_count: int = 0
    fps: float = 0.0
    mode: str = "idle"  # idle, follow, explore, hover

    def __post_init__(self):
        if self.persons is None:
            self.persons = []


# ---------------------------------------------------------------------------
# Person Detector (OpenCV HOG — lightweight, no GPU needed)
# ---------------------------------------------------------------------------

class PersonDetector:
    """Detect persons using OpenCV's HOG + SVM detector.

    Lightweight, runs at ~12-15fps on Pi 5 CPU for 720p.
    For higher accuracy, can optionally use a Haar cascade for faces.
    """

    def __init__(self):
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

        # Face cascade for target identification
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._face_cascade = cv2.CascadeClassifier(cascade_path)

        # Focal length estimate for Tello camera (82.6 degree FOV, 960px width)
        # f = (width/2) / tan(FOV/2) = 480 / tan(41.3°) = ~547
        self._focal_length = 547.0

    def detect(self, frame: np.ndarray) -> list[FPVPerson]:
        """Detect persons in frame, estimate distances."""
        h, w = frame.shape[:2]

        # Resize for faster detection
        scale = 0.5
        small = cv2.resize(frame, (int(w * scale), int(h * scale)))

        # HOG person detection
        boxes, weights = self._hog.detectMultiScale(
            small,
            winStride=(8, 8),
            padding=(4, 4),
            scale=1.05,
            useMeanshiftGrouping=True,
        )

        persons = []
        for i, (x, y, bw, bh) in enumerate(boxes):
            # Scale back to original size
            x1 = int(x / scale)
            y1 = int(y / scale)
            x2 = int((x + bw) / scale)
            y2 = int((y + bh) / scale)
            pw = x2 - x1
            ph = y2 - y1

            # Estimate distance from apparent height
            # distance = (real_height * focal_length) / apparent_height
            if ph > 0:
                distance_cm = (PERSON_REAL_HEIGHT_CM * self._focal_length) / ph
            else:
                distance_cm = 999.0

            frame_pct = ph / h

            persons.append(FPVPerson(
                bbox=(x1, y1, x2, y2),
                center_x=(x1 + x2) // 2,
                center_y=(y1 + y2) // 2,
                height_px=ph,
                width_px=pw,
                distance_cm=distance_cm,
                frame_pct=frame_pct,
                is_obstacle=False,  # controller decides this
            ))

        # Sort by distance (closest first)
        persons.sort(key=lambda p: p.distance_cm)

        return persons

    def estimate_focal_length(self, known_height_px: int, known_distance_cm: float):
        """Calibrate focal length from a known measurement."""
        if known_height_px > 0:
            self._focal_length = (known_distance_cm * known_height_px) / PERSON_REAL_HEIGHT_CM
            logger.info(f"Focal length calibrated: {self._focal_length:.1f}")


# ---------------------------------------------------------------------------
# FPV Stream Receiver
# ---------------------------------------------------------------------------

class TelloStream:
    """Receive and decode Tello's H264 video stream."""

    def __init__(self):
        self._socket: Optional[socket.socket] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False
        self._frame_count = 0
        self._fps = 0.0
        self._fps_time = 0.0
        self._fps_count = 0

    @property
    def frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def start(self):
        """Start receiving video stream."""
        self._running = True
        # Use OpenCV VideoCapture with UDP source
        self._cap = cv2.VideoCapture(
            f"udp://0.0.0.0:{TELLO_VIDEO_PORT}?overrun_nonfatal=1",
            cv2.CAP_FFMPEG,
        )
        if not self._cap.isOpened():
            # Fallback: try raw UDP socket approach
            logger.warning("OpenCV UDP capture failed, trying raw socket")
            self._cap = None

        threading.Thread(target=self._receive_loop, daemon=True).start()
        logger.info("FPV stream receiver started")

    def stop(self):
        self._running = False
        if self._cap:
            self._cap.release()
            self._cap = None
        with self._lock:
            self._frame = None

    def _receive_loop(self):
        """Receive and decode frames."""
        self._fps_time = time.time()

        while self._running:
            try:
                if self._cap and self._cap.isOpened():
                    ret, frame = self._cap.read()
                    if ret and frame is not None:
                        with self._lock:
                            self._frame = frame
                        self._frame_count += 1
                        self._update_fps()
                    else:
                        time.sleep(0.01)
                else:
                    time.sleep(0.1)
            except Exception as e:
                logger.error(f"Stream error: {e}")
                time.sleep(0.5)

    def _update_fps(self):
        self._fps_count += 1
        now = time.time()
        elapsed = now - self._fps_time
        if elapsed >= 1.0:
            self._fps = self._fps_count / elapsed
            self._fps_count = 0
            self._fps_time = now


# ---------------------------------------------------------------------------
# FPV Controller — Follow target, avoid obstacles
# ---------------------------------------------------------------------------

class FPVController:
    """Autonomous drone control based on FPV camera person detection.

    Modes:
        follow: Track and follow the target person (stay in front)
        orbit: Circle around the target person keeping distance
        explore: Slow rotation scanning for people
        hover: Hold position (no RC commands)
    """

    def __init__(self, proxy_url: str = DRONE_PROXY_URL):
        self._proxy_url = proxy_url

        # Control gains (responsive for tracking)
        self._kp_yaw = 30       # horizontal centering
        self._kp_throttle = 25  # vertical centering
        self._kp_pitch = 20     # distance control
        self._dead_zone = 0.06  # 6% of frame
        self._min_rc = 8        # minimum RC value to overcome Tello inertia

        # Smoothing (less = more responsive)
        self._prev_yaw = 0
        self._prev_throttle = 0
        self._prev_pitch = 0
        self._alpha = 0.7       # 70% new, 30% old

        # Command rate
        self._last_cmd_time = 0.0
        self._cmd_interval = 0.2  # 200ms between commands (Tello needs frequent RC)

        # State
        self._mode = "idle"
        self._target_lost_since = 0.0
        self._target_lost_timeout = 5.0  # switch to explore after 5s

        # Orbit state
        self._orbit_yaw_speed = 20  # constant yaw during orbit
        self._orbit_strafe_speed = 18  # lateral movement to circle

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str):
        self._mode = mode
        print(f"[FPV-CTL] Mode set: {mode}")

    def process(self, persons: list[FPVPerson], frame_w: int, frame_h: int) -> dict:
        """Process detected persons and generate RC command.

        Returns dict with rc command and status info.
        """
        now = time.time()
        if now - self._last_cmd_time < self._cmd_interval:
            return {"action": "wait"}

        if self._mode == "idle" or self._mode == "hover":
            return {"action": "hover", "rc": {"lr": 0, "fb": 0, "ud": 0, "yaw": 0}}

        # Find target — always pick the closest person as target
        target = None
        obstacles = []
        for p in persons:
            if target is None:
                target = p
                target.is_target = True
            else:
                # Only OTHER persons count as obstacles
                p.is_obstacle = True
                if p.frame_pct > OBSTACLE_CLOSE_PCT:
                    obstacles.append(p)

        # Check for non-target obstacles blocking path
        obstacle_ahead = len(obstacles) > 0
        closest_cm = min((o.distance_cm for o in obstacles), default=999.0)

        if obstacle_ahead and self._mode == "explore":
            # Only stop for obstacles when exploring (not follow/orbit)
            self._send_rc(0, 0, 0, 0)
            self._last_cmd_time = now
            return {
                "action": "obstacle_stop",
                "obstacle_cm": closest_cm,
                "rc": {"lr": 0, "fb": 0, "ud": 0, "yaw": 0},
            }

        if self._mode == "follow":
            return self._follow(target, frame_w, frame_h, persons, now)
        elif self._mode == "orbit":
            return self._orbit(target, frame_w, frame_h, now)
        elif self._mode == "explore":
            return self._explore(persons, now)

        return {"action": "idle"}

    def _follow(self, target: Optional[FPVPerson], frame_w: int, frame_h: int,
                persons: list, now: float) -> dict:
        """Follow the target person."""
        if target is None:
            # Target lost
            if self._target_lost_since == 0:
                self._target_lost_since = now

            lost_duration = now - self._target_lost_since

            # Use last known target for ~1s (Hailo detection can flicker)
            if lost_duration < 1.0 and hasattr(self, '_last_known_target') and self._last_known_target is not None:
                target = self._last_known_target
                # Don't reset _target_lost_since — still counting
            elif lost_duration > self._target_lost_timeout:
                # Switch to explore mode
                self._mode = "explore"
                self._target_lost_since = 0
                self._send_rc(0, 0, 0, 0)
                return {"action": "lost_exploring"}
            else:
                # Slow rotation to search — maintain altitude!
                self._send_rc(0, 0, 8, 15)
                self._last_cmd_time = now
                return {"action": "searching"}

        self._target_lost_since = 0
        self._last_known_target = target

        # Horizontal error (target should be centered)
        offset_x = (target.center_x - frame_w / 2) / (frame_w / 2)
        # Vertical error
        offset_y = (target.center_y - frame_h / 2) / (frame_h / 2)

        # Yaw to center target horizontally
        if abs(offset_x) > self._dead_zone:
            yaw = int(offset_x * self._kp_yaw)
        else:
            yaw = 0

        # Vertical centering — keep target in middle of frame
        if abs(offset_y) > self._dead_zone:
            throttle = int(-offset_y * self._kp_throttle)
        else:
            throttle = 0
        # Clamp throttle to avoid aggressive altitude changes
        throttle = max(-15, min(15, throttle))

        # Distance control via DISTANCE (cm), not frame_pct
        # At 1.5m height, Tello camera sees person at ~130cm as filling the frame
        # Ideal follow distance: ~150cm (1.5m) — close enough for impressive demo
        IDEAL_DIST = 150  # cm
        TOO_CLOSE = 80    # cm — dangerously close, back up
        TOO_FAR = 350     # cm — too far, approach
        dist = target.distance_cm

        if dist < TOO_CLOSE:
            pitch = -12  # too close — retreat
        elif dist > TOO_FAR:
            pitch = 15   # too far — approach
        elif dist < IDEAL_DIST * 0.7:
            pitch = -8   # somewhat close (< 105cm)
        elif dist > IDEAL_DIST * 2.0:
            pitch = 10   # somewhat far (> 300cm)
        else:
            pitch = 0    # sweet spot (105-300cm)

        print(f"[FPV-DIST] dist={dist:.0f}cm pct={target.frame_pct:.2f} -> pitch={pitch} throttle={throttle}")

        # Clamp
        yaw = max(-40, min(40, yaw))
        throttle = max(-30, min(30, throttle))
        pitch = max(-20, min(20, pitch))

        # Smooth
        yaw = int(self._alpha * yaw + (1 - self._alpha) * self._prev_yaw)
        throttle = int(self._alpha * throttle + (1 - self._alpha) * self._prev_throttle)
        pitch = int(self._alpha * pitch + (1 - self._alpha) * self._prev_pitch)
        self._prev_yaw = yaw
        self._prev_throttle = throttle
        self._prev_pitch = pitch

        rc = {"lr": 0, "fb": pitch, "ud": throttle, "yaw": yaw}
        self._send_rc(rc["lr"], rc["fb"], rc["ud"], rc["yaw"])
        self._last_cmd_time = now

        return {
            "action": "following",
            "target_dist_cm": round(target.distance_cm, 0),
            "target_pct": round(target.frame_pct, 2),
            "rc": rc,
        }

    def _orbit(self, target: Optional[FPVPerson], frame_w: int, frame_h: int,
               now: float) -> dict:
        """Orbit around the target — constant yaw + strafe while keeping target centered.

        The drone rotates (yaw) to circle the person while strafing (lr) to maintain
        a circular path. Pitch adjusts distance, throttle keeps vertical centering.
        """
        if target is None:
            # Lost target during orbit — use last known for 1s
            if self._target_lost_since == 0:
                self._target_lost_since = now

            lost_duration = now - self._target_lost_since
            if lost_duration < 1.0 and hasattr(self, '_last_known_target') and self._last_known_target is not None:
                target = self._last_known_target
            elif lost_duration > self._target_lost_timeout:
                self._mode = "explore"
                self._target_lost_since = 0
                self._send_rc(0, 0, 0, 0)
                return {"action": "lost_exploring"}
            else:
                # Keep rotating slowly to reacquire — maintain altitude
                self._send_rc(0, 0, 8, self._orbit_yaw_speed)
                self._last_cmd_time = now
            return {"action": "orbit_searching"}

        self._target_lost_since = 0

        # Same rise-first strategy as follow
        if target.frame_pct > 0.65:
            throttle = 20
            pitch = 0
        else:
            offset_y = (target.center_y - frame_h / 2) / (frame_h / 2)
            if abs(offset_y) > self._dead_zone:
                throttle = int(-offset_y * self._kp_throttle)
            else:
                throttle = 0

            if target.frame_pct < TARGET_MIN_PCT:
                pitch = 18
            elif target.frame_pct > TARGET_IDEAL_PCT * 1.7:
                pitch = -10
            elif target.frame_pct < TARGET_IDEAL_PCT * 0.5:
                pitch = 10
            else:
                pitch = 0

        # Constant yaw rotation (orbiting)
        yaw = self._orbit_yaw_speed

        # Strafe to maintain circular path around target
        # If target drifts from center horizontally, adjust strafe
        offset_x = (target.center_x - frame_w / 2) / (frame_w / 2)
        # Base strafe for circular motion + correction to keep target centered
        lr = self._orbit_strafe_speed + int(-offset_x * 15)

        # Clamp
        yaw = max(-35, min(35, yaw))
        throttle = max(-30, min(30, throttle))
        pitch = max(-25, min(25, pitch))
        lr = max(-30, min(30, lr))

        rc = {"lr": lr, "fb": pitch, "ud": throttle, "yaw": yaw}
        self._send_rc(rc["lr"], rc["fb"], rc["ud"], rc["yaw"])
        self._last_cmd_time = now

        return {
            "action": "orbiting",
            "target_dist_cm": round(target.distance_cm, 0),
            "target_pct": round(target.frame_pct, 2),
            "rc": rc,
        }

    def _explore(self, persons: list, now: float) -> dict:
        """Slow rotation + rise scanning for target."""
        if persons:
            # Found someone — switch back to previous mode (follow or orbit)
            self._mode = "follow"
            return {"action": "found_following"}

        # Slow yaw rotation + gentle rise to find people (they're usually taller than drone)
        self._send_rc(0, 0, 10, 20)
        self._last_cmd_time = now
        return {"action": "exploring"}

    def _apply_min_rc(self, val: int) -> int:
        """Apply minimum RC value — Tello ignores tiny values."""
        if val == 0:
            return 0
        if abs(val) < self._min_rc:
            return self._min_rc if val > 0 else -self._min_rc
        return val

    def _send_rc(self, lr: int, fb: int, ud: int, yaw: int):
        """Send RC command to drone proxy."""
        if _requests is None:
            print("[FPV-RC] ERROR: requests module not available!")
            return
        # Apply minimum values so Tello actually moves
        lr = self._apply_min_rc(lr)
        fb = self._apply_min_rc(fb)
        ud = self._apply_min_rc(ud)
        yaw = self._apply_min_rc(yaw)
        try:
            r = _requests.post(
                f"{self._proxy_url}/drone/command",
                json={"command": "rc", "params": {"lr": lr, "fb": fb, "ud": ud, "yaw": yaw}},
                timeout=1,
            )
            print(f"[FPV-RC] lr={lr} fb={fb} ud={ud} yaw={yaw} -> {r.status_code}")
        except Exception as e:
            print(f"[FPV-RC] SEND FAILED: {e}")


# ---------------------------------------------------------------------------
# Main FPV Manager
# ---------------------------------------------------------------------------

class DroneFPV:
    """Main FPV system — combines stream, detection, control, and UI output.

    Usage from main.py:
        fpv = DroneFPV()
        fpv.start_stream()        # after drone streamon command
        fpv.set_mode("follow")    # start autonomous following
        frame = fpv.get_frame()   # for PiP overlay on entity screen
        state = fpv.get_state()   # for AI brain context
        fpv.stop()
    """

    def __init__(self, proxy_url: str = DRONE_PROXY_URL):
        self._stream = TelloStream()
        self._detector = PersonDetector()  # fallback HOG detector
        self._controller = FPVController(proxy_url)
        self._lock = threading.Lock()
        self._running = False
        self._state = FPVState()
        self._last_frame: Optional[np.ndarray] = None
        self._annotated_frame: Optional[np.ndarray] = None
        self._process_interval = 0.1  # process every 100ms
        self._callback = None  # callback(event, message)
        self._external_detector = None  # set by main.py for Hailo detection
        self._last_ext_persons: list = []  # cache last external detection result

    def set_callback(self, callback):
        """callback(event_type: str, message: str)"""
        self._callback = callback

    def set_external_detector(self, detector_fn):
        """Set external detection function (e.g. Hailo).

        detector_fn(frame) -> list of (x1, y1, x2, y2) person bboxes
        """
        self._external_detector = detector_fn

    def start_stream(self):
        """Start receiving and processing the FPV stream."""
        self._stream.start()
        self._running = True
        threading.Thread(target=self._process_loop, daemon=True).start()
        logger.info("DroneFPV started")

    def stop(self):
        self._running = False
        self._stream.stop()
        self._controller.set_mode("idle")
        with self._lock:
            self._annotated_frame = None
            self._last_frame = None
            self._state = FPVState()

    def set_mode(self, mode: str):
        """Set FPV control mode: follow, explore, hover, idle."""
        self._controller.set_mode(mode)
        with self._lock:
            self._state.mode = mode

    def get_frame(self) -> Optional[np.ndarray]:
        """Get the latest annotated FPV frame for PiP display."""
        with self._lock:
            return self._annotated_frame.copy() if self._annotated_frame is not None else None

    def get_raw_frame(self) -> Optional[np.ndarray]:
        """Get raw frame without annotations."""
        return self._stream.frame

    def get_state(self) -> FPVState:
        """Get current FPV state for AI brain context."""
        with self._lock:
            return FPVState(
                streaming=self._stream.frame_count > 0,
                persons=list(self._state.persons),
                target=self._state.target,
                obstacle_ahead=self._state.obstacle_ahead,
                closest_obstacle_cm=self._state.closest_obstacle_cm,
                frame_count=self._stream.frame_count,
                fps=self._stream.fps,
                mode=self._controller.mode,
            )

    def get_context_for_brain(self) -> dict:
        """Get compact context dict for AI brain."""
        state = self.get_state()
        ctx = {
            "drone_fpv": True,
            "fpv_mode": state.mode,
            "fpv_persons": len(state.persons),
            "fpv_fps": round(state.fps, 1),
        }
        if state.target:
            ctx["fpv_target_distance"] = f"{state.target.distance_cm:.0f}cm"
        if state.obstacle_ahead:
            ctx["fpv_obstacle"] = f"{state.closest_obstacle_cm:.0f}cm"
        return ctx

    def _process_loop(self):
        """Main processing loop — detect, control, annotate."""
        time.sleep(2)  # wait for stream to start
        while self._running:
            try:
                frame = self._stream.frame
                if frame is None:
                    time.sleep(0.1)
                    continue

                # Detect persons — prefer Hailo (external) over HOG
                h, w = frame.shape[:2]
                if self._external_detector:
                    try:
                        boxes = self._external_detector(frame)
                        if boxes is None:
                            # Hailo hasn't processed yet — use cached
                            persons = self._last_ext_persons
                        elif len(boxes) == 0:
                            # Processed but no persons — clear cache
                            persons = []
                            self._last_ext_persons = []
                        else:
                            # Fresh detections
                            persons = []
                            for (x1, y1, x2, y2) in boxes:
                                pw = x2 - x1
                                ph = y2 - y1
                                if ph <= 0:
                                    continue
                                distance_cm = (PERSON_REAL_HEIGHT_CM * 547.0) / ph
                                frame_pct = ph / h
                                persons.append(FPVPerson(
                                    bbox=(x1, y1, x2, y2),
                                    center_x=(x1 + x2) // 2,
                                    center_y=(y1 + y2) // 2,
                                    height_px=ph,
                                    width_px=pw,
                                    distance_cm=distance_cm,
                                    frame_pct=frame_pct,
                                    is_obstacle=False,  # controller decides this
                                ))
                            persons.sort(key=lambda p: p.distance_cm)
                            self._last_ext_persons = persons
                    except Exception as e:
                        logger.error(f"External detector error: {e}")
                        persons = self._last_ext_persons
                else:
                    persons = self._detector.detect(frame)

                # Control
                result = self._controller.process(persons, w, h)

                # Periodic log every ~3s
                action = result.get("action", "")
                if action not in ("wait",) and self._stream.frame_count % 10 == 0:
                    t = next((p for p in persons if p.is_target), None)
                    if t:
                        print(f"[FPV] {action} | persons={len(persons)} "
                              f"target_pct={t.frame_pct:.2f} dist={t.distance_cm:.0f}cm "
                              f"rc={result.get('rc')}")
                    else:
                        print(f"[FPV] {action} | persons={len(persons)} | no target")

                # Update state
                target = None
                obstacle_ahead = False
                closest = 999.0
                for p in persons:
                    if p.is_target:
                        target = p
                    if p.is_obstacle:
                        obstacle_ahead = True
                        closest = min(closest, p.distance_cm)

                with self._lock:
                    self._state.persons = persons
                    self._state.target = target
                    self._state.obstacle_ahead = obstacle_ahead
                    self._state.closest_obstacle_cm = closest
                    self._state.mode = self._controller.mode

                # Annotate frame for UI
                annotated = self._draw_overlay(frame, persons, result)
                with self._lock:
                    self._annotated_frame = annotated

                # Callbacks for notable events
                if self._callback:
                    action = result.get("action", "")
                    if action == "obstacle_stop":
                        self._callback("fpv_obstacle",
                                       f"Obstáculo a {result['obstacle_cm']:.0f}cm, frenando")
                    elif action == "lost_exploring":
                        self._callback("fpv_lost", "Perdí al target, buscando...")
                    elif action == "found_following":
                        self._callback("fpv_found", "Target encontrado, siguiendo")

                time.sleep(self._process_interval)

            except Exception as e:
                logger.error(f"FPV process error: {e}")
                time.sleep(0.5)

    def _draw_overlay(self, frame: np.ndarray, persons: list[FPVPerson],
                      result: dict) -> np.ndarray:
        """Draw detection boxes and info on FPV frame."""
        out = frame.copy()
        h, w = out.shape[:2]

        for p in persons:
            x1, y1, x2, y2 = p.bbox

            if p.is_target:
                color = (0, 255, 0)  # green = target
                label = f"TARGET {p.distance_cm:.0f}cm"
            elif p.is_obstacle:
                color = (0, 0, 255)  # red = obstacle
                label = f"OBSTACLE {p.distance_cm:.0f}cm"
            else:
                color = (255, 255, 0)  # cyan = person
                label = f"{p.distance_cm:.0f}cm"

            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            cv2.putText(out, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        # Mode and status overlay
        action = result.get("action", "")
        mode_text = f"FPV: {self._controller.mode.upper()}"
        if action == "obstacle_stop":
            mode_text += " | OBSTACLE STOP"
        elif action == "searching":
            mode_text += " | SEARCHING..."
        elif action == "following":
            dist = result.get("target_dist_cm", 0)
            mode_text += f" | TRACKING {dist:.0f}cm"
        elif action == "orbiting":
            dist = result.get("target_dist_cm", 0)
            mode_text += f" | ORBITING {dist:.0f}cm"
        elif action == "orbit_searching":
            mode_text += " | ORBIT SEARCH..."

        cv2.putText(out, mode_text, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # FPS
        fps_text = f"FPS: {self._stream.fps:.0f}"
        cv2.putText(out, fps_text, (w - 100, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        # Crosshair center
        cx, cy = w // 2, h // 2
        cv2.line(out, (cx - 15, cy), (cx + 15, cy), (255, 255, 255), 1)
        cv2.line(out, (cx, cy - 15), (cx, cy + 15), (255, 255, 255), 1)

        return out
