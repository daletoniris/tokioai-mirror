"""
TokioAI Vision Filter — Claude teaches Hailo.

When Claude's AI brain sees a false positive from Hailo (e.g. "knife" that's
actually a remote control), it logs a correction. This filter learns from
those corrections and suppresses future false positives automatically.

Learning strategies:
  1. Per-class confidence boost — raise threshold for classes with many FPs
  2. Region suppression — suppress label X in frame region Y
  3. Static object ignore — same label in same position = furniture, ignore it

Corrections persist to disk (~/.tokio_health/vision_corrections.json).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

CORRECTIONS_DIR = os.path.expanduser("~/.tokio_health")
CORRECTIONS_FILE = os.path.join(CORRECTIONS_DIR, "vision_corrections.json")

# After this many FPs for a class, boost its confidence threshold
FP_THRESHOLD_BOOST_COUNT = 3
# Maximum confidence threshold (don't suppress everything)
MAX_CONF_THRESHOLD = 0.92
# How much to boost per correction
CONF_BOOST_STEP = 0.05
# Region match tolerance (normalized 0-1 coords, how close bboxes must be)
REGION_TOLERANCE = 0.12


@dataclass
class Correction:
    """A single correction from Claude."""
    label: str                # what Hailo said (e.g. "knife")
    correct_label: str        # what it actually is (e.g. "remote control")
    reason: str               # Claude's explanation
    region: list[float]       # normalized bbox [x1, y1, x2, y2] (0-1)
    confidence: float         # Hailo's confidence when it was wrong
    timestamp: float = 0.0
    times_seen: int = 1       # how many times this correction triggered

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "correct_label": self.correct_label,
            "reason": self.reason,
            "region": self.region,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "times_seen": self.times_seen,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Correction:
        return cls(
            label=d["label"],
            correct_label=d.get("correct_label", ""),
            reason=d.get("reason", ""),
            region=d.get("region", [0, 0, 1, 1]),
            confidence=d.get("confidence", 0.5),
            timestamp=d.get("timestamp", 0),
            times_seen=d.get("times_seen", 1),
        )


class VisionFilter:
    """Learns from Claude's corrections to filter Hailo false positives."""

    def __init__(self):
        self._lock = threading.Lock()
        self._corrections: list[Correction] = []
        # Per-class confidence thresholds (learned)
        self._class_thresholds: dict[str, float] = {}
        # Per-class FP count
        self._class_fp_count: dict[str, int] = {}
        # Stats
        self._total_filtered = 0
        self._total_corrections = 0

        os.makedirs(CORRECTIONS_DIR, exist_ok=True)
        self._load()

    # --- Persistence ---

    def _load(self):
        try:
            if os.path.isfile(CORRECTIONS_FILE):
                with open(CORRECTIONS_FILE, "r") as f:
                    data = json.load(f)
                self._corrections = [Correction.from_dict(c) for c in data.get("corrections", [])]
                self._class_thresholds = data.get("class_thresholds", {})
                self._class_fp_count = data.get("class_fp_count", {})
                self._total_filtered = data.get("total_filtered", 0)
                self._total_corrections = data.get("total_corrections", 0)
                print(f"[VisionFilter] Loaded {len(self._corrections)} corrections, "
                      f"{len(self._class_thresholds)} class thresholds")
        except Exception as e:
            print(f"[VisionFilter] Load error: {e}")

    def _save(self):
        try:
            data = {
                "corrections": [c.to_dict() for c in self._corrections[-100:]],
                "class_thresholds": self._class_thresholds,
                "class_fp_count": self._class_fp_count,
                "total_filtered": self._total_filtered,
                "total_corrections": self._total_corrections,
            }
            with open(CORRECTIONS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[VisionFilter] Save error: {e}")

    # --- Learning ---

    def add_correction(self, label: str, correct_label: str, reason: str,
                       region: list[float] = None, confidence: float = 0.5):
        """Add a correction from Claude's AI brain.

        Args:
            label: What Hailo detected (the false positive label)
            correct_label: What it actually is
            reason: Why it's wrong
            region: Normalized bbox [x1, y1, x2, y2] where detection was
            confidence: Hailo's confidence for this detection
        """
        with self._lock:
            # Check if we already have a similar correction (same label + region)
            existing = self._find_similar_correction(label, region or [0, 0, 1, 1])
            if existing:
                existing.times_seen += 1
                existing.timestamp = time.time()
                print(f"[VisionFilter] Updated correction: {label} -> {correct_label} "
                      f"(seen {existing.times_seen}x)")
            else:
                corr = Correction(
                    label=label,
                    correct_label=correct_label,
                    reason=reason,
                    region=region or [0, 0, 1, 1],
                    confidence=confidence,
                    timestamp=time.time(),
                )
                self._corrections.append(corr)
                print(f"[VisionFilter] New correction: {label} -> {correct_label} ({reason})")

            # Update per-class FP count and threshold
            self._class_fp_count[label] = self._class_fp_count.get(label, 0) + 1
            fp_count = self._class_fp_count[label]

            if fp_count >= FP_THRESHOLD_BOOST_COUNT:
                current = self._class_thresholds.get(label, 0.55)
                new_thresh = min(MAX_CONF_THRESHOLD, current + CONF_BOOST_STEP)
                self._class_thresholds[label] = round(new_thresh, 2)
                print(f"[VisionFilter] Threshold for '{label}' raised to {new_thresh:.0%} "
                      f"({fp_count} false positives)")

            self._total_corrections += 1
            self._save()

    def _find_similar_correction(self, label: str, region: list[float]) -> Optional[Correction]:
        """Find existing correction for same label in similar region."""
        for c in self._corrections:
            if c.label != label:
                continue
            if self._regions_overlap(c.region, region):
                return c
        return None

    @staticmethod
    def _regions_overlap(r1: list[float], r2: list[float]) -> bool:
        """Check if two normalized regions are similar enough."""
        if len(r1) < 4 or len(r2) < 4:
            return False
        for i in range(4):
            if abs(r1[i] - r2[i]) > REGION_TOLERANCE:
                return False
        return True

    # --- Filtering ---

    def filter_detections(self, detections: list, frame_w: int, frame_h: int) -> list:
        """Filter detections using learned corrections.

        Args:
            detections: List of Detection objects from vision_engine
            frame_w, frame_h: Frame dimensions for normalizing coords

        Returns:
            Filtered list of Detection objects (false positives removed)
        """
        if not self._corrections and not self._class_thresholds:
            return detections

        with self._lock:
            filtered = []
            for det in detections:
                # 1. Check per-class confidence threshold
                min_conf = self._class_thresholds.get(det.label, 0.0)
                if min_conf > 0 and det.confidence < min_conf:
                    self._total_filtered += 1
                    print(f"[VisionFilter] Suppressed {det.label} {det.confidence:.0%} "
                          f"(threshold: {min_conf:.0%})")
                    continue

                # 2. Check region-based corrections
                if frame_w > 0 and frame_h > 0:
                    det_region = [
                        det.x1 / frame_w, det.y1 / frame_h,
                        det.x2 / frame_w, det.y2 / frame_h,
                    ]
                    suppressed = False
                    for corr in self._corrections:
                        if corr.label != det.label:
                            continue
                        # Only apply region suppression for corrections seen 2+ times
                        if corr.times_seen < 2:
                            continue
                        if self._regions_overlap(corr.region, det_region):
                            # Similar region + same label = likely same FP
                            self._total_filtered += 1
                            print(f"[VisionFilter] Region-suppressed {det.label} {det.confidence:.0%} "
                                  f"(was {corr.correct_label})")
                            suppressed = True
                            break
                    if suppressed:
                        continue

                filtered.append(det)

            return filtered

    # --- API ---

    def get_status(self) -> dict:
        """Status for API/dashboard."""
        with self._lock:
            return {
                "total_corrections": self._total_corrections,
                "total_filtered": self._total_filtered,
                "active_corrections": len(self._corrections),
                "class_thresholds": dict(self._class_thresholds),
                "class_fp_counts": dict(self._class_fp_count),
                "recent_corrections": [
                    {
                        "label": c.label,
                        "correct_label": c.correct_label,
                        "reason": c.reason,
                        "times_seen": c.times_seen,
                        "age_s": int(time.time() - c.timestamp),
                    }
                    for c in self._corrections[-10:]
                ],
            }

    def reset_class(self, label: str):
        """Reset corrections for a specific class."""
        with self._lock:
            self._corrections = [c for c in self._corrections if c.label != label]
            self._class_thresholds.pop(label, None)
            self._class_fp_count.pop(label, None)
            self._save()
            print(f"[VisionFilter] Reset corrections for '{label}'")

    def reset_all(self):
        """Reset all corrections."""
        with self._lock:
            self._corrections.clear()
            self._class_thresholds.clear()
            self._class_fp_count.clear()
            self._total_filtered = 0
            self._total_corrections = 0
            self._save()
            print("[VisionFilter] All corrections reset")
