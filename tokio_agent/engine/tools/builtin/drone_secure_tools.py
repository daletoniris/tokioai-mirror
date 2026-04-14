"""
TokioAI Secure Drone Tools — Commands go through the Safety Proxy.

All drone commands are sent to the Raspi Safety Proxy via Tailscale.
The proxy validates geofence, authentication, rate limits, and safety
before forwarding to the actual drone.

Vision actions (visual servoing) go to the Entity API on port 5000.

This replaces direct drone control — NEVER bypass the proxy.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Dict

import httpx

logger = logging.getLogger(__name__)

# Raspi drone API via Tailscale (encrypted)
DRONE_API = os.getenv("DRONE_API_URL", "")
RASPI_API = os.getenv("RASPI_API_URL", "")
TIMEOUT = 15.0


def _enrich_drone_error(e: Exception, path: str) -> str:
    """Provide actionable error messages for drone operations."""
    err = str(e)
    if "Connection refused" in err or "connect" in err.lower():
        return (f"Drone proxy no responde ({path}). "
                "Posibles causas: 1) Raspberry Pi apagada — requiere encendido físico. "
                "2) Servicio tokio-drone-proxy caído — intentar self_heal(action='check'). "
                "3) WiFi del drone no conectado — usar drone(action='wifi_connect').")
    if "timeout" in err.lower():
        return (f"Timeout en drone proxy ({path}). "
                "El drone puede estar procesando un comando largo o desconectado.")
    return f"Error drone: {err}"


# Timeout map for different operations
_TIMEOUT_MAP = {
    "takeoff": 20.0,
    "land": 15.0,
    "patrol": 30.0,
    "dance": 25.0,
    "move": 15.0,
    "rotate": 10.0,
    "snapshot": 10.0,
    "wifi_connect": 25.0,
    "wifi_disconnect": 20.0,
}


async def _api(method: str, path: str, data: dict = None, timeout: float = None) -> dict:
    """Make request to drone safety proxy."""
    if not DRONE_API:
        return {"error": "DRONE_API_URL no configurada. Verificar variables de entorno del agente."}
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            if method == "GET":
                r = await client.get(f"{DRONE_API}{path}")
            else:
                r = await client.post(f"{DRONE_API}{path}", json=data or {})
            return r.json()
    except Exception as e:
        return {"error": _enrich_drone_error(e, path)}


async def _entity_api(method: str, path: str, data: dict = None, timeout: float = None) -> dict:
    """Make request to entity API (for vision endpoints)."""
    if not RASPI_API:
        return {"error": "RASPI_API_URL no configurada. Verificar variables de entorno del agente."}
    try:
        async with httpx.AsyncClient(timeout=timeout or 5.0) as client:
            if method == "GET":
                r = await client.get(f"{RASPI_API}{path}")
            else:
                r = await client.post(f"{RASPI_API}{path}", json=data or {})
            return r.json()
    except Exception as e:
        return {"error": _enrich_drone_error(e, path)}


async def _entity_api_raw(method: str, path: str, data: dict = None, timeout: float = None):
    """Make raw request to entity API (for binary data like FPV snapshots)."""
    try:
        async with httpx.AsyncClient(timeout=timeout or 10.0) as client:
            if method == "GET":
                r = await client.get(f"{RASPI_API}{path}")
            else:
                r = await client.post(f"{RASPI_API}{path}", json=data or {})
            return r
    except Exception:
        return None


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

    # ── WiFi management ───────────────────────────────────────────────
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

    # ── Drone Vision (visual servoing from entity camera) ─────────────
    elif action in ("vision_status", "vision"):
        r = await _entity_api("GET", "/drone/vision/status")
        if "error" in r:
            return f"Error vision: {r['error']}"
        lines = [
            f"Mode: {r.get('mode', 'idle')}",
            f"Registered: {r.get('registered', False)}",
            f"Detected: {r.get('detected', False)}",
        ]
        if r.get("distance_cm"):
            lines.append(f"Distance: {r['distance_cm']}cm")
        if r.get("confidence"):
            lines.append(f"Confidence: {r['confidence']}")
        if r.get("offset"):
            lines.append(f"Offset: X={r['offset'][0]}, Y={r['offset'][1]}")
        return "\n".join(lines)

    elif action in ("vision_register", "registrar_drone", "aprender_drone", "register_drone"):
        # Check if already registered
        status = await _entity_api("GET", "/drone/vision/status")
        if status.get("registered"):
            return f"El drone YA esta registrado y lo reconozco. Mode: {status.get('mode')}, Detected: {status.get('detected')}. Usa come_to_me/hover/dance para jugar, o vision_idle para desactivar."
        if status.get("mode") == "register":
            return f"Ya estoy registrando... Confidence: {status.get('confidence', 0)}. Espera 2 segundos con el drone quieto frente a la camara."
        # Fresh registration
        await _entity_api("POST", "/drone/vision/reset")
        r = await _entity_api("POST", "/drone/vision/register")
        if "error" in r:
            return f"Error: {r['error']}"
        return "Registro visual iniciado. Mostra el drone a la camara de Tokio, centrado, a ~40cm. Mantenelo quieto 2 segundos. Cuando este listo te aviso (usa vision_status para verificar)."

    elif action in ("vision_mode", "modo_vision"):
        mode = params.get("mode", "track")
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": mode})
        if "error" in r:
            return f"Error: {r['error']}"
        return f"Drone vision modo: {mode}"

    elif action in ("come_to_me", "veni", "acercate"):
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": "come_to_me"})
        if "error" in r:
            return f"Error: {r['error']}"
        return "El drone viene hacia mi! Lo estoy guiando con la camara."

    elif action in ("hover", "quieto", "quedate"):
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": "hover"})
        if "error" in r:
            return f"Error: {r['error']}"
        return "Drone en modo hover. Lo mantengo centrado en mi vision."

    elif action in ("dance", "bailar", "baila"):
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": "dance"})
        if "error" in r:
            return f"Error: {r['error']}"
        return "Modo baile activado! El drone baila mientras yo lo miro."

    elif action in ("patrol_vision", "patrullar_vision"):
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": "patrol"})
        if "error" in r:
            return f"Error: {r['error']}"
        return "Drone patrullando. Lo vigilo con la camara."

    elif action in ("vision_idle", "dejar_drone", "parar_vision"):
        r = await _entity_api("POST", "/drone/vision/mode", {"mode": "idle"})
        if "error" in r:
            return f"Error: {r['error']}"
        return "Vision del drone desactivada."

    # ── FPV — Drone's own camera ──────────────────────────────────────
    elif action in ("fpv_start", "fpv_on", "ojo_volador"):
        r = await _entity_api("POST", "/drone/fpv/start")
        if "error" in r:
            return f"Error FPV: {r['error']}"
        return "FPV activo. Veo a traves del drone. Modo follow activado."

    elif action in ("fpv_stop", "fpv_off"):
        r = await _entity_api("POST", "/drone/fpv/stop")
        if "error" in r:
            return f"Error: {r['error']}"
        return "FPV desactivado."

    elif action in ("fpv_status", "fpv"):
        r = await _entity_api("GET", "/drone/fpv/status")
        if "error" in r:
            return f"Error: {r['error']}"
        parts = [f"FPV: {'ACTIVO' if r.get('streaming') else 'INACTIVO'}"]
        parts.append(f"Modo: {r.get('mode', '?')}")
        parts.append(f"FPS: {r.get('fps', 0)}")
        parts.append(f"Personas: {r.get('persons', 0)}")
        if r.get("target"):
            t = r["target"]
            parts.append(f"Target: {t['distance_cm']}cm ({t['frame_pct']*100:.0f}% frame)")
        if r.get("obstacle_ahead"):
            parts.append(f"OBSTACULO: {r['closest_obstacle_cm']}cm")
        return " | ".join(parts)

    elif action in ("fpv_mode", "fpv_follow", "fpv_explore", "fpv_hover"):
        fpv_mode = action.replace("fpv_", "") if action.startswith("fpv_") and action != "fpv_mode" else params.get("mode", "follow")
        r = await _entity_api("POST", "/drone/fpv/mode", {"mode": fpv_mode})
        if "error" in r:
            return f"Error: {r['error']}"
        return f"FPV modo: {fpv_mode}"

    elif action in ("fpv_snapshot", "fpv_foto"):
        import base64
        import tempfile
        r = await _entity_api_raw("GET", "/drone/fpv/snapshot")
        if r is None:
            return "Error: no se pudo obtener frame FPV"
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image/"):
            output_dir = "/workspace/output"
            os.makedirs(output_dir, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".jpg", prefix="fpv_", delete=False, dir=output_dir
            )
            tmp.write(r.content)
            tmp.close()
            return f"PHOTO:{tmp.name}"
        return f"Error FPV snapshot: HTTP {r.status_code}"

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


    # ── Demo Flight ───────────────────────────────────────────────────
    elif action in ("demo", "demo_flight", "vuelo_demo"):
        pattern = params.get("pattern", "demo")
        speed = int(params.get("speed", 2))
        r = await _entity_api("POST", "/drone/demo", {"pattern": pattern, "speed": speed})
        if "error" in r:
            return f"Error demo: {r['error']}"
        return f"Demo flight '{pattern}' started! Speed: {speed}"

    elif action in ("demo_status", "demo_estado"):
        r = await _entity_api("GET", "/drone/demo/status")
        if "error" in r:
            return f"Error: {r['error']}"
        if r.get("running"):
            return f"Demo running: {r.get('pattern')} (step {r.get('step')}/{r.get('total_steps')})"
        return "No demo flight running"

    elif action in ("demo_cancel", "demo_parar", "cancelar_demo"):
        r = await _entity_api("POST", "/drone/demo/cancel")
        if "error" in r:
            return f"Error: {r['error']}"
        return "Demo flight cancelled"

    # ── Language ──────────────────────────────────────────────────────
    elif action in ("lang", "idioma", "language"):
        lang = params.get("lang", "en")
        r = await _entity_api("POST", "/ai/lang", {"lang": lang})
        if "error" in r:
            return f"Error: {r['error']}"
        return f"Language switched to: {lang}"

    # ── Vision Summary ────────────────────────────────────────────────
    elif action in ("vision_summary", "que_ves", "what_see"):
        r = await _entity_api("GET", "/vision/summary")
        if "error" in r:
            return f"Error: {r['error']}"
        parts = []
        if r.get("detections"):
            parts.append("Detections: " + ", ".join(f"{d['label']}({d['confidence']})" for d in r["detections"]))
        parts.append(f"Persons: {r.get('persons', 0)}")
        if r.get("face_recognized"):
            parts.append(f"Face: {r['face_recognized']}")
        if r.get("identities"):
            parts.append("Known: " + ", ".join(f"{i['name']}({i['confidence']})" for i in r["identities"]))
        if r.get("last_gesture"):
            parts.append(f"Gesture: {r['last_gesture']}")
        if r.get("ai_thought"):
            parts.append(f"Thought: {r['ai_thought'][:80]}")
        parts.append(f"FPS: {r.get('fps', 0)}")
        return " | ".join(parts)

    else:
        # All other commands (connect, takeoff, land, move, rotate, patrol, etc.)
        # go through the safety proxy — never bypass it
        timeout = _TIMEOUT_MAP.get(action, TIMEOUT)

        r = await _api("POST", "/drone/command", {
            "command": action,
            "params": params,
        }, timeout=timeout)

        if "error" in r:
            return f"Error: {r['error']}"

        result = r.get("result", "")
        if r.get("blocked"):
            reason = r.get("reason", result)
            return f"⚠️ BLOCKED by safety proxy: {reason}"

        # Enrich response for key commands
        if action in ("takeoff", "despegar") and "ok" in str(result).lower():
            return f"✅ Drone despegó exitosamente. Altura: ~120cm. Battery: {r.get('battery', '?')}%"
        elif action in ("land", "aterrizar") and "ok" in str(result).lower():
            return f"✅ Drone aterrizó exitosamente."
        elif action in ("connect", "conectar") and "ok" in str(result).lower():
            return f"✅ Drone conectado y listo. Battery: {r.get('battery', '?')}%"

        return result if result else str(r)
