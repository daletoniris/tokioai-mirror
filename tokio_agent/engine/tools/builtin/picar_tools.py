"""
TokioAI PiCar-X Robot Tools — Control via Safety Proxy on Raspberry Pi.

All commands go through the PiCar-X proxy (port 5002) on the robot's Raspi.
The proxy handles safety limits, auto-stop, and hardware abstraction.

Actions:
  - status: Full robot status (sensors, servos, motors, mode)
  - sensors: Read ultrasonic, grayscale, battery
  - move: Move in a direction (forward, backward, left, right, stop)
  - camera: Set camera pan/tilt angles
  - look: Look in a named direction (center, left, right, up, down)
  - steer: Set steering angle
  - obstacle_avoid: Autonomous obstacle avoidance mode
  - line_track: Follow a line on the floor
  - patrol: Drive a patrol pattern (square, zigzag, circle)
  - dance: Fun dance with servos and movement
  - servo_test: Test all servos
  - motor_test: Test motors
  - snapshot: Take a photo with PiCar camera
  - calibrate: Set servos to zero for calibration
  - stop: Emergency stop everything
  - kill: Hard emergency stop
  - audit: View command history
  - init: Re-initialize hardware
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# PiCar-X proxy - try LAN first, fallback to Tailscale
PICAR_API = os.getenv("PICAR_API_URL", "")
PICAR_LAN = os.getenv("PICAR_LAN_IP", "192.168.8.107")
PICAR_PORT = int(os.getenv("PICAR_PORT", "5002"))
TIMEOUT = 10.0

_TIMEOUT_MAP = {
    "obstacle_avoid": 30.0,
    "line_track": 30.0,
    "patrol": 30.0,
    "dance": 20.0,
    "move": 15.0,
    "servo_test": 10.0,
    "motor_test": 10.0,
    "snapshot": 15.0,
}


def _get_base_url() -> str:
    """Get the PiCar API base URL."""
    if PICAR_API:
        return PICAR_API
    return f"http://{PICAR_LAN}:{PICAR_PORT}"


def _enrich_error(e: Exception, path: str) -> str:
    """Provide actionable error messages."""
    err = str(e)
    if "Connection refused" in err or "connect" in err.lower():
        return (f"PiCar-X proxy no responde ({path}). "
                "Posibles causas: 1) Raspberry Pi del robot apagada. "
                "2) Servicio picar-proxy no iniciado — conectarse por SSH y ejecutar "
                "'sudo systemctl start picar-proxy'. "
                "3) IP incorrecta — verificar PICAR_LAN_IP.")
    if "timeout" in err.lower():
        return (f"Timeout en PiCar-X proxy ({path}). "
                "El robot puede estar ejecutando un comando largo.")
    return f"Error PiCar-X: {err}"


async def _api(method: str, path: str, data: dict = None,
               timeout: float = None) -> dict:
    """Make request to PiCar-X safety proxy."""
    base = _get_base_url()
    try:
        async with httpx.AsyncClient(timeout=timeout or TIMEOUT) as client:
            if method == "GET":
                r = await client.get(f"{base}{path}")
            else:
                r = await client.post(f"{base}{path}", json=data or {})
            # Handle binary (snapshot)
            if r.headers.get("content-type", "").startswith("image/"):
                return {"snapshot": True, "size": len(r.content),
                        "content_type": r.headers["content-type"]}
            return r.json()
    except Exception as e:
        return {"error": _enrich_error(e, path)}


async def picar_control(action: str, params: dict = None) -> str:
    """Unified PiCar-X robot control handler."""
    params = params or {}
    timeout = _TIMEOUT_MAP.get(action, TIMEOUT)

    # ── Status & Sensors ──
    if action == "status":
        r = await _api("GET", "/status", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        hw_icon = "✅" if r.get("initialized") else "❌"
        hw_text = "online" if r.get("initialized") else "not initialized"
        if r.get("moving"):
            move_text = "🟢 {} @ {}%".format(r.get("direction", "?"), r.get("speed", 0))
        else:
            move_text = "⚪ stopped"
        lines = [
            "🤖 PiCar-X Status",
            "  Hardware: {} {}".format(hw_icon, hw_text),
            "  Moving: {}".format(move_text),
            f"  Steering: {r.get('steering', 0)}°",
            f"  Camera: pan={r.get('cam_pan', 0)}° tilt={r.get('cam_tilt', 0)}°",
            f"  Autonomous: {r.get('autonomous_mode') or 'none'}",
            f"  Ultrasonic: {r.get('ultrasonic_cm', -1)} cm",
            f"  Grayscale: {r.get('grayscale', [])}",
            f"  Battery: {r.get('battery_v', -1)}V",
            f"  Speed limit: {r.get('max_speed', 50)}%",
            f"  Uptime: {r.get('uptime_s', 0)}s | Commands: {r.get('commands', 0)}",
        ]
        return "\n".join(lines)

    elif action == "sensors":
        r = await _api("GET", "/sensors", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        lines = [
            f"📡 Sensores PiCar-X",
            f"  Ultrasonido: {r.get('ultrasonic_cm', -1)} cm",
            f"  Grayscale: {r.get('grayscale', [])}",
            f"  Batería: {r.get('battery_v', -1)}V",
        ]
        return "\n".join(lines)

    # ── Movement ──
    elif action == "move":
        direction = params.get("direction", "forward")
        speed = params.get("speed", 30)
        duration = params.get("duration", 1.0)
        angle = params.get("angle", 0)
        r = await _api("POST", "/move", {
            "direction": direction, "speed": speed,
            "duration": duration, "angle": angle
        }, timeout=max(timeout, duration + 5))
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🚗 Movimiento: {direction} @ {speed}% por {duration}s" + \
               (f" (ángulo: {angle}°)" if angle else "")

    elif action in ("stop", "parar"):
        r = await _api("POST", "/stop", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return "🛑 PiCar-X detenido"

    elif action in ("kill", "emergencia"):
        r = await _api("POST", "/kill", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return "🚨 EMERGENCY STOP — todo detenido, servos centrados"

    # ── Camera ──
    elif action == "camera":
        pan = params.get("pan")
        tilt = params.get("tilt")
        r = await _api("POST", "/camera", {"pan": pan, "tilt": tilt}, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"📷 Cámara: pan={r.get('pan', 0)}° tilt={r.get('tilt', 0)}°"

    elif action in ("look", "mirar"):
        direction = params.get("direction", "center")
        r = await _api("POST", "/camera/look", {"direction": direction}, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"👁️ Mirando: {direction} (pan={r.get('pan', 0)}° tilt={r.get('tilt', 0)}°)"

    # ── Steering ──
    elif action in ("steer", "girar"):
        angle = params.get("angle", 0)
        r = await _api("POST", "/steer", {"angle": angle}, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🔄 Dirección: {angle}°"

    # ── Autonomous Modes ──
    elif action in ("obstacle_avoid", "esquivar"):
        duration = params.get("duration", 20)
        speed = params.get("speed", 30)
        r = await _api("POST", "/obstacle_avoid", {
            "duration": duration, "speed": speed
        }, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🚧 Esquivando obstáculos: {duration}s @ {speed}%"

    elif action in ("line_track", "seguir_linea"):
        duration = params.get("duration", 30)
        speed = params.get("speed", 25)
        r = await _api("POST", "/line_track", {
            "duration": duration, "speed": speed
        }, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"➖ Siguiendo línea: {duration}s @ {speed}%"

    elif action in ("patrol", "patrullar"):
        duration = params.get("duration", 30)
        speed = params.get("speed", 25)
        pattern = params.get("pattern", "square")
        r = await _api("POST", "/patrol", {
            "duration": duration, "speed": speed, "pattern": pattern
        }, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🔄 Patrullando: patrón {pattern}, {duration}s @ {speed}%"

    elif action in ("dance", "bailar"):
        r = await _api("POST", "/dance", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return "💃 PiCar-X bailando!"

    # ── Tests ──
    elif action == "servo_test":
        r = await _api("POST", "/servo_test", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        results = r.get("results", [])
        return f"🔧 Servo test: {', '.join(results)}"

    elif action == "motor_test":
        speed = params.get("speed", 25)
        r = await _api("POST", "/motor_test", {"speed": speed}, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"⚙️ Motor test: adelante + atrás @ {speed}%"

    # ── Snapshot ──
    elif action in ("snapshot", "foto"):
        r = await _api("GET", "/snapshot", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"📸 Foto tomada ({r.get('size', 0)} bytes)"

    # ── Calibration ──
    elif action in ("calibrate", "calibrar"):
        servo = params.get("servo", "all")
        angle = params.get("angle", 0)
        r = await _api("POST", "/calibrate", {"servo": servo, "angle": angle}, timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🔧 Calibración: {servo} → {angle}°"

    # ── Init ──
    elif action == "init":
        r = await _api("POST", "/init", timeout=timeout)
        if "error" in r:
            return f"Error: {r['error']}"
        return f"🔄 Re-init: {'✅ hardware OK' if r.get('hardware') else '❌ failed'}"

    # ── Audit ──
    elif action in ("audit", "historial"):
        limit = params.get("limit", 20)
        r = await _api("GET", f"/audit?limit={limit}", timeout=timeout)
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        if isinstance(r, list):
            lines = [f"📋 Últimos {len(r)} comandos:"]
            for e in r[-10:]:
                lines.append(f"  [{e.get('ts','')}] {e.get('action','')} {e.get('params',{})} → {e.get('result','')}")
            return "\n".join(lines)
        return str(r)

    else:
        return (f"Acción desconocida: {action}. "
                "Acciones válidas: status, sensors, move, stop, kill, camera, look, steer, "
                "obstacle_avoid, line_track, patrol, dance, servo_test, motor_test, "
                "snapshot, calibrate, init, audit")
