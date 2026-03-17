"""
Drone Control Tools — Routes commands through Raspi Safety Proxy.

Instead of using djitellopy directly, ALL commands go through the
drone safety proxy running on the Raspberry Pi (port 5001).

Flow: TokioAI (GCP) --[Tailscale]--> Raspi Safety Proxy --> Tello drone

The proxy handles: geofencing, rate limiting, kill switch, auto-land,
command validation, IP authorization, and audit logging.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# Raspi drone proxy endpoint (via Tailscale)
DRONE_PROXY_URL = os.getenv(
    "DRONE_PROXY_URL", "http://YOUR_RASPI_TAILSCALE_IP:5001"
)
DRONE_PROXY_TIMEOUT = int(os.getenv("DRONE_PROXY_TIMEOUT", "15"))


def _proxy_post(endpoint: str, data: dict = None, timeout: int = None) -> dict:
    """POST to the drone safety proxy."""
    url = f"{DRONE_PROXY_URL}{endpoint}"
    t = timeout or DRONE_PROXY_TIMEOUT
    try:
        r = requests.post(url, json=data or {}, timeout=t)
        return r.json()
    except requests.ConnectionError:
        return {"error": "No se puede conectar al proxy del drone en la Raspi. Verificar que esta encendida y el servicio tokio-drone-proxy activo."}
    except requests.Timeout:
        return {"error": f"Timeout ({t}s) esperando respuesta del proxy del drone."}
    except Exception as e:
        return {"error": f"Error comunicando con proxy: {e}"}


def _proxy_get(endpoint: str, timeout: int = None) -> dict:
    """GET from the drone safety proxy."""
    url = f"{DRONE_PROXY_URL}{endpoint}"
    t = timeout or DRONE_PROXY_TIMEOUT
    try:
        r = requests.get(url, timeout=t)
        return r.json()
    except requests.ConnectionError:
        return {"error": "No se puede conectar al proxy del drone en la Raspi."}
    except requests.Timeout:
        return {"error": f"Timeout ({t}s) esperando respuesta del proxy."}
    except Exception as e:
        return {"error": f"Error: {e}"}


def _send_command(command: str, params: dict = None) -> str:
    """Send a command through the safety proxy."""
    data = {"command": command}
    if params:
        data["params"] = params
    result = _proxy_post("/drone/command", data, timeout=20)

    if "error" in result:
        return json.dumps({"ok": False, "error": result["error"]}, ensure_ascii=False, indent=2)

    blocked = result.get("blocked", False)
    return json.dumps({
        "ok": not blocked,
        "command": command,
        "result": result.get("result", ""),
        "blocked": blocked,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Individual command handlers (same interface as old drone_tools.py)
# ---------------------------------------------------------------------------

def drone_connect(params: Dict[str, Any]) -> str:
    """Connect to the Tello drone (via Raspi proxy auto-connect)."""
    return _send_command("connect", params)

def drone_disconnect(params: Dict[str, Any]) -> str:
    return _send_command("disconnect")

def drone_takeoff(params: Dict[str, Any]) -> str:
    """Despegar el drone."""
    return _send_command("takeoff")

def drone_land(params: Dict[str, Any]) -> str:
    """Aterrizar el drone."""
    return _send_command("land")

def drone_emergency(params: Dict[str, Any]) -> str:
    """Parada de emergencia — corta motores inmediatamente."""
    result = _proxy_post("/drone/kill")
    return json.dumps({"ok": True, "result": result.get("result", "Kill switch activated")}, ensure_ascii=False, indent=2)

def drone_move(params: Dict[str, Any]) -> str:
    """Mover el drone. params: direction (forward/back/left/right/up/down), distance (cm)."""
    direction = params.get("direction", "forward")
    distance = params.get("distance", 50)
    return _send_command("move", {"direction": direction, "distance": distance})

def drone_rotate(params: Dict[str, Any]) -> str:
    """Rotar el drone. params: direction (clockwise/counter_clockwise), degrees."""
    direction = params.get("direction", "clockwise")
    degrees = params.get("degrees", 90)
    return _send_command("rotate", {"direction": direction, "degrees": degrees})

def drone_status(params: Dict[str, Any]) -> str:
    """Estado completo del drone y proxy de seguridad."""
    status = _proxy_get("/drone/status")
    if "error" in status:
        return json.dumps({"ok": False, "error": status["error"]}, ensure_ascii=False, indent=2)

    # Also get battery/telemetry if connected
    if status.get("connected"):
        battery_result = _proxy_post("/drone/command", {"command": "battery"})
        status["battery_info"] = battery_result.get("result", "unknown")

    return json.dumps({"ok": True, **status}, ensure_ascii=False, indent=2)

def drone_battery(params: Dict[str, Any]) -> str:
    """Nivel de bateria del drone."""
    return _send_command("battery")

def drone_telemetry(params: Dict[str, Any]) -> str:
    """Telemetria completa del drone."""
    return _send_command("telemetry")

def drone_patrol(params: Dict[str, Any]) -> str:
    """Patrulla automatica. params: pattern (square/triangle/line), size (cm)."""
    pattern = params.get("pattern", "square")
    size = params.get("size", 100)
    return _send_command("patrol", {"pattern": pattern, "size": size})

def drone_stream_on(params: Dict[str, Any]) -> str:
    return _send_command("stream_on")

def drone_stream_off(params: Dict[str, Any]) -> str:
    return _send_command("stream_off")

def drone_flight_log(params: Dict[str, Any]) -> str:
    """Ver audit log del drone."""
    n = params.get("limit", 20)
    result = _proxy_get(f"/drone/audit?n={n}")
    return json.dumps(result, ensure_ascii=False, indent=2)

def drone_geofence(params: Dict[str, Any]) -> str:
    """Ver configuracion del geofence."""
    result = _proxy_get("/drone/geofence")
    return json.dumps(result, ensure_ascii=False, indent=2)

def drone_kill_reset(params: Dict[str, Any]) -> str:
    """Resetear kill switch despues de emergencia."""
    result = _proxy_post("/drone/kill/reset")
    return json.dumps({"ok": True, "result": result.get("result", "")}, ensure_ascii=False, indent=2)

def drone_wifi_connect(params: Dict[str, Any]) -> str:
    """Conectar la Raspi al WiFi del drone (T0K10-NET). Paso necesario antes de volar."""
    result = _proxy_post("/drone/wifi/connect", timeout=20)
    if "error" in result:
        return json.dumps({"ok": False, "error": result["error"]}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": result.get("ok", False), "result": result.get("result", "")}, ensure_ascii=False, indent=2)

def drone_wifi_disconnect(params: Dict[str, Any]) -> str:
    """Desconectar del WiFi del drone y volver a la red principal."""
    result = _proxy_post("/drone/wifi/disconnect", timeout=20)
    if "error" in result:
        return json.dumps({"ok": False, "error": result["error"]}, ensure_ascii=False, indent=2)
    return json.dumps({"ok": result.get("ok", False), "result": result.get("result", "")}, ensure_ascii=False, indent=2)

def drone_wifi_status(params: Dict[str, Any]) -> str:
    """Ver estado actual del WiFi de la Raspi (conectado al drone o no)."""
    result = _proxy_get("/drone/wifi/status")
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main dispatcher (same interface as old drone_control)
# ---------------------------------------------------------------------------

def drone_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Control del drone DJI Tello via proxy seguro en Raspberry Pi.

    Todos los comandos pasan por el Safety Proxy que valida:
    - Geofencing (altura max, distancia max)
    - Rate limiting
    - Kill switch
    - Auto-land por bateria baja o timeout

    Acciones disponibles:
      Vuelo: connect, takeoff/despegar, land/aterrizar, emergency/emergencia
      Movimiento: move/mover (direction + distance), rotate/rotar (direction + degrees)
      Patrones: patrol/patrullar (pattern + size)
      Telemetria: status/estado, battery/bateria, telemetry/telemetria
      Camara: stream_on, stream_off
      Seguridad: flight_log, geofence, kill_reset
      WiFi: wifi_connect/conectar_wifi, wifi_disconnect/desconectar_wifi, wifi_status

      IMPORTANTE: Antes de volar hay que ejecutar wifi_connect para conectar al drone.
      Despues de volar, wifi_disconnect para volver a la red normal.
    """
    params = params or {}
    action = (action or "").strip().lower()

    handlers = {
        # Connection
        "connect": drone_connect,
        "disconnect": drone_disconnect,
        # Flight
        "takeoff": drone_takeoff,
        "despegar": drone_takeoff,
        "land": drone_land,
        "aterrizar": drone_land,
        "emergency": drone_emergency,
        "emergencia": drone_emergency,
        # Movement
        "move": drone_move,
        "mover": drone_move,
        "rotate": drone_rotate,
        "rotar": drone_rotate,
        # Patterns
        "patrol": drone_patrol,
        "patrullar": drone_patrol,
        # Camera
        "stream_on": drone_stream_on,
        "stream_off": drone_stream_off,
        # Telemetry
        "status": drone_status,
        "estado": drone_status,
        "battery": drone_battery,
        "bateria": drone_battery,
        "telemetry": drone_telemetry,
        "telemetria": drone_telemetry,
        # Security
        "flight_log": drone_flight_log,
        "geofence": drone_geofence,
        "kill_reset": drone_kill_reset,
        # WiFi management
        "wifi_connect": drone_wifi_connect,
        "conectar_wifi": drone_wifi_connect,
        "conectar_drone": drone_wifi_connect,
        "wifi_disconnect": drone_wifi_disconnect,
        "desconectar_wifi": drone_wifi_disconnect,
        "desconectar_drone": drone_wifi_disconnect,
        "wifi_status": drone_wifi_status,
    }

    handler = handlers.get(action)
    if handler is None:
        return json.dumps({
            "ok": False,
            "error": f"Accion no soportada: {action}",
            "supported": sorted(set(handlers.keys())),
        }, ensure_ascii=False, indent=2)

    try:
        return handler(params)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"Error ejecutando {action}: {exc}",
        }, ensure_ascii=False, indent=2)
