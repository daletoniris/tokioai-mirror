"""
TokioAI Face Database — Face recognition with SQLite storage.

Uses histogram-based face embeddings for recognition.
Faces are detected by Hailo or Haar cascade, cropped, and stored
as normalized color+LBP histograms for fast comparison.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("tokio.facedb")

DB_PATH = os.environ.get("TOKIO_FACE_DB", "/home/mrmoz/tokio_raspi/faces.db")
FACE_SIZE = (128, 128)  # normalized face crop size
RECOGNITION_THRESHOLD = 0.55  # cosine similarity threshold
HIST_BINS = 64


@dataclass
class KnownFace:
    face_id: int
    name: str
    role: str  # "admin", "friend", "visitor", "unknown"
    embedding: np.ndarray
    photo_path: Optional[str]
    first_seen: str
    last_seen: str
    times_seen: int

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ---------------------------------------------------------------------------
# Face Embedding — histogram-based (fast, no dlib needed)
# ---------------------------------------------------------------------------

def compute_face_embedding(face_crop: np.ndarray) -> np.ndarray:
    """
    Compute a face embedding from a cropped face image.

    Uses a combination of:
    1. Color histogram (HSV) — captures skin tone, hair color
    2. Gradient histogram — captures face structure
    3. Spatial bins — captures feature positions

    Returns a normalized vector for cosine similarity comparison.
    """
    # Resize to standard size
    face = cv2.resize(face_crop, FACE_SIZE)

    features = []

    # 1. HSV color histogram (3 channels, spatial 2x2 grid)
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    h, w = face.shape[:2]
    for gy in range(2):
        for gx in range(2):
            y1, y2 = gy * h // 2, (gy + 1) * h // 2
            x1, x2 = gx * w // 2, (gx + 1) * w // 2
            region = hsv[y1:y2, x1:x2]
            for ch in range(3):
                hist = cv2.calcHist([region], [ch], None, [HIST_BINS // 4], [0, 256])
                hist = hist.flatten()
                features.append(hist)

    # 2. Gradient orientation histogram (captures edges/structure)
    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_img = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy_img)
    angle = cv2.phase(gx, gy_img, angleInDegrees=True)

    # 4x4 spatial grid of gradient histograms
    for row in range(4):
        for col in range(4):
            y1, y2 = row * h // 4, (row + 1) * h // 4
            x1, x2 = col * w // 4, (col + 1) * w // 4
            region_mag = magnitude[y1:y2, x1:x2].flatten()
            region_ang = angle[y1:y2, x1:x2].flatten()
            hist, _ = np.histogram(region_ang, bins=8, range=(0, 360),
                                   weights=region_mag)
            features.append(hist.astype(np.float32))

    # 3. Intensity profile (horizontal and vertical)
    h_profile = np.mean(gray, axis=1).astype(np.float32)  # vertical profile
    v_profile = np.mean(gray, axis=0).astype(np.float32)  # horizontal profile
    features.append(h_profile)
    features.append(v_profile)

    # Concatenate and normalize
    embedding = np.concatenate(features)
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm

    return embedding


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two embeddings."""
    dot = np.dot(a, b)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


# ---------------------------------------------------------------------------
# Face Detector (Haar cascade — lightweight fallback)
# ---------------------------------------------------------------------------

class FaceDetector:
    """Detect faces using Haar cascade (works without Hailo)."""

    def __init__(self):
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            logger.error("Failed to load Haar cascade")

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Detect faces, returns list of (x, y, w, h) tuples."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(60, 60),
            maxSize=(400, 400),
        )
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]


# ---------------------------------------------------------------------------
# Face Database
# ---------------------------------------------------------------------------

class FaceDB:
    """SQLite-backed face recognition database."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._detector = FaceDetector()
        self._known_faces: list[KnownFace] = []
        self._init_db()
        self._load_faces()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'visitor',
                embedding BLOB NOT NULL,
                photo_path TEXT,
                first_seen TEXT DEFAULT (datetime('now','localtime')),
                last_seen TEXT DEFAULT (datetime('now','localtime')),
                times_seen INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sightings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id INTEGER,
                timestamp TEXT DEFAULT (datetime('now','localtime')),
                confidence REAL,
                FOREIGN KEY (face_id) REFERENCES faces(id)
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Face DB initialized: {self.db_path}")

    def _load_faces(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT id, name, role, embedding, photo_path, first_seen, last_seen, times_seen FROM faces"
        ).fetchall()
        conn.close()

        self._known_faces = []
        for row in rows:
            emb = np.frombuffer(row[3], dtype=np.float32)
            self._known_faces.append(KnownFace(
                face_id=row[0], name=row[1], role=row[2],
                embedding=emb, photo_path=row[4],
                first_seen=row[5], last_seen=row[6], times_seen=row[7],
            ))
        logger.info(f"Loaded {len(self._known_faces)} known faces")

    def detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Detect faces in frame using Haar cascade."""
        return self._detector.detect(frame)

    def recognize(self, frame: np.ndarray, face_rect: tuple[int, int, int, int]
                  ) -> tuple[Optional[KnownFace], float]:
        """
        Recognize a face from a frame crop.

        Returns (KnownFace, confidence) or (None, 0.0) if unknown.
        """
        x, y, w, h = face_rect
        # Add margin around face for better embedding
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(fw, x + w + margin)
        y2 = min(fh, y + h + margin)

        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None, 0.0

        embedding = compute_face_embedding(face_crop)

        best_match = None
        best_sim = 0.0

        for known in self._known_faces:
            sim = cosine_similarity(embedding, known.embedding)
            if sim > best_sim:
                best_sim = sim
                best_match = known

        if best_match and best_sim >= RECOGNITION_THRESHOLD:
            # Update last seen
            self._update_sighting(best_match.face_id, best_sim)
            return best_match, best_sim

        return None, best_sim

    def register_face(self, frame: np.ndarray, face_rect: tuple[int, int, int, int],
                      name: str, role: str = "visitor") -> Optional[KnownFace]:
        """Register a new face in the database."""
        x, y, w, h = face_rect
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(fw, x + w + margin)
        y2 = min(fh, y + h + margin)

        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None

        embedding = compute_face_embedding(face_crop)

        # Save photo
        photo_dir = os.path.join(os.path.dirname(self.db_path), "face_photos")
        os.makedirs(photo_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        photo_path = os.path.join(photo_dir, f"{name}_{ts}.jpg")
        cv2.imwrite(photo_path, face_crop)

        # Insert into DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO faces (name, role, embedding, photo_path) VALUES (?, ?, ?, ?)",
            (name, role, embedding.tobytes(), photo_path),
        )
        face_id = cursor.lastrowid
        conn.commit()
        conn.close()

        new_face = KnownFace(
            face_id=face_id, name=name, role=role,
            embedding=embedding, photo_path=photo_path,
            first_seen=time.strftime("%Y-%m-%d %H:%M:%S"),
            last_seen=time.strftime("%Y-%m-%d %H:%M:%S"),
            times_seen=1,
        )
        self._known_faces.append(new_face)
        logger.info(f"Registered face: {name} (role={role}, id={face_id})")
        return new_face

    def _update_sighting(self, face_id: int, confidence: float):
        """Record a sighting and update last_seen."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE faces SET last_seen = datetime('now','localtime'), "
                "times_seen = times_seen + 1 WHERE id = ?",
                (face_id,),
            )
            conn.execute(
                "INSERT INTO sightings (face_id, confidence) VALUES (?, ?)",
                (face_id, confidence),
            )
            conn.commit()
            conn.close()

            # Update in-memory
            for f in self._known_faces:
                if f.face_id == face_id:
                    f.times_seen += 1
                    f.last_seen = time.strftime("%Y-%m-%d %H:%M:%S")
                    break
        except Exception as e:
            logger.error(f"Sighting update error: {e}")

    def get_all_faces(self) -> list[KnownFace]:
        return list(self._known_faces)

    def get_face_by_name(self, name: str) -> Optional[KnownFace]:
        for f in self._known_faces:
            if f.name.lower() == name.lower():
                return f
        return None

    def delete_face(self, face_id: int):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM faces WHERE id = ?", (face_id,))
        conn.execute("DELETE FROM sightings WHERE face_id = ?", (face_id,))
        conn.commit()
        conn.close()
        self._known_faces = [f for f in self._known_faces if f.face_id != face_id]
        logger.info(f"Deleted face id={face_id}")

    @property
    def count(self) -> int:
        return len(self._known_faces)
