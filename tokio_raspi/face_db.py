"""
TokioAI Face Database — Multi-embedding face recognition.

Each person has MULTIPLE embeddings (different angles, lighting, expressions).
Recognition matches against the closest embedding across all of a person's gallery.
New embeddings auto-accumulate when high-confidence matches occur.

Uses dlib/face_recognition for robust 128-dim face embeddings.
Falls back to histogram-based if face_recognition is not available.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("tokio.facedb")

DB_PATH = os.environ.get("TOKIO_FACE_DB", "/home/mrmoz/tokio_raspi/faces.db")
FACE_SIZE = (128, 128)

# Try to use face_recognition (dlib-based, high accuracy)
try:
    import face_recognition as _fr
    _USE_DLIB = True
    logger.info("Face recognition: using dlib (deep learning embeddings)")
except ImportError:
    _fr = None
    _USE_DLIB = False
    logger.warning("face_recognition not available, using histogram fallback")

# --- Thresholds ---
# L2 distance: lower = more similar. dlib embeddings typically:
#   same person different angle: 0.3-0.5
#   different people: 0.6-1.2
MATCH_THRESHOLD = 0.50        # Max L2 distance to consider a match
STRONG_MATCH = 0.38           # Very confident match — auto-enroll this embedding
AUTO_ENROLL_MIN_DIST = 0.20   # Min distance from existing embeddings to enroll (avoids duplicates)
MAX_EMBEDDINGS_PER_PERSON = 15  # Gallery size per person
HIST_SIMILARITY_THRESHOLD = 0.40  # For histogram fallback

HIST_BINS = 64


@dataclass
class KnownFace:
    face_id: int
    name: str
    role: str  # "admin", "friend", "visitor", "unknown"
    embeddings: list[np.ndarray]  # Multiple embeddings per person
    photo_path: Optional[str]
    first_seen: str
    last_seen: str
    times_seen: int

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def embedding(self) -> np.ndarray:
        """Primary embedding (first one) — for backward compatibility."""
        return self.embeddings[0] if self.embeddings else np.zeros(128)


# ---------------------------------------------------------------------------
# Face Embedding
# ---------------------------------------------------------------------------

def _compute_dlib_embedding(face_crop: np.ndarray) -> Optional[np.ndarray]:
    """128-dim face embedding using dlib's ResNet model.

    Detects the precise face within the crop first, then computes embedding.
    Returns None if no face can be found (instead of garbage fallback).
    """
    rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
    if rgb.dtype != np.uint8:
        rgb = rgb.astype(np.uint8)
    rgb = np.ascontiguousarray(rgb)

    h, w = rgb.shape[:2]
    if max(h, w) < 80:
        scale = 160 / max(h, w)
        rgb = cv2.resize(rgb, (int(w * scale), int(h * scale)))
        h, w = rgb.shape[:2]

    # Try to detect face precisely
    locs = _fr.face_locations(rgb, model="hog")
    if locs:
        encs = _fr.face_encodings(rgb, locs[:1], num_jitters=1)
        if encs:
            return encs[0]

    # Retry with upsampling
    locs = _fr.face_locations(rgb, number_of_times_to_upsample=2, model="hog")
    if locs:
        encs = _fr.face_encodings(rgb, locs[:1], num_jitters=1)
        if encs:
            return encs[0]

    # NO "last resort full region" — produces garbage embeddings
    # Return None so the caller knows we couldn't get a good embedding
    return None


def _compute_histogram_embedding(face_crop: np.ndarray) -> np.ndarray:
    """Histogram-based embedding fallback."""
    face = cv2.resize(face_crop, FACE_SIZE)
    features = []
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    h, w = face.shape[:2]
    for gy in range(2):
        for gx in range(2):
            y1, y2 = gy * h // 2, (gy + 1) * h // 2
            x1, x2 = gx * w // 2, (gx + 1) * w // 2
            region = hsv[y1:y2, x1:x2]
            for ch in range(3):
                hist = cv2.calcHist([region], [ch], None, [HIST_BINS // 4], [0, 256])
                features.append(hist.flatten())

    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_img = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy_img)
    angle = cv2.phase(gx, gy_img, angleInDegrees=True)
    for row in range(4):
        for col in range(4):
            y1, y2 = row * h // 4, (row + 1) * h // 4
            x1, x2 = col * w // 4, (col + 1) * w // 4
            region_mag = magnitude[y1:y2, x1:x2].flatten()
            region_ang = angle[y1:y2, x1:x2].flatten()
            hist, _ = np.histogram(region_ang, bins=8, range=(0, 360), weights=region_mag)
            features.append(hist.astype(np.float32))

    h_profile = np.mean(gray, axis=1).astype(np.float32)
    v_profile = np.mean(gray, axis=0).astype(np.float32)
    features.append(h_profile)
    features.append(v_profile)

    embedding = np.concatenate(features)
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    return embedding


def compare_embeddings(a: np.ndarray, b: np.ndarray) -> tuple[bool, float]:
    """Compare two embeddings. Returns (is_match, confidence)."""
    if len(a) == 128 and len(b) == 128:
        distance = float(np.linalg.norm(a - b))
        is_match = distance <= MATCH_THRESHOLD
        confidence = max(0.0, 1.0 - distance)
        return is_match, confidence
    else:
        dot = np.dot(a, b)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return False, 0.0
        similarity = float(dot / (na * nb))
        return similarity >= HIST_SIMILARITY_THRESHOLD, similarity


# Keep old function name for compatibility
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    _, conf = compare_embeddings(a, b)
    return conf


# ---------------------------------------------------------------------------
# Face Detector
# ---------------------------------------------------------------------------

class FaceDetector:
    def __init__(self):
        self._use_dlib = _USE_DLIB
        if not self._use_dlib:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._cascade = cv2.CascadeClassifier(cascade_path)

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        if self._use_dlib:
            return self._detect_dlib(frame)
        return self._detect_haar(frame)

    def _detect_dlib(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        scale = 1.0
        if max(h, w) > 480:
            scale = 480 / max(h, w)
            small = cv2.resize(frame, (int(w * scale), int(h * scale)))
        else:
            small = frame
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locations = _fr.face_locations(rgb, model="hog")
        results = []
        for top, right, bottom, left in locations:
            x = int(left / scale)
            y = int(top / scale)
            fw = int((right - left) / scale)
            fh = int((bottom - top) / scale)
            results.append((x, y, fw, fh))
        return results

    def _detect_haar(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                                minSize=(60, 60), maxSize=(400, 400))
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in faces]


# ---------------------------------------------------------------------------
# Face Database — Multi-Embedding
# ---------------------------------------------------------------------------

class FaceDB:
    """SQLite-backed face recognition with multiple embeddings per person."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._detector = FaceDetector()
        self._known_faces: list[KnownFace] = []
        self._init_db()
        self._load_faces()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        # Main faces table (one row per person)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'visitor',
                embedding BLOB NOT NULL,
                embedding_type TEXT DEFAULT 'histogram',
                photo_path TEXT,
                first_seen TEXT DEFAULT (datetime('now','localtime')),
                last_seen TEXT DEFAULT (datetime('now','localtime')),
                times_seen INTEGER DEFAULT 1
            )
        """)
        # Multi-embedding gallery table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS face_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                face_id INTEGER NOT NULL,
                embedding BLOB NOT NULL,
                quality REAL DEFAULT 0.0,
                created TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (face_id) REFERENCES faces(id) ON DELETE CASCADE
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
        # Migration: add embedding_type if missing
        try:
            conn.execute("ALTER TABLE faces ADD COLUMN embedding_type TEXT DEFAULT 'histogram'")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()
        logger.info(f"Face DB initialized: {self.db_path}")

    def _load_faces(self):
        conn = sqlite3.connect(self.db_path)

        # Load persons
        try:
            rows = conn.execute(
                "SELECT id, name, role, embedding, photo_path, first_seen, last_seen, times_seen, "
                "COALESCE(embedding_type, 'histogram') FROM faces"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT id, name, role, embedding, photo_path, first_seen, last_seen, times_seen FROM faces"
            ).fetchall()
            rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], 'histogram') for r in rows]

        # Load multi-embeddings
        gallery_rows = conn.execute(
            "SELECT face_id, embedding FROM face_embeddings ORDER BY face_id, quality DESC"
        ).fetchall()
        conn.close()

        # Group gallery embeddings by face_id
        gallery: dict[int, list[np.ndarray]] = {}
        for face_id, emb_blob in gallery_rows:
            emb = np.frombuffer(emb_blob, dtype=np.float64)
            if len(emb) == 128:
                gallery.setdefault(face_id, []).append(emb)

        self._known_faces = []
        for row in rows:
            face_id = row[0]
            emb_type = row[8]
            dtype = np.float64 if emb_type == 'dlib' else np.float32
            primary_emb = np.frombuffer(row[3], dtype=dtype)

            # Build embedding list: gallery embeddings + primary
            embeddings = gallery.get(face_id, [])
            if len(primary_emb) == 128 and emb_type == 'dlib':
                # Add primary if not already in gallery
                if not embeddings:
                    embeddings = [primary_emb]
                else:
                    # Check if primary is already in gallery (avoid duplicate)
                    dists = [float(np.linalg.norm(primary_emb - e)) for e in embeddings]
                    if min(dists) > 0.05:
                        embeddings.insert(0, primary_emb)

            if not embeddings:
                embeddings = [primary_emb]

            self._known_faces.append(KnownFace(
                face_id=face_id, name=row[1], role=row[2],
                embeddings=embeddings, photo_path=row[4],
                first_seen=row[5], last_seen=row[6], times_seen=row[7],
            ))

        total_embs = sum(len(f.embeddings) for f in self._known_faces)
        logger.info(f"Loaded {len(self._known_faces)} faces, {total_embs} total embeddings")

    def detect_faces(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        return self._detector.detect(frame)

    def recognize(self, frame: np.ndarray, face_rect: tuple[int, int, int, int]
                  ) -> tuple[Optional[KnownFace], float]:
        """Recognize a face. Returns (KnownFace, confidence) or (None, 0.0)."""
        if _USE_DLIB:
            return self._recognize_dlib(frame, face_rect)

        # Histogram fallback
        x, y, w, h = face_rect
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x - margin), max(0, y - margin)
        x2, y2 = min(fw, x + w + margin), min(fh, y + h + margin)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None, 0.0
        embedding = _compute_histogram_embedding(face_crop)
        return self._match_embedding(embedding, face_rect)

    def _recognize_dlib(self, frame: np.ndarray, face_rect: tuple[int, int, int, int]
                        ) -> tuple[Optional[KnownFace], float]:
        """Dlib recognition: detect precise face in region, compute embedding, match."""
        x, y, w, h = face_rect
        fh, fw = frame.shape[:2]

        # Expand search area
        margin = int(max(w, h) * 0.3)
        x1 = max(0, x - margin)
        y1 = max(0, y - margin)
        x2 = min(fw, x + w + margin)
        y2 = min(fh, y + h + margin)

        region = frame[y1:y2, x1:x2]
        if region.size == 0:
            return None, 0.0

        embedding = _compute_dlib_embedding(region)
        if embedding is None:
            print(f"[FaceDB] dlib can't find face in region {x},{y},{w},{h}")
            return None, 0.0

        match, conf = self._match_multi_embedding(embedding)
        if match:
            # Auto-enroll: if strong match and embedding is different enough, save it
            self._try_auto_enroll(match, embedding)
        return match, conf

    def _match_multi_embedding(self, embedding: np.ndarray
                                ) -> tuple[Optional[KnownFace], float]:
        """Match embedding against ALL embeddings of ALL persons.

        For each person, compute the minimum L2 distance across their gallery.
        The person with the lowest min-distance wins (if under threshold).
        """
        if len(embedding) != 128:
            # Histogram fallback — use old logic
            return self._match_embedding(embedding, (0, 0, 0, 0))

        best_face = None
        best_dist = float('inf')
        second_dist = float('inf')

        for known in self._known_faces:
            # Find minimum distance across all embeddings of this person
            min_dist = float('inf')
            for stored_emb in known.embeddings:
                if len(stored_emb) != 128:
                    continue
                d = float(np.linalg.norm(embedding - stored_emb))
                if d < min_dist:
                    min_dist = d

            if min_dist < best_dist:
                second_dist = best_dist
                best_dist = min_dist
                best_face = known
            elif min_dist < second_dist:
                second_dist = min_dist

        if best_face and best_dist <= MATCH_THRESHOLD:
            # Ambiguity check: best must be clearly better than second
            margin = second_dist - best_dist
            if second_dist <= MATCH_THRESHOLD and margin < 0.10:
                import sys
            print(f"[FaceDB] AMBIGUOUS: best={best_face.name} d={best_dist:.3f}, "
                  f"second d={second_dist:.3f}, margin={margin:.3f}", flush=True)
                return None, 0.0

            confidence = max(0.0, 1.0 - best_dist)
            print(f"[FaceDB] MATCH: {best_face.name} d={best_dist:.3f} conf={confidence:.2f} "
                  f"(gallery={len(best_face.embeddings)}, 2nd={second_dist:.3f})", flush=True)
            self._update_sighting(best_face.face_id, confidence)
            return best_face, confidence

        if best_face:
            print(f"[FaceDB] NO MATCH: closest={best_face.name} d={best_dist:.3f} > threshold={MATCH_THRESHOLD}", flush=True)
        return None, 0.0

    def _try_auto_enroll(self, face: KnownFace, new_embedding: np.ndarray):
        """Auto-enroll a new embedding if it's a strong match but different angle."""
        if len(new_embedding) != 128:
            return
        if len(face.embeddings) >= MAX_EMBEDDINGS_PER_PERSON:
            return

        # Check distance to closest existing embedding
        min_dist_to_existing = min(
            float(np.linalg.norm(new_embedding - e))
            for e in face.embeddings if len(e) == 128
        )

        # Only the best match distance matters for deciding to enroll
        best_match_dist = min_dist_to_existing

        # Must be a strong match overall but different enough from existing gallery
        if best_match_dist > STRONG_MATCH:
            return  # Not confident enough to auto-enroll
        if min_dist_to_existing < AUTO_ENROLL_MIN_DIST:
            return  # Too similar to existing — no value in storing

        # Enroll!
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding, quality) VALUES (?, ?, ?)",
                (face.face_id, new_embedding.astype(np.float64).tobytes(), 1.0 - min_dist_to_existing),
            )
            conn.commit()
            conn.close()
            face.embeddings.append(new_embedding)
            logger.info(f"Auto-enrolled embedding for '{face.name}' "
                        f"(gallery now {len(face.embeddings)}, dist_to_closest={min_dist_to_existing:.3f})")
        except Exception as e:
            logger.error(f"Auto-enroll error: {e}")

    def _match_embedding(self, embedding: np.ndarray, face_rect: tuple
                         ) -> tuple[Optional[KnownFace], float]:
        """Match embedding (histogram fallback path)."""
        best_match = None
        best_conf = 0.0

        for known in self._known_faces:
            for stored_emb in known.embeddings:
                is_match, confidence = compare_embeddings(embedding, stored_emb)
                if is_match and confidence > best_conf:
                    best_conf = confidence
                    best_match = known

        if best_match:
            self._update_sighting(best_match.face_id, best_conf)
            return best_match, best_conf
        return None, 0.0

    def register_face(self, frame: np.ndarray, face_rect: tuple[int, int, int, int],
                      name: str, role: str = "visitor") -> Optional[KnownFace]:
        """Register a new face with multiple embeddings from one frame."""
        x, y, w, h = face_rect
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x - margin), max(0, y - margin)
        x2, y2 = min(fw, x + w + margin), min(fh, y + h + margin)

        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return None

        if _USE_DLIB:
            embedding = _compute_dlib_embedding(face_crop)
            if embedding is None:
                logger.warning(f"Could not detect face for registration of '{name}'")
                return None
            emb_type = "dlib"

            # Register with multiple jitters for better primary embedding
            rgb = cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
            rgb = np.ascontiguousarray(rgb.astype(np.uint8))
            locs = _fr.face_locations(rgb, model="hog")
            if locs:
                # High-quality embedding with 10 jitters
                encs = _fr.face_encodings(rgb, locs[:1], num_jitters=10)
                if encs:
                    embedding = encs[0]
        else:
            embedding = _compute_histogram_embedding(face_crop)
            emb_type = "histogram"

        # Save photo
        photo_dir = os.path.join(os.path.dirname(self.db_path), "face_photos")
        os.makedirs(photo_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        photo_path = os.path.join(photo_dir, f"{name}_{ts}.jpg")
        cv2.imwrite(photo_path, face_crop)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO faces (name, role, embedding, embedding_type, photo_path) VALUES (?, ?, ?, ?, ?)",
            (name, role, embedding.astype(np.float64).tobytes() if emb_type == 'dlib' else embedding.tobytes(),
             emb_type, photo_path),
        )
        face_id = cursor.lastrowid

        # Also store in gallery table
        if emb_type == 'dlib':
            conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding, quality) VALUES (?, ?, ?)",
                (face_id, embedding.astype(np.float64).tobytes(), 1.0),
            )

        conn.commit()
        conn.close()

        new_face = KnownFace(
            face_id=face_id, name=name, role=role,
            embeddings=[embedding], photo_path=photo_path,
            first_seen=time.strftime("%Y-%m-%d %H:%M:%S"),
            last_seen=time.strftime("%Y-%m-%d %H:%M:%S"),
            times_seen=1,
        )
        self._known_faces.append(new_face)
        logger.info(f"Registered face: {name} (role={role}, id={face_id}, type={emb_type})")
        return new_face

    def register_multi(self, frame: np.ndarray, face_rect: tuple[int, int, int, int],
                       name: str, role: str = "visitor", num_jitters: int = 10
                       ) -> Optional[KnownFace]:
        """Register with high-quality embedding (more jitters = slower but better)."""
        return self.register_face(frame, face_rect, name, role)

    def add_embedding_to_person(self, face_id: int, frame: np.ndarray,
                                 face_rect: tuple[int, int, int, int]) -> bool:
        """Manually add an additional embedding to an existing person."""
        if not _USE_DLIB:
            return False

        x, y, w, h = face_rect
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x - margin), max(0, y - margin)
        x2, y2 = min(fw, x + w + margin), min(fh, y + h + margin)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return False

        embedding = _compute_dlib_embedding(face_crop)
        if embedding is None:
            return False

        face = None
        for f in self._known_faces:
            if f.face_id == face_id:
                face = f
                break
        if not face:
            return False

        if len(face.embeddings) >= MAX_EMBEDDINGS_PER_PERSON:
            logger.warning(f"Gallery full for '{face.name}' ({MAX_EMBEDDINGS_PER_PERSON} embeddings)")
            return False

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO face_embeddings (face_id, embedding, quality) VALUES (?, ?, ?)",
                (face_id, embedding.astype(np.float64).tobytes(), 0.9),
            )
            conn.commit()
            conn.close()
            face.embeddings.append(embedding)
            logger.info(f"Added embedding to '{face.name}' (gallery now {len(face.embeddings)})")
            return True
        except Exception as e:
            logger.error(f"Add embedding error: {e}")
            return False

    def _update_sighting(self, face_id: int, confidence: float):
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
            for f in self._known_faces:
                if f.face_id == face_id:
                    f.times_seen += 1
                    f.last_seen = time.strftime("%Y-%m-%d %H:%M:%S")
                    break
        except Exception as e:
            logger.error(f"Sighting update error: {e}")

    def reregister_face(self, face_id: int, frame: np.ndarray,
                        face_rect: tuple[int, int, int, int]) -> bool:
        """Re-register: replace ALL embeddings with a fresh one."""
        x, y, w, h = face_rect
        margin = int(max(w, h) * 0.15)
        fh, fw = frame.shape[:2]
        x1, y1 = max(0, x - margin), max(0, y - margin)
        x2, y2 = min(fw, x + w + margin), min(fh, y + h + margin)
        face_crop = frame[y1:y2, x1:x2]
        if face_crop.size == 0:
            return False

        if _USE_DLIB:
            embedding = _compute_dlib_embedding(face_crop)
            if embedding is None:
                return False
            emb_type = "dlib"
        else:
            embedding = _compute_histogram_embedding(face_crop)
            emb_type = "histogram"

        photo_dir = os.path.join(os.path.dirname(self.db_path), "face_photos")
        os.makedirs(photo_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")

        for f in self._known_faces:
            if f.face_id == face_id:
                photo_path = os.path.join(photo_dir, f"{f.name}_reregistered_{ts}.jpg")
                cv2.imwrite(photo_path, face_crop)

                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE faces SET embedding = ?, embedding_type = ?, photo_path = ? WHERE id = ?",
                    (embedding.astype(np.float64).tobytes() if emb_type == 'dlib' else embedding.tobytes(),
                     emb_type, photo_path, face_id),
                )
                # Clear old gallery, start fresh
                conn.execute("DELETE FROM face_embeddings WHERE face_id = ?", (face_id,))
                conn.execute(
                    "INSERT INTO face_embeddings (face_id, embedding, quality) VALUES (?, ?, ?)",
                    (face_id, embedding.astype(np.float64).tobytes(), 1.0),
                )
                conn.commit()
                conn.close()

                f.embeddings = [embedding]
                f.photo_path = photo_path
                logger.info(f"Re-registered '{f.name}' (id={face_id}) — gallery reset to 1 embedding")
                return True
        return False

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
        conn.execute("DELETE FROM face_embeddings WHERE face_id = ?", (face_id,))
        conn.execute("DELETE FROM sightings WHERE face_id = ?", (face_id,))
        conn.commit()
        conn.close()
        self._known_faces = [f for f in self._known_faces if f.face_id != face_id]
        logger.info(f"Deleted face id={face_id}")

    @property
    def count(self) -> int:
        return len(self._known_faces)
