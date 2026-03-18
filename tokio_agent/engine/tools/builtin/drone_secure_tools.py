"""
TokioAI Secure Drone Tools — Commands go through the Safety Proxy.

All drone commands are sent to the Raspi Safety Proxy via Tailscale.
The proxy validates geofence, authentication, rate limits, and safety
before forwarding to the actual drone.

This replaces direct drone control — NEVER bypass the proxy.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Dict

import httpx

logger = logging.getLogger(__name__)

# Raspi drone API via Tailscale (encrypted)
DRONE_API = os.getenv("DRONE_API_URL", "http://YOUR_RASPI_TAILSCALE_IP:5001")
TIMEOUT = 15.0


async def _api(method: str, path: str, data: dict = None, timeout: float = None) -> dict:
    """Make request to drone safety proxy."""
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            if method == "GET":
                r = await client.get(f"{DRONE_API}{path}")
            else:
                r = await client.post(f"{DRONE_API}{path}", json=data or {})
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def _api_raw(method: str, path: str, data: dict = None, timeout: float = None):
    """Make request and return raw response (for binary data like photos)."""
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            if method == "GET":
                r = await client.get(f"{DRONE_API}{path}")
            else:
                r = await client.post(f"{DRONE_API}{path}", json=data or {})
            return r
    except Exception as e:
        return None


async def drone_secure(action: str = "status", params: dict = None, **kwargs) -> str:
    """Unified secure drone control handler."""
    params = params or {}

    if action == "status":
        r = await _api("GET", "/drone/status")
        if "error" in r:
            return f"Drone proxy unreachable: {r['error']}"
        lines = [
            f"Safety Level: {r.get('safety_level', '?')}",
            f"Kill Switch: {'ACTIVE' if r.get('kill_switch') else 'off'}",
            f"Armed: {'YES (in flight)' if r.get('armed') else 'no'}",
            f"Connected: {'YES' if r.get('connected') else 'no'}",
            f"Watchdog: {'running' if r.get('watchdog_running') else 'stopped'}",
        ]
        pos = r.get("position", {})
        lines.append(f"Position: x={pos.get('x', 0)} y={pos.get('y', 0)} z={pos.get('z', 0)} cm")
        gf = r.get("geofence", {})
        lines.append(f"Geofence: {gf.get('max_distance_cm', 0)}cm radius, {gf.get('max_height_cm', 0)}cm height")
        audit = r.get("audit", {})
        lines.append(f"Commands: {audit.get('total_commands', 0)} total, {audit.get('blocked_commands', 0)} blocked")
        return "\n".join(lines)

    elif action == "kill":
        r = await _api("POST", "/drone/kill")
        return r.get("result", str(r))

    elif action == "kill_reset":
        r = await _api("POST", "/drone/kill/reset")
        return r.get("result", str(r))

    elif action == "audit":
        r = await _api("GET", "/drone/audit")
        if isinstance(r, list):
            if not r:
                return "No audit entries"
            lines = []
            for e in r[-15:]:
                status = "BLOCKED" if e.get("blocked") else "OK"
                lines.append(f"[{e.get('time', '?')}] {status} {e.get('cmd', '?')} from {e.get('ip', '?')}"
                             + (f" — {e.get('reason', '')}" if e.get("reason") else ""))
            return "\n".join(lines)
        return str(r)

    elif action == "geofence":
        r = await _api("GET", "/drone/geofence")
        if "error" in r:
            return f"Error: {r['error']}"
        lines = [
            f"Max height: {r.get('max_height_cm', '?')} cm",
            f"Max distance: {r.get('max_distance_cm', '?')} cm",
            f"Max speed: {r.get('max_speed_cms', '?')} cm/s",
        ]
        boundary = r.get("boundary", {})
        if boundary:
            lines.append(f"Boundary X: {boundary.get('x', '?')}")
            lines.append(f"Boundary Y: {boundary.get('y', '?')}")
        return "\n".join(lines)

    # ── WiFi management (separate endpoints on proxy) ─────────────────
    elif action in ("wifi_connect", "conectar_wifi", "conectar_drone"):
        r = await _api("POST", "/drone/wifi/connect", timeout=25.0)
        if "error" in r:
            return f"Error WiFi: {r['error']}"
        return r.get("result", str(r))

    elif action in ("wifi_disconnect", "desconectar_wifi", "desconectar_drone"):
        r = await _api("POST", "/drone/wifi/disconnect", timeout=20.0)
        if "error" in r:
            return f"Error WiFi: {r['error']}"
        return r.get("result", str(r))

    elif action == "wifi_status":
        r = await _api("GET", "/drone/wifi/status")
        if "error" in r:
            return f"Error: {r['error']}"
        return str(r)

    # ── Snapshot (returns binary JPEG) ────────────────────────────────
    elif action in ("snapshot", "selfie", "foto", "photo", "take_photo"):
        import base64
        import tempfile
        r = await _api_raw("POST", "/drone/snapshot", timeout=30.0)
        if r is None:
            return "Error: no se pudo conectar al proxy del drone"
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image/"):
            output_dir = "/workspace/output"
            os.makedirs(output_dir, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".jpg", prefix="drone_selfie_", delete=False, dir=output_dir
            )
            tmp.write(r.content)
            tmp.close()
            return f"PHOTO:{tmp.name}"
        try:
            err = r.json()
            return f"Error snapshot: {err.get('error', r.text)}"
        except Exception:
            return f"Error snapshot: HTTP {r.status_code}"

    else:
        # All other commands go through the proxy
        r = await _api("POST", "/drone/command", {
            "command": action,
            "params": params,
        })
        if "error" in r:
            return f"Error: {r['error']}"
        result = r.get("result", "")
        if r.get("blocked"):
            return f"BLOCKED by safety proxy: {result}"
        return result
