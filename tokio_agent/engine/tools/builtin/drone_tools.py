"""
Drone Control Tools - DJI Tello integration via djitellopy.

Full drone control: flight, camera, telemetry, emergency.
Supports SIMULATION MODE (no hardware needed) and REAL mode.

Requires (real mode only): djitellopy (pip install djitellopy)
Network (real mode only): must be connected to Tello WiFi or Tello in station mode.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Singleton drone instance (real or simulated)
_drone = None
_drone_lock = threading.Lock()
_flight_log: list = []
_simulation_mode = False


def _log_action(action: str, detail: str = "") -> None:
    mode = "[SIM] " if _simulation_mode else ""
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "action": f"{mode}{action}",
        "detail": detail,
    }
    _flight_log.append(entry)
    if len(_flight_log) > 200:
        _flight_log.pop(0)


# ===========================================================================
# Simulated Drone — full state tracking without hardware
# ===========================================================================

class SimulatedTello:
    """Simulates a DJI Tello drone with realistic state tracking."""

    RESOLUTION_480P = "low"
    RESOLUTION_720P = "high"
    FPS_5 = "low"
    FPS_15 = "middle"
    FPS_30 = "high"
    BITRATE_AUTO = 0
    CAMERA_FORWARD = 0
    CAMERA_DOWNWARD = 1

    def __init__(self, host: str = "192.168.10.1"):
        self.host = host
        self.is_flying = False
        self.stream_on = False
        self._connected = False
        self._motors_on = False
        self._mission_pads_enabled = False

        # Position & attitude (cm, degrees)
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0  # height
        self._yaw = 0.0
        self._pitch = 0.0
        self._roll = 0.0

        # Speeds
        self._vx = 0
        self._vy = 0
        self._vz = 0
        self._speed = 50  # cm/s default

        # Environment
        self._battery = random.randint(75, 100)
        self._temp_low = random.randint(25, 35)
        self._temp_high = self._temp_low + random.randint(3, 8)
        self._baro = round(random.uniform(100.0, 200.0), 2)
        self._flight_time = 0
        self._takeoff_time = None

        # Video settings
        self._resolution = self.RESOLUTION_720P
        self._fps = self.FPS_30
        self._bitrate = self.BITRATE_AUTO
        self._camera_dir = self.CAMERA_FORWARD

        # Path recording
        self._path: list = []

        logger.info("[SIM] Tello simulado creado (host=%s)", host)

    def _drain_battery(self, amount: float = 0.3) -> None:
        self._battery = max(0, self._battery - amount)

    def _update_flight_time(self) -> None:
        if self._takeoff_time:
            self._flight_time = int(time.time() - self._takeoff_time)

    def _record_position(self, action: str) -> None:
        self._path.append({
            "action": action,
            "x": round(self._x, 1),
            "y": round(self._y, 1),
            "z": round(self._z, 1),
            "yaw": round(self._yaw, 1),
            "battery": round(self._battery, 1),
        })

    def _move_relative(self, dx: float, dy: float, dz: float) -> None:
        rad = math.radians(self._yaw)
        self._x += dx * math.cos(rad) - dy * math.sin(rad)
        self._y += dx * math.sin(rad) + dy * math.cos(rad)
        self._z += dz
        self._z = max(0, self._z)
        self._drain_battery(0.5)
        time.sleep(0.1)  # brief delay for realism

    # --- Connection ---

    def connect(self) -> None:
        self._connected = True
        logger.info("[SIM] Drone conectado")

    def end(self) -> None:
        self._connected = False
        self.stream_on = False
        logger.info("[SIM] Drone desconectado")

    def reboot(self) -> None:
        self.__init__(self.host)
        logger.info("[SIM] Drone reiniciado")

    # --- Flight ---

    def takeoff(self) -> None:
        self.is_flying = True
        self._z = 80.0  # ~80cm hover height
        self._takeoff_time = time.time()
        self._record_position("takeoff")

    def land(self) -> None:
        self.is_flying = False
        self._z = 0.0
        self._takeoff_time = None
        self._record_position("land")

    def emergency(self) -> None:
        self.is_flying = False
        self._z = 0.0
        self._motors_on = False
        self._takeoff_time = None

    def move_forward(self, x: int) -> None:
        self._move_relative(x, 0, 0)
        self._record_position(f"forward {x}cm")

    def move_back(self, x: int) -> None:
        self._move_relative(-x, 0, 0)
        self._record_position(f"back {x}cm")

    def move_left(self, x: int) -> None:
        self._move_relative(0, -x, 0)
        self._record_position(f"left {x}cm")

    def move_right(self, x: int) -> None:
        self._move_relative(0, x, 0)
        self._record_position(f"right {x}cm")

    def move_up(self, x: int) -> None:
        self._move_relative(0, 0, x)
        self._record_position(f"up {x}cm")

    def move_down(self, x: int) -> None:
        self._move_relative(0, 0, -x)
        self._record_position(f"down {x}cm")

    def rotate_clockwise(self, degrees: int) -> None:
        self._yaw = (self._yaw + degrees) % 360
        self._drain_battery(0.2)
        self._record_position(f"CW {degrees}")

    def rotate_counter_clockwise(self, degrees: int) -> None:
        self._yaw = (self._yaw - degrees) % 360
        self._drain_battery(0.2)
        self._record_position(f"CCW {degrees}")

    def flip(self, direction: str) -> None:
        self._drain_battery(3.0)
        self._record_position(f"flip {direction}")

    def go_xyz_speed(self, x: int, y: int, z: int, speed: int) -> None:
        self._move_relative(x, y, z)
        self._record_position(f"go ({x},{y},{z}) @{speed}")

    def go_xyz_speed_mid(self, x, y, z, speed, mid) -> None:
        self._move_relative(x, y, z)
        self._record_position(f"go_mid ({x},{y},{z}) pad={mid}")

    def curve_xyz_speed(self, x1, y1, z1, x2, y2, z2, speed) -> None:
        self._move_relative(x1, y1, z1)
        self._move_relative(x2 - x1, y2 - y1, z2 - z1)
        self._record_position(f"curve -> ({x2},{y2},{z2})")

    def send_rc_control(self, lr, fb, ud, yaw) -> None:
        self._vx = fb
        self._vy = lr
        self._vz = ud
        self._x += fb * 0.05
        self._y += lr * 0.05
        self._z = max(0, self._z + ud * 0.05)
        self._yaw = (self._yaw + yaw * 0.1) % 360
        self._drain_battery(0.1)

    def set_speed(self, speed: int) -> None:
        self._speed = speed

    def turn_motor_on(self) -> None:
        self._motors_on = True

    def turn_motor_off(self) -> None:
        self._motors_on = False

    # --- Telemetry ---

    def get_current_state(self) -> dict:
        self._update_flight_time()
        return {
            "pitch": int(self._pitch + random.uniform(-1, 1)),
            "roll": int(self._roll + random.uniform(-1, 1)),
            "yaw": int(self._yaw),
            "vgx": self._vx,
            "vgy": self._vy,
            "vgz": self._vz,
            "templ": self._temp_low,
            "temph": self._temp_high,
            "tof": max(10, int(self._z * 1.1 + random.uniform(-2, 2))),
            "h": max(0, int(self._z)),
            "bat": int(self._battery),
            "baro": round(self._baro + self._z / 100, 2),
            "time": self._flight_time,
            "agx": round(random.uniform(-10, 10), 2),
            "agy": round(random.uniform(-10, 10), 2),
            "agz": round(-980 + random.uniform(-5, 5), 2),
        }

    def get_battery(self) -> int:
        return int(self._battery)

    def get_height(self) -> int:
        return max(0, int(self._z))

    def get_distance_tof(self) -> int:
        return max(10, int(self._z * 1.1))

    def get_barometer(self) -> float:
        return round((self._baro + self._z / 100) * 100, 2)

    def get_temperature(self) -> float:
        return (self._temp_low + self._temp_high) / 2.0

    def get_lowest_temperature(self) -> int:
        return self._temp_low

    def get_highest_temperature(self) -> int:
        return self._temp_high

    def get_flight_time(self) -> int:
        self._update_flight_time()
        return self._flight_time

    def get_pitch(self) -> int:
        return int(self._pitch)

    def get_roll(self) -> int:
        return int(self._roll)

    def get_yaw(self) -> int:
        return int(self._yaw)

    def get_speed_x(self) -> int:
        return self._vx

    def get_speed_y(self) -> int:
        return self._vy

    def get_speed_z(self) -> int:
        return self._vz

    def get_acceleration_x(self) -> float:
        return round(random.uniform(-10, 10), 2)

    def get_acceleration_y(self) -> float:
        return round(random.uniform(-10, 10), 2)

    def get_acceleration_z(self) -> float:
        return round(-980 + random.uniform(-5, 5), 2)

    # --- Queries ---

    def query_sdk_version(self) -> str:
        return "SIM-2.0"

    def query_serial_number(self) -> str:
        return "SIM-TELLO-001"

    def query_wifi_signal_noise_ratio(self) -> str:
        return str(random.randint(70, 99))

    # --- Camera ---

    def streamon(self) -> None:
        self.stream_on = True

    def streamoff(self) -> None:
        self.stream_on = False

    def get_udp_video_address(self) -> str:
        return "udp://@0.0.0.0:11111 [SIMULATED]"

    def get_frame_read(self, **kwargs):
        return _SimFrameRead()

    def set_video_resolution(self, res) -> None:
        self._resolution = res

    def set_video_fps(self, fps) -> None:
        self._fps = fps

    def set_video_bitrate(self, br) -> None:
        self._bitrate = br

    def set_video_direction(self, d) -> None:
        self._camera_dir = d

    # --- Mission Pads ---

    def enable_mission_pads(self) -> None:
        self._mission_pads_enabled = True

    def disable_mission_pads(self) -> None:
        self._mission_pads_enabled = False

    def set_mission_pad_detection_direction(self, d) -> None:
        pass

    def get_mission_pad_id(self) -> int:
        return random.choice([-1, 1, 2, 3])

    def get_mission_pad_distance_x(self) -> int:
        return random.randint(-50, 50)

    def get_mission_pad_distance_y(self) -> int:
        return random.randint(-50, 50)

    def get_mission_pad_distance_z(self) -> int:
        return random.randint(-120, -60)

    # --- WiFi ---

    def set_wifi_credentials(self, ssid, pwd) -> None:
        logger.info("[SIM] WiFi credentials set: %s", ssid)

    def connect_to_wifi(self, ssid, pwd) -> None:
        logger.info("[SIM] Connecting to WiFi: %s", ssid)

    # --- Sim-only helpers ---

    def get_position(self) -> Dict[str, float]:
        return {
            "x": round(self._x, 1),
            "y": round(self._y, 1),
            "z": round(self._z, 1),
            "yaw": round(self._yaw, 1),
        }

    def get_path(self) -> list:
        return list(self._path)


class _SimFrameRead:
    """Fake frame reader for simulation."""

    @property
    def frame(self):
        try:
            import numpy as np
            img = np.zeros((720, 960, 3), dtype=np.uint8)
            img[100:200, 100:300] = [0, 200, 0]
            return img
        except ImportError:
            return None


# ===========================================================================
# Drone instance management
# ===========================================================================

def _get_drone(host: str = "192.168.10.1"):
    global _drone
    with _drone_lock:
        if _drone is not None:
            return _drone
        if _simulation_mode:
            _drone = SimulatedTello(host=host)
            return _drone
        try:
            from djitellopy import Tello
        except ImportError:
            raise RuntimeError(
                "djitellopy no instalado. Ejecutar: pip install djitellopy\n"
                "O usar modo simulacion: drone({\"action\": \"simulate\", \"params\": {\"enabled\": true}})"
            )
        _drone = Tello(host=host)
        return _drone


def _safe_int(val, default=0, lo=None, hi=None) -> int:
    try:
        v = int(val)
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


# ---------------------------------------------------------------------------
# Simulation toggle
# ---------------------------------------------------------------------------

def drone_simulate(params: Dict[str, Any]) -> str:
    global _simulation_mode, _drone
    enabled = params.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ("true", "1", "yes", "si")

    with _drone_lock:
        if _drone is not None:
            try:
                if hasattr(_drone, 'end'):
                    _drone.end()
            except Exception:
                pass
            _drone = None

    _simulation_mode = bool(enabled)
    _log_action("simulate", f"{'ON' if _simulation_mode else 'OFF'}")

    if _simulation_mode:
        return (
            "MODO SIMULACION ACTIVADO\n"
            "El drone virtual responde a todos los comandos.\n"
            "Trackea posicion 3D, bateria, yaw, flight log.\n"
            "Usar 'sim_position' para ver posicion actual.\n"
            "Usar 'sim_path' para ver trayectoria completa."
        )
    return "MODO SIMULACION DESACTIVADO. Proxima conexion usara drone real."


def drone_sim_position(params: Dict[str, Any]) -> str:
    if not _simulation_mode:
        return "Solo disponible en modo simulacion"
    drone = _get_drone()
    if not isinstance(drone, SimulatedTello):
        return "Drone no es simulado"
    pos = drone.get_position()
    return (
        f"=== POSICION SIMULADA ===\n"
        f"X: {pos['x']}cm\n"
        f"Y: {pos['y']}cm\n"
        f"Z (altura): {pos['z']}cm\n"
        f"Yaw: {pos['yaw']} grados\n"
        f"Volando: {drone.is_flying}\n"
        f"Bateria: {drone.get_battery()}%"
    )


def drone_sim_path(params: Dict[str, Any]) -> str:
    if not _simulation_mode:
        return "Solo disponible en modo simulacion"
    drone = _get_drone()
    if not isinstance(drone, SimulatedTello):
        return "Drone no es simulado"
    path = drone.get_path()
    if not path:
        return "Sin movimientos registrados"
    lines = [f"=== TRAYECTORIA ({len(path)} puntos) ==="]
    for i, p in enumerate(path):
        lines.append(
            f"  {i+1}. {p['action']:20s} -> "
            f"({p['x']:>7.1f}, {p['y']:>7.1f}, {p['z']:>7.1f}) "
            f"yaw={p['yaw']:>6.1f} bat={p['battery']:.0f}%"
        )
    return "\n".join(lines)


def drone_sim_map(params: Dict[str, Any]) -> str:
    """Render ASCII top-down map of the drone path."""
    if not _simulation_mode:
        return "Solo disponible en modo simulacion"
    drone = _get_drone()
    if not isinstance(drone, SimulatedTello):
        return "Drone no es simulado"
    path = drone.get_path()
    if not path:
        return "Sin movimientos registrados"

    xs = [p["x"] for p in path]
    ys = [p["y"] for p in path]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    W, H = 60, 30
    range_x = max(max_x - min_x, 1)
    range_y = max(max_y - min_y, 1)

    grid = [["." for _ in range(W)] for _ in range(H)]

    for i, p in enumerate(path):
        col = int((p["x"] - min_x) / range_x * (W - 1))
        row = int((p["y"] - min_y) / range_y * (H - 1))
        row = H - 1 - row  # flip Y
        char = "S" if i == 0 else ("E" if i == len(path) - 1 else "*")
        grid[row][col] = char

    lines = [f"=== MAPA AEREO (S=inicio, E=fin, *=paso) ==="]
    for row in grid:
        lines.append("".join(row))
    lines.append(f"Rango X: {min_x:.0f} a {max_x:.0f}cm | Y: {min_y:.0f} a {max_y:.0f}cm")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def drone_connect(params: Dict[str, Any]) -> str:
    host = str(params.get("host", "192.168.10.1")).strip()
    drone = _get_drone(host)
    drone.connect()
    bat = drone.get_battery()
    _log_action("connect", f"host={host} battery={bat}%")
    mode = " [SIMULACION]" if _simulation_mode else ""
    return (
        f"Drone conectado a {host}{mode}\n"
        f"Bateria: {bat}%\n"
        f"SDK: {drone.query_sdk_version()}\n"
        f"SN: {drone.query_serial_number()}"
    )


def drone_disconnect(params: Dict[str, Any]) -> str:
    global _drone
    with _drone_lock:
        if _drone is None:
            return "No hay drone conectado"
        try:
            if _drone.is_flying:
                _drone.land()
                _log_action("auto_land", "landing before disconnect")
            _drone.end()
        except Exception as e:
            logger.warning("Error al desconectar drone: %s", e)
        _drone = None
    _log_action("disconnect")
    return "Drone desconectado"


# ---------------------------------------------------------------------------
# Flight control
# ---------------------------------------------------------------------------

def drone_takeoff(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    bat = drone.get_battery()
    if bat < 10:
        return f"Bateria muy baja ({bat}%). No se puede despegar"
    drone.takeoff()
    _log_action("takeoff", f"battery={bat}%")
    h = drone.get_height()
    return f"Drone despegado. Altura: {h}cm, Bateria: {bat}%"


def drone_land(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.land()
    _log_action("land")
    return "Drone aterrizando"


def drone_emergency(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.emergency()
    _log_action("EMERGENCY", "motors killed")
    return "EMERGENCIA: motores detenidos inmediatamente"


def drone_move(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    direction = str(params.get("direction", "")).strip().lower()
    distance = _safe_int(params.get("distance", 30), default=30, lo=20, hi=500)

    moves = {
        "forward": drone.move_forward,
        "adelante": drone.move_forward,
        "back": drone.move_back,
        "atras": drone.move_back,
        "left": drone.move_left,
        "izquierda": drone.move_left,
        "right": drone.move_right,
        "derecha": drone.move_right,
        "up": drone.move_up,
        "arriba": drone.move_up,
        "down": drone.move_down,
        "abajo": drone.move_down,
    }

    fn = moves.get(direction)
    if fn is None:
        return (
            f"Direccion no valida: '{direction}'. "
            f"Usar: forward/back/left/right/up/down "
            f"(o adelante/atras/izquierda/derecha/arriba/abajo)"
        )

    fn(distance)
    _log_action("move", f"{direction} {distance}cm")
    return f"Movido {direction} {distance}cm"


def drone_rotate(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    degrees = _safe_int(params.get("degrees", 90), default=90, lo=1, hi=360)
    direction = str(params.get("direction", "clockwise")).strip().lower()

    if direction in ("clockwise", "cw", "derecha", "horario"):
        drone.rotate_clockwise(degrees)
        _log_action("rotate", f"CW {degrees}")
        return f"Rotado {degrees} grados en sentido horario"
    elif direction in ("counter_clockwise", "ccw", "izquierda", "antihorario"):
        drone.rotate_counter_clockwise(degrees)
        _log_action("rotate", f"CCW {degrees}")
        return f"Rotado {degrees} grados en sentido antihorario"
    else:
        return "Direccion no valida. Usar: clockwise/counter_clockwise (o horario/antihorario)"


def drone_flip(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    bat = drone.get_battery()
    if bat < 50:
        return f"Bateria insuficiente para flip ({bat}%). Minimo 50%"

    direction = str(params.get("direction", "forward")).strip().lower()
    flip_map = {
        "forward": "f", "adelante": "f",
        "back": "b", "atras": "b",
        "left": "l", "izquierda": "l",
        "right": "r", "derecha": "r",
    }
    code = flip_map.get(direction)
    if code is None:
        return "Direccion de flip no valida. Usar: forward/back/left/right"

    drone.flip(code)
    _log_action("flip", direction)
    return f"Flip {direction} completado"


def drone_go_xyz(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    x = _safe_int(params.get("x", 0), lo=-500, hi=500)
    y = _safe_int(params.get("y", 0), lo=-500, hi=500)
    z = _safe_int(params.get("z", 0), lo=-500, hi=500)
    speed = _safe_int(params.get("speed", 50), lo=10, hi=100)

    drone.go_xyz_speed(x, y, z, speed)
    _log_action("go_xyz", f"x={x} y={y} z={z} speed={speed}")
    return f"Movido a ({x}, {y}, {z}) a {speed}cm/s"


def drone_curve(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    x1 = _safe_int(params.get("x1", 0), lo=-500, hi=500)
    y1 = _safe_int(params.get("y1", 0), lo=-500, hi=500)
    z1 = _safe_int(params.get("z1", 0), lo=-500, hi=500)
    x2 = _safe_int(params.get("x2", 0), lo=-500, hi=500)
    y2 = _safe_int(params.get("y2", 0), lo=-500, hi=500)
    z2 = _safe_int(params.get("z2", 0), lo=-500, hi=500)
    speed = _safe_int(params.get("speed", 30), lo=10, hi=60)

    drone.curve_xyz_speed(x1, y1, z1, x2, y2, z2, speed)
    _log_action("curve", f"({x1},{y1},{z1})->({x2},{y2},{z2}) speed={speed}")
    return f"Curva ejecutada de ({x1},{y1},{z1}) a ({x2},{y2},{z2}) a {speed}cm/s"


def drone_rc_control(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    lr = _safe_int(params.get("left_right", 0), lo=-100, hi=100)
    fb = _safe_int(params.get("forward_backward", 0), lo=-100, hi=100)
    ud = _safe_int(params.get("up_down", 0), lo=-100, hi=100)
    yaw = _safe_int(params.get("yaw", 0), lo=-100, hi=100)
    duration = _safe_int(params.get("duration_ms", 500), lo=100, hi=5000)

    drone.send_rc_control(lr, fb, ud, yaw)
    time.sleep(duration / 1000.0)
    drone.send_rc_control(0, 0, 0, 0)  # stop

    _log_action("rc_control", f"lr={lr} fb={fb} ud={ud} yaw={yaw} {duration}ms")
    return f"RC control: lr={lr} fb={fb} ud={ud} yaw={yaw} durante {duration}ms"


def drone_set_speed(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    speed = _safe_int(params.get("speed", 50), lo=10, hi=100)
    drone.set_speed(speed)
    _log_action("set_speed", str(speed))
    return f"Velocidad establecida: {speed}cm/s"


def drone_motor_on(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.turn_motor_on()
    _log_action("motor_on")
    return "Motores encendidos (sin despegar)"


def drone_motor_off(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.turn_motor_off()
    _log_action("motor_off")
    return "Motores apagados"


# ---------------------------------------------------------------------------
# Telemetry & Status
# ---------------------------------------------------------------------------

def drone_status(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    try:
        state = drone.get_current_state()
    except Exception:
        state = {}

    mode = " [SIMULACION]" if _simulation_mode else ""
    lines = [
        f"=== DRONE STATUS{mode} ===",
        f"Flying: {drone.is_flying}",
        f"Battery: {state.get('bat', '?')}%",
        f"Height: {state.get('h', '?')}cm",
        f"TOF distance: {state.get('tof', '?')}cm",
        f"Barometer: {state.get('baro', '?')}",
        f"Flight time: {state.get('time', '?')}s",
        f"Temperature: {state.get('templ', '?')}-{state.get('temph', '?')}C",
        f"Attitude: pitch={state.get('pitch', '?')} roll={state.get('roll', '?')} yaw={state.get('yaw', '?')}",
        f"Speed: vx={state.get('vgx', '?')} vy={state.get('vgy', '?')} vz={state.get('vgz', '?')}",
        f"Stream: {'ON' if drone.stream_on else 'OFF'}",
    ]

    if _simulation_mode and isinstance(drone, SimulatedTello):
        pos = drone.get_position()
        lines.append(f"Position: x={pos['x']}cm y={pos['y']}cm z={pos['z']}cm")

    _log_action("status")
    return "\n".join(lines)


def drone_battery(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    bat = drone.get_battery()
    _log_action("battery_check", f"{bat}%")
    return f"Bateria: {bat}%"


def drone_telemetry(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    data = {
        "mode": "simulation" if _simulation_mode else "real",
        "battery": drone.get_battery(),
        "height_cm": drone.get_height(),
        "tof_cm": drone.get_distance_tof(),
        "barometer_cm": drone.get_barometer(),
        "temperature_c": drone.get_temperature(),
        "flight_time_s": drone.get_flight_time(),
        "pitch": drone.get_pitch(),
        "roll": drone.get_roll(),
        "yaw": drone.get_yaw(),
        "speed_x": drone.get_speed_x(),
        "speed_y": drone.get_speed_y(),
        "speed_z": drone.get_speed_z(),
        "acceleration_x": drone.get_acceleration_x(),
        "acceleration_y": drone.get_acceleration_y(),
        "acceleration_z": drone.get_acceleration_z(),
        "is_flying": drone.is_flying,
        "stream_on": drone.stream_on,
    }
    if _simulation_mode and isinstance(drone, SimulatedTello):
        data["position"] = drone.get_position()
    try:
        data["wifi_snr"] = drone.query_wifi_signal_noise_ratio()
    except Exception:
        pass
    _log_action("telemetry")
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def drone_stream_on(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.streamon()
    _log_action("stream_on")
    return f"Video stream activado en {drone.get_udp_video_address()}"


def drone_stream_off(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    drone.streamoff()
    _log_action("stream_off")
    return "Video stream desactivado"


def drone_take_photo(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    output = str(params.get("output", "/tmp/drone_photo.jpg")).strip()

    if not drone.stream_on:
        drone.streamon()
        time.sleep(0.5 if _simulation_mode else 2)

    frame_read = drone.get_frame_read()
    time.sleep(0.1 if _simulation_mode else 0.5)
    frame = frame_read.frame

    if frame is None:
        if _simulation_mode:
            _log_action("photo", f"{output} [numpy no disponible]")
            return f"[SIM] Foto simulada (numpy no disponible). Path: {output}"
        return "No se pudo capturar frame del drone"

    try:
        import cv2
        cv2.imwrite(output, frame)
    except ImportError:
        if _simulation_mode:
            _log_action("photo", f"{output} [cv2 no disponible]")
            return f"[SIM] Foto simulada (cv2 no disponible). Path: {output}"
        return "cv2 (opencv-python) no instalado. Ejecutar: pip install opencv-python"

    _log_action("photo", output)
    return f"Foto guardada en {output} ({frame.shape[1]}x{frame.shape[0]})"


def drone_set_video(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    results = []

    resolution = str(params.get("resolution", "")).strip().lower()
    if resolution in ("720p", "high"):
        drone.set_video_resolution(drone.RESOLUTION_720P)
        results.append("Resolucion: 720p")
    elif resolution in ("480p", "low"):
        drone.set_video_resolution(drone.RESOLUTION_480P)
        results.append("Resolucion: 480p")

    fps = str(params.get("fps", "")).strip().lower()
    if fps in ("30", "high"):
        drone.set_video_fps(drone.FPS_30)
        results.append("FPS: 30")
    elif fps in ("15", "middle"):
        drone.set_video_fps(drone.FPS_15)
        results.append("FPS: 15")
    elif fps in ("5", "low"):
        drone.set_video_fps(drone.FPS_5)
        results.append("FPS: 5")

    bitrate = str(params.get("bitrate", "")).strip()
    bitrate_map = {"auto": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
    if bitrate in bitrate_map:
        drone.set_video_bitrate(bitrate_map[bitrate])
        results.append(f"Bitrate: {bitrate}Mbps")

    direction = str(params.get("direction", "")).strip().lower()
    if direction in ("forward", "front", "adelante"):
        drone.set_video_direction(drone.CAMERA_FORWARD)
        results.append("Camara: frontal")
    elif direction in ("down", "downward", "abajo"):
        drone.set_video_direction(drone.CAMERA_DOWNWARD)
        results.append("Camara: abajo")

    if not results:
        return "Especificar: resolution (720p/480p), fps (5/15/30), bitrate (auto/1-5), direction (forward/down)"

    _log_action("set_video", ", ".join(results))
    return "Video configurado: " + ", ".join(results)


# ---------------------------------------------------------------------------
# Advanced / Mission Pads (EDU only)
# ---------------------------------------------------------------------------

def drone_mission_pad(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    sub = str(params.get("sub_action", "")).strip().lower()

    if sub == "enable":
        drone.enable_mission_pads()
        direction = _safe_int(params.get("detection_direction", 2), lo=0, hi=2)
        drone.set_mission_pad_detection_direction(direction)
        _log_action("mission_pad", "enabled")
        return f"Mission pads habilitados (deteccion: {direction})"
    elif sub == "disable":
        drone.disable_mission_pads()
        _log_action("mission_pad", "disabled")
        return "Mission pads deshabilitados"
    elif sub == "status":
        mid = drone.get_mission_pad_id()
        if mid == -1:
            return "No se detecta mission pad"
        x = drone.get_mission_pad_distance_x()
        y = drone.get_mission_pad_distance_y()
        z = drone.get_mission_pad_distance_z()
        return f"Mission pad {mid} detectado. Distancia: x={x}cm y={y}cm z={z}cm"
    elif sub == "go_to":
        x = _safe_int(params.get("x", 0), lo=-500, hi=500)
        y = _safe_int(params.get("y", 0), lo=-500, hi=500)
        z = _safe_int(params.get("z", 0), lo=-500, hi=500)
        speed = _safe_int(params.get("speed", 50), lo=10, hi=100)
        mid = _safe_int(params.get("mid", 1), lo=1, hi=8)
        drone.go_xyz_speed_mid(x, y, z, speed, mid)
        return f"Movido a ({x},{y},{z}) relativo a mission pad {mid}"
    else:
        return "sub_action: enable, disable, status, go_to"


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def drone_wifi(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    sub = str(params.get("sub_action", "")).strip().lower()

    if sub == "set_credentials":
        ssid = str(params.get("ssid", "")).strip()
        password = str(params.get("password", "")).strip()
        if not ssid or not password:
            return "Requiere ssid y password"
        drone.set_wifi_credentials(ssid, password)
        _log_action("wifi", f"credentials set for {ssid}")
        return f"Credenciales WiFi AP configuradas: {ssid}. El drone se reiniciara"
    elif sub == "connect_to":
        ssid = str(params.get("ssid", "")).strip()
        password = str(params.get("password", "")).strip()
        if not ssid or not password:
            return "Requiere ssid y password"
        drone.connect_to_wifi(ssid, password)
        _log_action("wifi", f"connecting to {ssid}")
        return f"Drone conectandose a red WiFi: {ssid}. Cambiar IP de conexion"
    elif sub == "snr":
        snr = drone.query_wifi_signal_noise_ratio()
        return f"WiFi SNR: {snr}"
    else:
        return "sub_action: set_credentials, connect_to, snr"


# ---------------------------------------------------------------------------
# Flight log
# ---------------------------------------------------------------------------

def drone_flight_log(params: Dict[str, Any]) -> str:
    limit = _safe_int(params.get("limit", 20), lo=1, hi=200)
    if not _flight_log:
        return "No hay registros de vuelo"
    entries = _flight_log[-limit:]
    lines = ["=== FLIGHT LOG ==="]
    for e in entries:
        detail = f" - {e['detail']}" if e.get("detail") else ""
        lines.append(f"[{e['time']}] {e['action']}{detail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Patrol / Sequence
# ---------------------------------------------------------------------------

def drone_patrol(params: Dict[str, Any]) -> str:
    drone = _get_drone()
    pattern = str(params.get("pattern", "square")).strip().lower()
    size = _safe_int(params.get("size", 100), lo=20, hi=300)
    speed = _safe_int(params.get("speed", 30), lo=10, hi=100)

    bat = drone.get_battery()
    if bat < 20:
        return f"Bateria insuficiente para patrulla ({bat}%). Minimo 20%"

    _log_action("patrol_start", f"{pattern} size={size}")

    if pattern == "square":
        for direction_fn in [drone.move_forward, drone.move_right, drone.move_back, drone.move_left]:
            direction_fn(size)
            time.sleep(0.1 if _simulation_mode else 0.5)
        return f"Patrulla cuadrada completada ({size}cm x 4 lados)"

    elif pattern == "triangle":
        drone.move_forward(size)
        time.sleep(0.1 if _simulation_mode else 0.3)
        drone.rotate_clockwise(120)
        time.sleep(0.1 if _simulation_mode else 0.3)
        drone.move_forward(size)
        time.sleep(0.1 if _simulation_mode else 0.3)
        drone.rotate_clockwise(120)
        time.sleep(0.1 if _simulation_mode else 0.3)
        drone.move_forward(size)
        time.sleep(0.1 if _simulation_mode else 0.3)
        drone.rotate_clockwise(120)
        return f"Patrulla triangular completada ({size}cm x 3 lados)"

    elif pattern == "circle":
        steps = 12
        angle = 360 // steps
        seg = max(20, size // steps)
        for _ in range(steps):
            drone.move_forward(seg)
            drone.rotate_clockwise(angle)
            time.sleep(0.05 if _simulation_mode else 0.2)
        return f"Patrulla circular completada (radio ~{size}cm)"

    elif pattern == "zigzag":
        for i in range(4):
            drone.move_forward(size)
            time.sleep(0.1 if _simulation_mode else 0.3)
            if i % 2 == 0:
                drone.move_right(size // 2)
            else:
                drone.move_left(size // 2)
            time.sleep(0.1 if _simulation_mode else 0.3)
        return f"Patrulla zigzag completada (4 tramos de {size}cm)"

    elif pattern == "sweep":
        drone.move_forward(size)
        drone.rotate_clockwise(90)
        drone.move_forward(size // 3)
        drone.rotate_clockwise(90)
        drone.move_forward(size)
        drone.rotate_counter_clockwise(90)
        drone.move_forward(size // 3)
        drone.rotate_counter_clockwise(90)
        drone.move_forward(size)
        return f"Barrido completado ({size}cm x 2 pasadas)"

    else:
        return "Patrones: square, triangle, circle, zigzag, sweep"


def drone_reboot(params: Dict[str, Any]) -> str:
    global _drone
    drone = _get_drone()
    drone.reboot()
    with _drone_lock:
        _drone = None
    _log_action("reboot")
    return "Drone reiniciandose. Reconectar en ~10 segundos"


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def drone_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Unified drone control tool for DJI Tello.

    SIMULATION MODE: Use action 'simulate' with params {"enabled": true}
    to activate full drone simulation without hardware.

    Actions:
      Simulation:
        - simulate: Toggle simulation mode (params: enabled true/false)
        - sim_position: Show current simulated position
        - sim_path: Show full flight path with coordinates
        - sim_map: ASCII top-down map of flight path

      Connection:
        - connect: Connect to drone (params: host)
        - disconnect: Land and disconnect
        - reboot: Reboot drone

      Flight:
        - takeoff: Take off
        - land: Land
        - emergency: Emergency motor stop
        - move: Move in direction (params: direction, distance)
        - rotate: Rotate (params: direction, degrees)
        - flip: Do a flip (params: direction) [needs >50% battery]
        - go_xyz: Move to XYZ relative position (params: x, y, z, speed)
        - curve: Fly a curve (params: x1,y1,z1, x2,y2,z2, speed)
        - rc_control: Real-time RC control (params: left_right, forward_backward, up_down, yaw, duration_ms)
        - set_speed: Set movement speed (params: speed 10-100)
        - motor_on/motor_off: Toggle motors without flight

      Patterns:
        - patrol: Execute flight pattern (params: pattern [square/triangle/circle/zigzag/sweep], size, speed)

      Camera:
        - stream_on/stream_off: Toggle video stream
        - take_photo: Capture photo (params: output)
        - set_video: Configure video (params: resolution, fps, bitrate, direction)

      Telemetry:
        - status: Full drone status
        - battery: Battery level
        - telemetry: Complete sensor data as JSON

      Advanced:
        - mission_pad: Mission pad ops (params: sub_action [enable/disable/status/go_to])
        - wifi: WiFi config (params: sub_action [set_credentials/connect_to/snr])
        - flight_log: View flight log (params: limit)
    """
    params = params or {}
    action = (action or "").strip().lower()

    handlers = {
        # Simulation
        "simulate": drone_simulate,
        "simular": drone_simulate,
        "sim_position": drone_sim_position,
        "sim_path": drone_sim_path,
        "sim_map": drone_sim_map,
        # Connection
        "connect": drone_connect,
        "disconnect": drone_disconnect,
        "reboot": drone_reboot,
        # Flight
        "takeoff": drone_takeoff,
        "despegar": drone_takeoff,
        "land": drone_land,
        "aterrizar": drone_land,
        "emergency": drone_emergency,
        "emergencia": drone_emergency,
        "move": drone_move,
        "mover": drone_move,
        "rotate": drone_rotate,
        "rotar": drone_rotate,
        "flip": drone_flip,
        "go_xyz": drone_go_xyz,
        "curve": drone_curve,
        "rc_control": drone_rc_control,
        "set_speed": drone_set_speed,
        "motor_on": drone_motor_on,
        "motor_off": drone_motor_off,
        # Patterns
        "patrol": drone_patrol,
        "patrullar": drone_patrol,
        # Camera
        "stream_on": drone_stream_on,
        "stream_off": drone_stream_off,
        "take_photo": drone_take_photo,
        "foto": drone_take_photo,
        "set_video": drone_set_video,
        # Telemetry
        "status": drone_status,
        "estado": drone_status,
        "battery": drone_battery,
        "bateria": drone_battery,
        "telemetry": drone_telemetry,
        "telemetria": drone_telemetry,
        # Advanced
        "mission_pad": drone_mission_pad,
        "wifi": drone_wifi,
        "flight_log": drone_flight_log,
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
        _log_action("error", f"{action}: {exc}")
        error_msg = str(exc)
        if "Tello" in error_msg or "timed out" in error_msg.lower():
            return f"Error de comunicacion con drone: {error_msg}"
        return f"Error ejecutando {action}: {error_msg}"
