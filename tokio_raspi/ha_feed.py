"""
Home Assistant integration for TokioAI Entity.

Polls HA API for sensor data, media players (Alexa/Jarvis), lights, switches.
Displays info on the entity UI and exposes via API for GCP core.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

try:
    import requests as _requests
except ImportError:
    _requests = None

HA_URL = os.getenv("TOKIO_HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("TOKIO_HA_TOKEN", "")
HA_POLL_INTERVAL = float(os.getenv("TOKIO_HA_INTERVAL", "10"))


class HAFeed:
    """Polls Home Assistant for sensor/media data."""

    def __init__(self):
        self._running = False
        self._lock = threading.Lock()
        self._available = False
        self._token = HA_TOKEN
        self._error_count = 0

        # State
        self._media: dict = {}       # media_player state
        self._sensors: dict = {}     # sensor values
        self._devices: dict = {}     # switches/lights
        self._last_poll = 0.0

        if not self._token:
            # Re-read env in case it was set after module import
            self._token = os.getenv("TOKIO_HA_TOKEN", "")
        if not self._token:
            self._try_generate_token()

        if self._token:
            self._available = self._test_connection()

    def _try_generate_token(self):
        """Try to construct JWT from HA auth storage."""
        try:
            import jwt
            auth_path = "/home/mrmoz/homeassistant/.storage/auth"
            if not os.path.isfile(auth_path):
                return
            with open(auth_path) as f:
                data = json.load(f)
            for t in data.get("data", {}).get("refresh_tokens", []):
                if t.get("client_name") == "TokioAI" and t.get("token_type") == "long_lived_access_token":
                    now = int(time.time())
                    payload = {"iss": t["id"], "iat": now, "exp": now + 315360000}
                    self._token = jwt.encode(payload, t["jwt_key"], algorithm="HS256")
                    print(f"[HAFeed] Generated JWT from auth storage")
                    return
            print("[HAFeed] No TokioAI token found in HA auth storage")
        except ImportError:
            print("[HAFeed] PyJWT not installed — set TOKIO_HA_TOKEN env var")
        except Exception as e:
            print(f"[HAFeed] Token generation failed: {e}")

    def _test_connection(self) -> bool:
        if _requests is None:
            print("[HAFeed] requests library not available")
            return False
        try:
            r = _requests.get(f"{HA_URL}/api/", headers=self._headers(), timeout=5)
            if r.status_code == 200:
                print(f"[HAFeed] Connected to Home Assistant at {HA_URL}")
                return True
            print(f"[HAFeed] HA API returned {r.status_code}")
        except Exception as e:
            print(f"[HAFeed] Connection failed: {e}")
        return False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    @property
    def available(self) -> bool:
        return self._available

    def start(self):
        if not self._available:
            return
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()
        print(f"[HAFeed] Started (interval={HA_POLL_INTERVAL}s)")

    def stop(self):
        self._running = False

    def _force_update_entities(self):
        """Force HA to refresh media_player and weather entities (they cache stale data)."""
        try:
            with self._lock:
                eids = list(self._media.keys())
            # Also refresh weather entities
            with self._lock:
                for sid in self._sensors:
                    if "weather" in sid:
                        eids.append(sid.replace("weather.", "", 1) if sid.startswith("weather.weather.") else sid)
            for eid in eids:
                try:
                    _requests.post(
                        f"{HA_URL}/api/services/homeassistant/update_entity",
                        headers=self._headers(),
                        json={"entity_id": eid},
                        timeout=5,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _poll_loop(self):
        time.sleep(3)
        poll_count = 0
        while self._running:
            try:
                # Force refresh media + weather entities every 3rd poll (~30s)
                if poll_count % 3 == 0:
                    self._force_update_entities()
                    time.sleep(1)
                self._poll()
                self._error_count = 0
                poll_count += 1
            except Exception as e:
                self._error_count += 1
                if self._error_count <= 3:
                    print(f"[HAFeed] Poll error: {e}")
                if self._error_count > 10:
                    time.sleep(30)
            time.sleep(HA_POLL_INTERVAL)

    def _poll(self):
        r = _requests.get(f"{HA_URL}/api/states", headers=self._headers(), timeout=8)
        if r.status_code != 200:
            return

        states = r.json()
        media = {}
        sensors = {}
        devices = {}

        for s in states:
            eid = s["entity_id"]
            state = s["state"]
            attrs = s.get("attributes", {})
            name = attrs.get("friendly_name", eid)

            if eid.startswith("media_player.") and state != "unavailable":
                info = {"state": state, "name": name}
                if state in ("playing", "paused"):
                    info["title"] = attrs.get("media_title", "")
                    info["artist"] = attrs.get("media_artist", "")
                    info["album"] = attrs.get("media_album_name", "")
                    info["volume"] = attrs.get("volume_level", 0)
                    info["source"] = attrs.get("source", "")
                media[eid] = info

            elif eid.startswith("sensor.") and state not in ("unavailable", "unknown"):
                if any(k in eid for k in ["temperatura", "temperature", "humidity", "humedad",
                                           "battery", "bateria", "power", "energia",
                                           "sun_next_rising", "sun_next_setting"]):
                    unit = attrs.get("unit_of_measurement", "")
                    val = state
                    # Convert Fahrenheit to Celsius
                    if unit in ("\u00b0F", "°F"):
                        try:
                            val = f"{(float(state) - 32) * 5 / 9:.1f}"
                            unit = "\u00b0C"
                        except ValueError:
                            pass
                    sensors[eid] = {"state": val, "name": name, "unit": unit,
                                    "last_updated": s.get("last_updated", "")}

            elif eid.startswith("weather.") and state != "unavailable":
                sensors[f"weather.{eid}"] = {
                    "state": state, "name": name, "unit": "",
                    "temperature": attrs.get("temperature"),
                    "humidity": attrs.get("humidity"),
                    "wind_speed": attrs.get("wind_speed"),
                    "condition": state,
                }

            elif eid.startswith(("switch.", "light.")) and state != "unavailable":
                devices[eid] = {"state": state, "name": name}

        with self._lock:
            self._media = media
            self._sensors = sensors
            self._devices = devices
            self._last_poll = time.time()

    def get_media(self) -> dict:
        with self._lock:
            return dict(self._media)

    def get_sensors(self) -> dict:
        with self._lock:
            return dict(self._sensors)

    def get_devices(self) -> dict:
        with self._lock:
            return dict(self._devices)

    def get_now_playing(self) -> Optional[str]:
        """Return a short string of what's playing on Jarvis/Alexa."""
        with self._lock:
            for eid, info in self._media.items():
                if info.get("state") == "playing" and info.get("title"):
                    artist = info.get("artist", "")
                    title = info.get("title", "")
                    name = info.get("name", "")
                    if artist:
                        return f"{artist} - {title}"
                    return title
        return None

    def call_service(self, domain: str, service: str, entity_id: str,
                     extra_data: Optional[dict] = None) -> dict:
        """Call a Home Assistant service (turn_on, turn_off, play_media, etc).

        Args:
            domain: e.g. "light", "switch", "media_player"
            service: e.g. "turn_on", "turn_off", "toggle", "play_media"
            entity_id: e.g. "light.laboratorio_laboratorio"
            extra_data: extra fields for the service call payload

        Returns:
            {"ok": True/False, "detail": "..."}
        """
        if not self._available or not self._token:
            return {"ok": False, "detail": "HA not available"}
        if _requests is None:
            return {"ok": False, "detail": "requests not installed"}

        url = f"{HA_URL}/api/services/{domain}/{service}"
        payload = {"entity_id": entity_id}
        if extra_data:
            payload.update(extra_data)

        try:
            r = _requests.post(url, headers=self._headers(), json=payload, timeout=10)
            if r.status_code == 200:
                print(f"[HAFeed] Service {domain}/{service} -> {entity_id}: OK")
                # Force immediate poll to update state
                try:
                    self._poll()
                except Exception:
                    pass
                return {"ok": True, "detail": f"{domain}/{service} executed on {entity_id}"}
            return {"ok": False, "detail": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "detail": str(e)}

    def get_summary(self) -> dict:
        """Full summary for API/core integration."""
        with self._lock:
            media = dict(self._media)
            sensors = dict(self._sensors)
            devices = dict(self._devices)
            last_poll = self._last_poll
            available = self._available

            # Inline now_playing to avoid deadlock (get_now_playing also takes lock)
            now_playing = None
            for eid, info in media.items():
                if info.get("state") == "playing" and info.get("title"):
                    artist = info.get("artist", "")
                    title = info.get("title", "")
                    now_playing = f"{artist} - {title}" if artist else title
                    break

        return {
            "available": available,
            "media": media,
            "sensors": sensors,
            "devices": devices,
            "now_playing": now_playing,
            "last_poll": last_poll,
        }
