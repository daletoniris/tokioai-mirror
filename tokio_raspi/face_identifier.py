"""
Face Identifier — Gemini 2.5 Flash via Vertex AI for face identification.

Replaces dlib-based face_db recognition with cloud vision.
Hailo detects persons, this module identifies WHO they are.

Architecture:
  Hailo detects person -> extract head crop -> Gemini Flash identifies -> cache 5 min

Cost: ~$0.0001 per identification call (~$0.36/hr at 1 call/sec)
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "teco-sdb-irt-4f83")
VERTEX_REGION = os.getenv("VERTEX_REGION", "global")
GEMINI_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash")

# Cache duration: how long to remember an identified face without re-querying
CACHE_TTL = float(os.getenv("FACE_CACHE_TTL", "300"))  # 5 minutes

# Minimum interval between Gemini calls for the same person box region
MIN_IDENTIFY_INTERVAL = float(os.getenv("FACE_IDENTIFY_INTERVAL", "5.0"))


@dataclass
class IdentifiedPerson:
    name: str  # "Daniel", "unknown", "visitor_1"
    confidence: float  # 0.0-1.0
    role: str  # "creator", "daughter", "visitor", "unknown"
    rect: tuple  # (x, y, w, h) face region
    timestamp: float = field(default_factory=time.time)
    description: str = ""  # brief description from Gemini


class FaceIdentifier:
    """Identifies faces using Gemini 2.5 Flash via Vertex AI."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, IdentifiedPerson] = {}  # region_key -> IdentifiedPerson
        self._last_call_time: float = 0.0
        self._credentials = None
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._available = False
        self._call_count = 0
        self._error_count = 0

        # Known people reference (what to tell Gemini to look for)
        self._known_people = {
            "Daniel": {
                "role": "creator",
                "description": "Male, beard, glasses, Argentine hacker. Creator of TokioAI. Also known as MrMoz",
            },
            "Sofi": {
                "role": "daughter",
                "description": "Young girl, Daniel's daughter",
            },
        }

        self._init_credentials()

    def _init_credentials(self):
        """Initialize Google credentials for Vertex AI."""
        sa_paths = [
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex-credentials.json"),
            "/home/mrmoz/tokio_raspi/vertex-credentials.json",
        ]

        for sa_path in sa_paths:
            if sa_path and os.path.isfile(sa_path):
                try:
                    from google.oauth2 import service_account
                    self._credentials = service_account.Credentials.from_service_account_file(
                        sa_path,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"],
                    )
                    self._available = True
                    print(f"[FaceID] Vertex AI credentials loaded from {sa_path}")
                    return
                except Exception as e:
                    print(f"[FaceID] Failed to load credentials from {sa_path}: {e}")

        print("[FaceID] No credentials found - face identification disabled")

    def _refresh_token(self):
        """Refresh the OAuth2 token if needed."""
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return  # still valid

        try:
            import google.auth.transport.requests
            self._credentials.refresh(google.auth.transport.requests.Request())
            self._token = self._credentials.token
            self._token_expiry = now + 3500  # ~58 min
        except Exception as e:
            print(f"[FaceID] Token refresh failed: {e}")
            self._token = None

    @property
    def available(self) -> bool:
        return self._available

    def _region_key(self, rect: tuple) -> str:
        """Create a spatial key for caching based on face region.

        Groups nearby regions together so slight movement doesn't trigger re-identification.
        """
        x, y, w, h = rect
        # Quantize to 50px grid
        gx = (x + w // 2) // 50
        gy = (y + h // 2) // 50
        return f"{gx}_{gy}"

    def get_cached(self, rect: tuple) -> Optional[IdentifiedPerson]:
        """Check cache for a recently identified person at this location."""
        key = self._region_key(rect)
        with self._lock:
            cached = self._cache.get(key)
            if cached and (time.time() - cached.timestamp) < CACHE_TTL:
                cached.rect = rect  # update position
                return cached
            elif cached:
                del self._cache[key]
        return None

    def identify(self, frame: np.ndarray, rect: tuple) -> Optional[IdentifiedPerson]:
        """Identify a person from a head crop. Uses cache first, then Gemini Flash.

        Args:
            frame: Full camera frame
            rect: (x, y, w, h) of the head region

        Returns:
            IdentifiedPerson or None if identification failed
        """
        # Check cache first
        cached = self.get_cached(rect)
        if cached:
            return cached

        if not self._available:
            return None

        # Rate limit
        now = time.time()
        if now - self._last_call_time < MIN_IDENTIFY_INTERVAL:
            return None

        self._last_call_time = now

        # Extract head crop
        x, y, w, h = rect
        fh, fw = frame.shape[:2]
        x = max(0, x)
        y = max(0, y)
        x2 = min(fw, x + w)
        y2 = min(fh, y + h)

        if x2 - x < 20 or y2 - y < 20:
            return None

        crop = frame[y:y2, x:x2]

        # Also get wider context (upper body) for better identification
        ctx_x = max(0, x - w // 2)
        ctx_y = max(0, y - h // 4)
        ctx_x2 = min(fw, x + w + w // 2)
        ctx_y2 = min(fh, y + h * 2)
        context_crop = frame[ctx_y:ctx_y2, ctx_x:ctx_x2]

        # Resize for API (small = fast + cheap)
        if context_crop.shape[1] > 256:
            scale = 256 / context_crop.shape[1]
            context_crop = cv2.resize(context_crop, None, fx=scale, fy=scale)

        # Encode
        _, buf = cv2.imencode(".jpg", context_crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
        img_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

        # Call Gemini Flash
        result = self._call_gemini(img_b64)
        if result:
            result.rect = rect
            # Cache it
            key = self._region_key(rect)
            with self._lock:
                self._cache[key] = result
            self._call_count += 1
            return result

        return None

    def _call_gemini(self, img_b64: str) -> Optional[IdentifiedPerson]:
        """Call Gemini 2.5 Flash via Vertex AI to identify the person."""
        self._refresh_token()
        if not self._token:
            return None

        # Build prompt with known people descriptions
        people_desc = "\n".join(
            f"- {name}: {info['description']}"
            for name, info in self._known_people.items()
        )

        prompt = (
            "Who is this person? Reply ONLY with short JSON.\n"
            f"Known: {people_desc}\n"
            'Match: {"name":"TheirName","conf":0.9}\n'
            'No match: {"name":"unknown","conf":0.0}\n'
            "JSON only, no markdown, no explanation."
        )

        if VERTEX_REGION == "global":
            url = (
                f"https://aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
                f"/locations/global/publishers/google/models/{GEMINI_MODEL}:generateContent"
            )
        else:
            url = (
                f"https://{VERTEX_REGION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT}"
                f"/locations/{VERTEX_REGION}/publishers/google/models/{GEMINI_MODEL}:generateContent"
            )

        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                ]
            }],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256},
        }

        try:
            import urllib.request
            import urllib.error

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            candidates = result.get("candidates", [])
            if not candidates:
                return None

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()

            # Parse JSON response
            # Clean potential markdown wrapping
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            # Try strict JSON first, then handle truncated responses
            data = None
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Gemini may truncate — try to extract name from partial JSON
                import re
                m = re.search(r'"name"\s*:\s*"([^"]+)"', text)
                if m:
                    name_match = m.group(1)
                    c = re.search(r'"conf(?:idence)?"\s*:\s*([\d.]+)', text)
                    data = {
                        "name": name_match,
                        "conf": float(c.group(1)) if c else 0.8,
                    }
                    print(f"[FaceID] Recovered from truncated JSON: {name_match}")

            if not data:
                self._error_count += 1
                if self._error_count <= 5:
                    print(f"[FaceID] Unparseable response: {text[:100]}")
                return None

            name = data.get("name", "unknown")
            confidence = float(data.get("conf", data.get("confidence", 0.0)))
            role = "visitor"

            # Map to known people roles
            if name in self._known_people:
                role = self._known_people[name]["role"]

            print(f"[FaceID] Identified: {name} (conf={confidence:.2f}, role={role})")

            return IdentifiedPerson(
                name=name,
                confidence=confidence,
                role=role,
                rect=(0, 0, 0, 0),  # set by caller
                description="",
            )
        except Exception as e:
            self._error_count += 1
            if self._error_count <= 10:
                print(f"[FaceID] Gemini call failed: {e}")
            return None

    def add_known_person(self, name: str, role: str, description: str):
        """Add a person to the known people list (runtime, for events)."""
        self._known_people[name] = {"role": role, "description": description}
        print(f"[FaceID] Added known person: {name} ({role})")

    def clear_cache(self):
        """Clear the identification cache."""
        with self._lock:
            self._cache.clear()

    def get_stats(self) -> dict:
        with self._lock:
            cache_size = len(self._cache)
        return {
            "available": self._available,
            "call_count": self._call_count,
            "error_count": self._error_count,
            "cache_size": cache_size,
            "cache_ttl": CACHE_TTL,
            "known_people": list(self._known_people.keys()),
        }
