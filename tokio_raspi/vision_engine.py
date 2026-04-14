"""
TokioAI Vision Engine — Hailo-8L accelerated inference.

Captures frames from USB camera, runs YOLOv8 on Hailo-8L,
returns detections with bounding boxes and labels.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from .coco_labels import COCO_LABELS

# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    @property
    def area(self) -> int:
        return (self.x2 - self.x1) * (self.y2 - self.y1)


# ---------------------------------------------------------------------------
# Available models
# ---------------------------------------------------------------------------

MODELS = {
    "detect": "/usr/share/hailo-models/yolov8s_h8l.hef",
    "faces": "/usr/share/hailo-models/yolov5s_personface_h8l.hef",
    "pose": "/usr/share/hailo-models/yolov8s_pose_h8l_pi.hef",
    "face_scrfd": "/usr/share/hailo-models/scrfd_2.5g_h8l.hef",
}


# ---------------------------------------------------------------------------
# YOLOv8 post-processing (Hailo output format)
# ---------------------------------------------------------------------------

def _hailo_nms_postprocess(outputs: dict, orig_h: int, orig_w: int,
                            conf_thresh: float = 0.4,
                            num_classes: int = 80) -> list[Detection]:
    """
    Parse Hailo on-chip NMS output.

    Hailo YOLOv8 NMS output format:
    - results[key] is a list (batch) of lists (80 classes)
    - Each class is an ndarray of shape (N, 5) where N = number of detections
    - 5 values per detection: [y_min, x_min, y_max, x_max, score]
    - Coordinates are normalized 0-1
    """
    results = []

    for key, value in outputs.items():
        # value is list[list[ndarray]] — [batch][class_id] = ndarray(N, 5)
        if not isinstance(value, list) or len(value) == 0:
            continue

        batch = value[0]  # first (only) batch
        if not isinstance(batch, list):
            continue

        for cls_id, cls_detections in enumerate(batch):
            if not isinstance(cls_detections, np.ndarray):
                continue
            if cls_detections.size == 0:
                continue
            if cls_detections.ndim != 2 or cls_detections.shape[1] < 5:
                continue

            for det in cls_detections:
                y_min, x_min, y_max, x_max, score = (
                    float(det[0]), float(det[1]), float(det[2]),
                    float(det[3]), float(det[4])
                )

                if score < conf_thresh:
                    continue

                x1 = max(0, int(x_min * orig_w))
                y1 = max(0, int(y_min * orig_h))
                x2 = min(orig_w, int(x_max * orig_w))
                y2 = min(orig_h, int(y_max * orig_h))

                if x2 <= x1 or y2 <= y1:
                    continue

                label = COCO_LABELS[cls_id] if cls_id < len(COCO_LABELS) else f"class_{cls_id}"
                results.append(Detection(cls_id, label, score, x1, y1, x2, y2))

    return results


# ---------------------------------------------------------------------------
# Vision Engine
# ---------------------------------------------------------------------------

class VisionEngine:
    """Camera capture + Hailo-8L inference engine."""

    def __init__(self, camera_id: int = 0, model: str = "detect",
                 conf_thresh: float = 0.55):
        self.camera_id = camera_id
        self.model_name = model
        self.conf_thresh = conf_thresh

        self._cap: Optional[cv2.VideoCapture] = None
        self._hailo_device = None
        self._network_group = None
        self._input_params = None
        self._output_params = None
        self._input_name: str = ""
        self._input_shape: tuple = (0, 0)  # (H, W)
        self._num_classes: int = 80
        self._hailo_available = False

        self._frame: Optional[np.ndarray] = None
        self._detections: list[Detection] = []
        self._fps: float = 0.0
        self._running = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        # External frame inbox/outbox for FPV detection
        self._ext_frame: Optional[np.ndarray] = None
        self._ext_detections: Optional[list[Detection]] = None
        self._ext_lock = threading.Lock()

        self._init_camera()
        self._init_hailo()

    def _init_camera(self):
        """Open the USB camera."""
        self._cap = cv2.VideoCapture(self.camera_id)
        if not self._cap.isOpened():
            print(f"[VisionEngine] WARNING: Cannot open camera {self.camera_id}")
            return
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self._cap.set(cv2.CAP_PROP_FPS, 30)
        print(f"[VisionEngine] Camera {self.camera_id} opened: "
              f"{int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
              f"{int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")

    def _init_hailo(self):
        """Initialize Hailo-8L device and load model."""
        model_path = MODELS.get(self.model_name)
        if not model_path:
            print(f"[VisionEngine] Unknown model: {self.model_name}")
            return

        try:
            from hailo_platform import (
                HEF, VDevice, ConfigureParams, InputVStreamParams,
                OutputVStreamParams, FormatType, InferVStreams,
                HailoStreamInterface,
            )

            print(f"[VisionEngine] Loading Hailo model: {model_path}")
            hef = HEF(model_path)

            self._hailo_device = VDevice()
            configure_params = ConfigureParams.create_from_hef(
                hef, interface=HailoStreamInterface.PCIe
            )
            self._network_group = self._hailo_device.configure(hef, configure_params)[0]

            input_info = hef.get_input_vstream_infos()
            output_info = hef.get_output_vstream_infos()

            self._input_name = input_info[0].name
            shape = input_info[0].shape  # [H, W, C] or [C, H, W]
            if shape[0] <= 4:  # CHW format
                self._input_shape = (shape[1], shape[2])
            else:
                self._input_shape = (shape[0], shape[1])

            print(f"[VisionEngine] Input: {self._input_name} {shape}")
            for o in output_info:
                print(f"[VisionEngine] Output: {o.name} {o.shape}")

            # Determine num_classes from model name
            if "personface" in model_path:
                self._num_classes = 2  # person, face
            elif "scrfd" in model_path:
                self._num_classes = 1  # face only

            self._input_params = InputVStreamParams.make(
                self._network_group, quantized=True,
                format_type=FormatType.UINT8
            )
            self._output_params = OutputVStreamParams.make(
                self._network_group, quantized=False,
                format_type=FormatType.FLOAT32
            )
            self._hailo_available = True
            print(f"[VisionEngine] Hailo-8L ready! Input: {self._input_shape}")

        except ImportError:
            print("[VisionEngine] hailo_platform not available — running without AI")
        except Exception as e:
            print(f"[VisionEngine] Hailo init error: {e}")

    def switch_model(self, model_name: str):
        """Switch to a different model at runtime."""
        if model_name == self.model_name:
            return
        was_running = self._running
        if was_running:
            self.stop()
        # Cleanup old model
        self._cleanup_hailo()
        self.model_name = model_name
        self._init_hailo()
        if was_running:
            self.start()

    def _cleanup_hailo(self):
        """Release Hailo resources."""
        self._infer_pipeline = None
        self._network_group = None
        if self._hailo_device:
            try:
                self._hailo_device.release()
            except Exception:
                pass
            self._hailo_device = None
        self._hailo_available = False

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        """Resize frame for model input. Returns UINT8 RGB [0-255]."""
        h, w = self._input_shape
        resized = cv2.resize(frame, (w, h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        return np.expand_dims(rgb, axis=0)  # [1, H, W, 3] uint8

    def _infer_batch(self, frames: list[np.ndarray],
                      pipeline, activation) -> list[list[Detection]]:
        """Run inference on frames using an active pipeline."""
        all_detections = []
        for frame in frames:
            orig_h, orig_w = frame.shape[:2]
            input_data = self._preprocess(frame)
            try:
                results = pipeline.infer({self._input_name: input_data})
                detections = _hailo_nms_postprocess(
                    results,
                    orig_h=orig_h, orig_w=orig_w,
                    conf_thresh=self.conf_thresh,
                    num_classes=self._num_classes,
                )
                all_detections.append(detections)
            except Exception as e:
                print(f"[VisionEngine] Inference error: {e}")
                all_detections.append([])
        return all_detections

    def _capture_loop(self):
        """Background thread: capture frames and run inference."""
        if self._hailo_available:
            self._capture_loop_hailo()
        else:
            self._capture_loop_camera_only()

    def _capture_loop_camera_only(self):
        """Capture frames without AI inference."""
        while self._running:
            if self._cap is None or not self._cap.isOpened():
                time.sleep(0.1)
                continue
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame = frame
                self._fps = 30.0

    def _capture_loop_hailo(self):
        """Capture frames with Hailo AI inference."""
        from hailo_platform import InferVStreams

        frame_times = []
        ext_counter = 0

        with InferVStreams(self._network_group,
                          self._input_params,
                          self._output_params) as pipeline:
            with self._network_group.activate():
                print("[VisionEngine] Hailo pipeline active")
                while self._running:
                    if self._cap is None or not self._cap.isOpened():
                        time.sleep(0.1)
                        continue

                    t0 = time.monotonic()
                    ret, frame = self._cap.read()
                    if not ret:
                        time.sleep(0.01)
                        continue

                    orig_h, orig_w = frame.shape[:2]
                    input_data = self._preprocess(frame)
                    try:
                        results = pipeline.infer({self._input_name: input_data})
                        detections = _hailo_nms_postprocess(
                            results,
                            orig_h=orig_h, orig_w=orig_w,
                            conf_thresh=self.conf_thresh,
                            num_classes=self._num_classes,
                        )
                    except Exception as e:
                        print(f"[VisionEngine] Inference error: {e}")
                        detections = []

                    dt = time.monotonic() - t0
                    frame_times.append(dt)
                    if len(frame_times) > 30:
                        frame_times.pop(0)
                    fps = 1.0 / (sum(frame_times) / len(frame_times)) if frame_times else 0

                    with self._lock:
                        self._frame = frame
                        self._detections = detections
                        self._fps = fps

                    # Process external FPV frame every 3rd cycle (~10fps)
                    ext_counter += 1
                    if ext_counter >= 3:
                        ext_counter = 0
                        ext_frame = None
                        with self._ext_lock:
                            if self._ext_frame is not None:
                                ext_frame = self._ext_frame
                                self._ext_frame = None
                        if ext_frame is not None:
                            try:
                                eh, ew = ext_frame.shape[:2]
                                ext_input = self._preprocess(ext_frame)
                                ext_results = pipeline.infer({self._input_name: ext_input})
                                ext_dets = _hailo_nms_postprocess(
                                    ext_results,
                                    orig_h=eh, orig_w=ew,
                                    conf_thresh=self.conf_thresh,
                                    num_classes=self._num_classes,
                                )
                                with self._ext_lock:
                                    self._ext_detections = ext_dets
                            except Exception as e:
                                print(f"[VisionEngine] FPV inference error: {e}")

    def start(self):
        """Start capture + inference thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print("[VisionEngine] Started")

    def stop(self):
        """Stop capture thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        print("[VisionEngine] Stopped")

    def get_frame(self) -> Optional[np.ndarray]:
        """Get latest frame (BGR)."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def get_detections(self) -> list[Detection]:
        """Get latest detections."""
        with self._lock:
            return list(self._detections)

    def get_fps(self) -> float:
        """Get current FPS."""
        with self._lock:
            return self._fps

    def detect_external(self, frame: np.ndarray) -> Optional[list[Detection]]:
        """Submit an external frame for Hailo detection (non-blocking).

        Returns previous results or None if not yet processed.
        Call this repeatedly — results come back on next cycle.
        """
        with self._ext_lock:
            self._ext_frame = frame.copy()
            result = self._ext_detections
            self._ext_detections = None
            return result

    def get_status(self) -> dict:
        """Get engine status for API/UI."""
        with self._lock:
            return {
                "model": self.model_name,
                "hailo_available": self._hailo_available,
                "camera_open": self._cap is not None and self._cap.isOpened(),
                "fps": round(self._fps, 1),
                "detections_count": len(self._detections),
                "detections": [
                    {"label": d.label, "confidence": round(d.confidence, 2),
                     "bbox": [d.x1, d.y1, d.x2, d.y2]}
                    for d in self._detections
                ],
                "running": self._running,
            }

    def capture_snapshot(self) -> Optional[bytes]:
        """Capture a JPEG snapshot with detections drawn."""
        frame = self.get_frame()
        if frame is None:
            return None
        detections = self.get_detections()
        annotated = draw_detections(frame, detections)
        _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    def release(self):
        """Release all resources — camera + Hailo."""
        self.stop()
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._cleanup_hailo()
        print("[VisionEngine] All resources released")

    def __del__(self):
        """Safety net — release resources if not properly stopped."""
        try:
            self.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Drawing utilities
# ---------------------------------------------------------------------------

# Color palette for different object types
COLORS = {
    "person": (0, 255, 100),
    "face": (255, 200, 0),
    "vehicle": (255, 100, 0),
    "animal": (0, 200, 255),
    "threat": (0, 0, 255),
    "default": (100, 255, 100),
}


def _get_color(label: str) -> tuple:
    from .coco_labels import THREAT_OBJECTS, VEHICLE_OBJECTS, PERSON_OBJECTS, ANIMAL_OBJECTS
    if label in THREAT_OBJECTS:
        return COLORS["threat"]
    if label in PERSON_OBJECTS:
        return COLORS["person"]
    if label in VEHICLE_OBJECTS:
        return COLORS["vehicle"]
    if label in ANIMAL_OBJECTS:
        return COLORS["animal"]
    if label == "face":
        return COLORS["face"]
    return COLORS["default"]


def draw_detections(frame: np.ndarray, detections: list[Detection]) -> np.ndarray:
    """Draw bounding boxes and labels on frame."""
    annotated = frame.copy()
    for det in detections:
        color = _get_color(det.label)
        cv2.rectangle(annotated, (det.x1, det.y1), (det.x2, det.y2), color, 2)

        text = f"{det.label} {det.confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(annotated, (det.x1, det.y1 - th - 8),
                      (det.x1 + tw + 4, det.y1), color, -1)
        cv2.putText(annotated, text, (det.x1 + 2, det.y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

    return annotated
