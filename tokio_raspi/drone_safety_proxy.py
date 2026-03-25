"""
TokioAI Drone Safety Proxy — Secure drone control layer.

ALL drone commands pass through this proxy. It enforces:
- Geofencing (max distance, max height, boundary box)
- Command authentication (only Tailscale/localhost)
- Rate limiting (prevent command flooding)
- Kill switch (immediate emergency stop)
- Auto-land on timeout, low battery, or geofence breach
- Command whitelist (block dangerous commands in demo mode)
- Full audit logging

Architecture:
    TokioAI (GCP) --[Tailscale encrypted]--> Raspi Safety Proxy --> Tello drone

    Nobody at the conference can reach the drone directly.
    The proxy is the ONLY way to send commands.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("drone.safety")


# ---------------------------------------------------------------------------
# Direct UDP Tello driver (replaces djitellopy for reliability)
# ---------------------------------------------------------------------------

class TelloUDP:
    """Minimal Tello driver using raw UDP. More reliable than djitellopy on Pi 5."""

    def __init__(self, host: str = "192.168.10.1", port: int = 8889):
        self.host = host
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("", port))
        self._sock.settimeout(7)
        self._lock = threading.Lock()
        self.is_flying = False
        # Continuous RC sender thread — Tello needs RC every 50ms
        self._rc_lock = threading.Lock()
        self._rc_values = [0, 0, 0, 0]  # lr, fb, ud, yaw
        self._rc_running = False
        self._rc_thread: Optional[threading.Thread] = None

    def _cmd(self, command: str, timeout: float = 7) -> str:
        with self._lock:
            self._sock.settimeout(timeout)
            # Flush any stale data in the socket
            self._sock.setblocking(False)
            try:
                while True:
                    self._sock.recvfrom(1024)
            except (BlockingIOError, socket.error):
                pass
            self._sock.setblocking(True)
            self._sock.settimeout(timeout)
            self._sock.sendto(command.encode(), (self.host, self.port))
            try:
                data, _ = self._sock.recvfrom(1024)
                return data.decode().strip()
            except socket.timeout:
                return "TIMEOUT"

    def connect(self):
        r = self._cmd("command")
        if r == "TIMEOUT":
            raise ConnectionError("Tello not responding")
        return r

    def takeoff(self):
        self.is_flying = True
        return self._cmd("takeoff", timeout=15)

    def land(self):
        self.is_flying = False
        return self._cmd("land", timeout=15)

    def emergency(self):
        self.is_flying = False
        return self._cmd("emergency")

    def move_forward(self, d): return self._cmd(f"forward {d}", 10)
    def move_back(self, d): return self._cmd(f"back {d}", 10)
    def move_left(self, d): return self._cmd(f"left {d}", 10)
    def move_right(self, d): return self._cmd(f"right {d}", 10)
    def move_up(self, d): return self._cmd(f"up {d}", 10)
    def move_down(self, d): return self._cmd(f"down {d}", 10)
    def rotate_clockwise(self, d): return self._cmd(f"cw {d}", 10)
    def rotate_counter_clockwise(self, d): return self._cmd(f"ccw {d}", 10)

    def get_battery(self) -> int:
        r = self._cmd("battery?")
        try: return int(r)
        except: return -1

    def get_height(self) -> int:
        r = self._cmd("height?")
        try: return int(r.replace("dm", "").strip()) * 10
        except: return 0

    def get_temperature(self) -> str: return self._cmd("temp?")
    def get_flight_time(self) -> str: return self._cmd("time?")
    def get_speed_x(self) -> str: return self._cmd("speed?")
    def get_speed_y(self) -> str: return "0"
    def get_speed_z(self) -> str: return "0"
    def get_acceleration_x(self) -> str: return self._cmd("acceleration?")
    def get_acceleration_y(self) -> str: return "0"
    def get_acceleration_z(self) -> str: return "0"
    def get_barometer(self) -> float:
        r = self._cmd("baro?")
        try: return float(r)
        except: return 0.0
    def get_distance_tof(self) -> str: return self._cmd("tof?")
    def get_yaw(self) -> str: return "0"
    def get_pitch(self) -> str: return "0"
    def get_roll(self) -> str: return "0"

    def streamon(self): return self._cmd("streamon")
    def streamoff(self): return self._cmd("streamoff")

    def rc(self, lr: int, fb: int, ud: int, yaw: int):
        """Set RC values — sent continuously by background thread at 20Hz."""
        lr = max(-100, min(100, lr))
        fb = max(-100, min(100, fb))
        ud = max(-100, min(100, ud))
        yaw = max(-100, min(100, yaw))
        with self._rc_lock:
            self._rc_values = [lr, fb, ud, yaw]
        # Start RC thread if not running
        if not self._rc_running:
            self.start_rc_loop()

    def start_rc_loop(self):
        """Start continuous RC sender (20Hz). Call after takeoff."""
        if self._rc_running:
            return
        self._rc_running = True
        self._rc_thread = threading.Thread(target=self._rc_loop, daemon=True)
        self._rc_thread.start()
        logger.info("RC sender thread started (20Hz)")

    def stop_rc_loop(self):
        """Stop continuous RC sender. Call on land."""
        self._rc_running = False
        with self._rc_lock:
            self._rc_values = [0, 0, 0, 0]
        if self._rc_thread:
            self._rc_thread.join(timeout=1)
        logger.info("RC sender thread stopped")

    def _rc_loop(self):
        """Background: send current RC values to Tello every 50ms."""
        while self._rc_running:
            with self._rc_lock:
                lr, fb, ud, yaw = self._rc_values
            cmd = f"rc {lr} {fb} {ud} {yaw}"
            try:
                self._sock.sendto(cmd.encode(), (self.host, self.port))
            except Exception as e:
                logger.error(f"RC send error: {e}")
            time.sleep(0.05)  # 50ms = 20Hz

    def end(self):
        try: self._sock.close()
        except: pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class SafetyLevel(Enum):
    DEMO = "demo"        # Most restrictive — for live demos
    NORMAL = "normal"    # Standard safety limits
    EXPERT = "expert"    # Relaxed limits, still has geofence


@dataclass
class GeofenceConfig:
    """Geofence boundary configuration."""
    max_height_cm: int = 200        # 2 meters max height
    max_distance_cm: int = 500      # 5 meters max distance from home
    max_speed_cms: int = 60         # 60 cm/s max speed
    min_battery_pct: int = 20       # Auto-land below 20%
    command_timeout_s: float = 30   # Auto-land if no command for 30s
    watchdog_interval_s: float = 2  # Check safety every 2s

    # Boundary box (relative to takeoff point, in cm)
    # None = use circular geofence only
    boundary_min_x: int = -500
    boundary_max_x: int = 500
    boundary_min_y: int = -500
    boundary_max_y: int = 500


SAFETY_PRESETS = {
    SafetyLevel.DEMO: GeofenceConfig(
        max_height_cm=150,      # 1.5m — indoor safe
        max_distance_cm=200,    # 2m radius — super controlado
        max_speed_cms=30,       # slow and safe
        min_battery_pct=25,
        command_timeout_s=20,
        boundary_min_x=-100, boundary_max_x=100,   # 2m x 2m box
        boundary_min_y=-100, boundary_max_y=100,
    ),
    SafetyLevel.NORMAL: GeofenceConfig(
        max_height_cm=300,
        max_distance_cm=1000,
        max_speed_cms=80,
        min_battery_pct=20,
        command_timeout_s=30,
    ),
    SafetyLevel.EXPERT: GeofenceConfig(
        max_height_cm=500,
        max_distance_cm=2000,
        max_speed_cms=100,
        min_battery_pct=15,
        command_timeout_s=60,
    ),
}


# ---------------------------------------------------------------------------
# Authorized IPs — only these can send commands
# ---------------------------------------------------------------------------

AUTHORIZED_IPS = {
    "127.0.0.1",            # localhost
    "::1",                  # localhost ipv6
    "100.100.80.12",        # this Raspi (Tailscale)
    "100.125.151.118",      # GCP TokioAI (Tailscale)
    "100.79.121.13",        # Dev machine (Tailscale)
    "100.64.237.35",        # Subnet router (Tailscale)
    "192.168.8.161",        # Raspi LAN
    "192.168.8.235",        # Dev machine LAN
}
# Additional IPs from env
_extra = os.getenv("DRONE_AUTHORIZED_IPS", "")
AUTHORIZED_IPS |= {ip.strip() for ip in _extra.split(",") if ip.strip()}


# ---------------------------------------------------------------------------
# Command whitelist per safety level
# ---------------------------------------------------------------------------

DEMO_ALLOWED_COMMANDS = {
    "connect", "disconnect", "takeoff", "land", "emergency",
    "move", "rotate", "status", "battery", "telemetry",
    "stream_on", "stream_off", "take_photo",
    "patrol",  # patrol with geofence is safe
    "rc",      # RC control for visual servoing (clamped values)
}

DEMO_BLOCKED_COMMANDS = {
    "flip",         # risky indoors
    "go_xyz",       # raw coordinate control
    "curve",        # complex trajectory
    "motor_on",     # raw motor control
    "motor_off",
    "reboot",       # could lose control
    "wifi",         # network reconfiguration
    "mission_pad",
    "set_video",
}

# Max move distances per command (cm)
DEMO_MAX_MOVE = 100    # 1 meter max per move command
NORMAL_MAX_MOVE = 200
EXPERT_MAX_MOVE = 500


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Prevent command flooding."""

    def __init__(self, max_commands: int = 10, window_s: float = 5.0):
        self.max_commands = max_commands
        self.window_s = window_s
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def check(self) -> bool:
        """Returns True if command is allowed, False if rate limited."""
        now = time.monotonic()
        with self._lock:
            self._timestamps = [t for t in self._timestamps if now - t < self.window_s]
            if len(self._timestamps) >= self.max_commands:
                return False
            self._timestamps.append(now)
            return True

    @property
    def current_rate(self) -> float:
        now = time.monotonic()
        recent = [t for t in self._timestamps if now - t < self.window_s]
        return len(recent) / self.window_s if self.window_s > 0 else 0


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    timestamp: str
    source_ip: str
    command: str
    params: dict
    result: str
    blocked: bool
    reason: str = ""


class AuditLog:
    """Security audit log for all drone commands."""

    def __init__(self, max_entries: int = 500):
        self._entries: list[AuditEntry] = []
        self._max = max_entries
        self._lock = threading.Lock()

    def log(self, source_ip: str, command: str, params: dict,
            result: str, blocked: bool, reason: str = ""):
        entry = AuditEntry(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            source_ip=source_ip,
            command=command,
            params=params,
            result=result,
            blocked=blocked,
            reason=reason,
        )
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max:
                self._entries.pop(0)

        level = logging.WARNING if blocked else logging.INFO
        logger.log(level,
                   f"{'BLOCKED' if blocked else 'OK'} "
                   f"[{source_ip}] {command} {params} "
                   f"{'— ' + reason if reason else ''}")

    def get_recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return [
                {
                    "time": e.timestamp,
                    "ip": e.source_ip,
                    "cmd": e.command,
                    "blocked": e.blocked,
                    "reason": e.reason,
                }
                for e in self._entries[-n:]
            ]

    def blocked_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entries if e.blocked)

    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Safety Proxy
# ---------------------------------------------------------------------------

class DroneSafetyProxy:
    """
    Security proxy for all drone commands.

    Every command goes through validate() before reaching the drone.
    The watchdog thread monitors battery, position, and connectivity.
    """

    def __init__(self, safety_level: SafetyLevel = SafetyLevel.DEMO):
        self.safety_level = safety_level
        self.geofence = SAFETY_PRESETS[safety_level]
        self.rate_limiter = RateLimiter(max_commands=10, window_s=5.0)
        self.audit = AuditLog()

        # State
        self._kill_switch = False
        self._armed = False  # True after takeoff
        self._home_position = {"x": 0, "y": 0, "z": 0}
        self._current_position = {"x": 0, "y": 0, "z": 0}
        self._last_command_time = time.monotonic()
        self._connected = False

        # Watchdog thread
        self._watchdog_running = False
        self._watchdog_thread: Optional[threading.Thread] = None

        # Drone reference (set externally)
        self._drone = None

        # Visual tracker reference (set by API)
        self._tracker = None

        logger.info(f"Safety Proxy initialized: level={safety_level.value}, "
                    f"geofence={self.geofence.max_distance_cm}cm, "
                    f"max_height={self.geofence.max_height_cm}cm")

    def set_drone(self, drone):
        """Set the drone instance (real or simulated)."""
        self._drone = drone

    def activate_kill_switch(self, source_ip: str = "system") -> str:
        """EMERGENCY — immediately stop all motors."""
        self._kill_switch = True
        self.audit.log(source_ip, "KILL_SWITCH", {},
                       "ACTIVATED", blocked=False)
        logger.critical("KILL SWITCH ACTIVATED")

        if self._drone:
            try:
                self._drone.stop_rc_loop()
            except Exception:
                pass
            if self._armed:
                try:
                    self._drone.emergency()
                except Exception as e:
                    logger.error(f"Emergency command failed: {e}")

        self._armed = False
        return "KILL SWITCH ACTIVATED — all motors stopped"

    def reset_kill_switch(self, source_ip: str = "system") -> str:
        """Reset kill switch after emergency."""
        self._kill_switch = False
        self.audit.log(source_ip, "KILL_SWITCH_RESET", {},
                       "RESET", blocked=False)
        logger.warning("Kill switch reset")
        return "Kill switch reset — drone can fly again"

    # -------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------

    def validate_auth(self, source_ip: str) -> tuple[bool, str]:
        """Check if source IP is authorized."""
        if source_ip in AUTHORIZED_IPS:
            return True, ""
        return False, f"Unauthorized IP: {source_ip}"

    def validate_command(self, command: str, params: dict,
                         source_ip: str = "127.0.0.1") -> tuple[bool, str]:
        """
        Validate a command before execution.

        Returns (allowed: bool, reason: str)
        """
        # Kill switch overrides everything
        if self._kill_switch:
            if command not in ("status", "battery", "telemetry"):
                return False, "Kill switch active — only status commands allowed"

        # IP authorization
        authorized, reason = self.validate_auth(source_ip)
        if not authorized:
            return False, reason

        # Rate limiting (skip for RC, battery, telemetry — they're high-frequency by design)
        if command not in ("rc", "battery", "telemetry", "status"):
            if not self.rate_limiter.check():
                return False, "Rate limited — too many commands"

        # Demo mode command whitelist
        if self.safety_level == SafetyLevel.DEMO:
            if command in DEMO_BLOCKED_COMMANDS:
                return False, f"Command '{command}' blocked in demo mode"
            if command not in DEMO_ALLOWED_COMMANDS and command not in ("status", "battery", "telemetry", "flight_log"):
                return False, f"Unknown command '{command}' — not in whitelist"

        # Geofence checks for movement commands
        if command == "move":
            return self._validate_move(params)
        elif command == "takeoff":
            return self._validate_takeoff()
        elif command == "go_xyz":
            return self._validate_go_xyz(params)
        elif command == "set_speed":
            speed = params.get("speed", 50)
            if speed > self.geofence.max_speed_cms:
                return False, f"Speed {speed} exceeds limit {self.geofence.max_speed_cms}"

        return True, ""

    def _validate_takeoff(self) -> tuple[bool, str]:
        """Validate takeoff conditions."""
        if self._armed:
            return False, "Already in flight"
        if self._drone:
            try:
                battery = self._drone.get_battery()
                # -1 means query failed — don't block takeoff for unknown battery
                if battery >= 0 and battery < self.geofence.min_battery_pct:
                    return False, f"Battery too low: {battery}% (min: {self.geofence.min_battery_pct}%)"
            except Exception:
                pass
        return True, ""

    def _validate_move(self, params: dict) -> tuple[bool, str]:
        """Validate a move command against geofence."""
        distance = params.get("distance", 50)
        direction = params.get("direction", "forward")

        # Check max move distance
        max_move = {
            SafetyLevel.DEMO: DEMO_MAX_MOVE,
            SafetyLevel.NORMAL: NORMAL_MAX_MOVE,
            SafetyLevel.EXPERT: EXPERT_MAX_MOVE,
        }.get(self.safety_level, DEMO_MAX_MOVE)

        if distance > max_move:
            return False, f"Move {distance}cm exceeds max {max_move}cm"

        # Check height limits
        if direction == "up":
            new_height = self._current_position.get("z", 0) + distance
            if new_height > self.geofence.max_height_cm:
                return False, f"Would exceed max height: {new_height}cm > {self.geofence.max_height_cm}cm"

        # Estimate new position and check geofence
        return self._check_geofence_after_move(direction, distance)

    def _validate_go_xyz(self, params: dict) -> tuple[bool, str]:
        """Validate go_xyz against geofence."""
        x = abs(params.get("x", 0))
        y = abs(params.get("y", 0))
        z = params.get("z", 0)

        # Check height
        new_z = self._current_position.get("z", 0) + z
        if new_z > self.geofence.max_height_cm:
            return False, f"Would exceed max height: {new_z}cm"

        # Check distance from home
        new_x = self._current_position.get("x", 0) + params.get("x", 0)
        new_y = self._current_position.get("y", 0) + params.get("y", 0)
        dist = (new_x ** 2 + new_y ** 2) ** 0.5
        if dist > self.geofence.max_distance_cm:
            return False, f"Would exceed max distance: {dist:.0f}cm > {self.geofence.max_distance_cm}cm"

        return True, ""

    def _check_geofence_after_move(self, direction: str, distance: int) -> tuple[bool, str]:
        """Check if a move would violate geofence."""
        x = self._current_position.get("x", 0)
        y = self._current_position.get("y", 0)

        if direction == "forward":
            y += distance
        elif direction == "back":
            y -= distance
        elif direction == "left":
            x -= distance
        elif direction == "right":
            x += distance

        # Circular geofence
        dist = (x ** 2 + y ** 2) ** 0.5
        if dist > self.geofence.max_distance_cm:
            return False, f"GEOFENCE: move would put drone {dist:.0f}cm from home (max: {self.geofence.max_distance_cm}cm)"

        # Boundary box
        cfg = self.geofence
        if x < cfg.boundary_min_x or x > cfg.boundary_max_x:
            return False, f"GEOFENCE: X={x}cm out of bounds [{cfg.boundary_min_x}, {cfg.boundary_max_x}]"
        if y < cfg.boundary_min_y or y > cfg.boundary_max_y:
            return False, f"GEOFENCE: Y={y}cm out of bounds [{cfg.boundary_min_y}, {cfg.boundary_max_y}]"

        return True, ""

    # -------------------------------------------------------------------
    # Command execution (through proxy)
    # -------------------------------------------------------------------

    def execute(self, command: str, params: dict,
                source_ip: str = "127.0.0.1") -> str:
        """
        Execute a drone command through the safety proxy.

        All commands MUST go through this method.
        """
        self._last_command_time = time.monotonic()

        # Validate
        allowed, reason = self.validate_command(command, params, source_ip)

        if not allowed:
            self.audit.log(source_ip, command, params,
                           f"BLOCKED: {reason}", blocked=True, reason=reason)
            return f"BLOCKED: {reason}"

        # Execute and log
        try:
            result = self._execute_command(command, params)
            self.audit.log(source_ip, command, params,
                           result, blocked=False)
            return result
        except Exception as e:
            error = f"ERROR: {e}"
            self.audit.log(source_ip, command, params,
                           error, blocked=False, reason=str(e))
            return error

    def _execute_command(self, command: str, params: dict) -> str:
        """Execute a validated command on the drone via djitellopy."""
        if command == "takeoff":
            self._armed = True
            self._home_position = {"x": 0, "y": 0, "z": 0}
            self._current_position = {"x": 0, "y": 0, "z": 50}
            if self._drone:
                self._drone.takeoff()
                self._drone.start_rc_loop()  # Start continuous RC sender
            self._start_watchdog()
            if self._tracker:
                self._tracker.activate()
            return "Takeoff successful"

        elif command == "land":
            if self._drone:
                self._drone.stop_rc_loop()  # Stop RC sender before landing
                self._drone.land()
            self._armed = False
            self._current_position["z"] = 0
            self._stop_watchdog()
            if self._tracker:
                self._tracker.deactivate()
            return "Landing successful"

        elif command == "emergency":
            return self.activate_kill_switch("command")

        elif command == "connect":
            return self._do_connect(params)

        elif command == "disconnect":
            if self._drone:
                self._drone.end()
                self._drone = None
            self._connected = False
            return "Disconnected from Tello"

        elif command == "move":
            direction = params.get("direction", "forward")
            distance = params.get("distance", 50)
            self._update_position(direction, distance)
            if self._drone:
                move_map = {
                    "forward": self._drone.move_forward,
                    "back": self._drone.move_back,
                    "left": self._drone.move_left,
                    "right": self._drone.move_right,
                    "up": self._drone.move_up,
                    "down": self._drone.move_down,
                }
                fn = move_map.get(direction)
                if fn:
                    fn(distance)
            return f"Moved {direction} {distance}cm"

        elif command == "rotate":
            direction = params.get("direction", "clockwise")
            degrees = params.get("degrees", 90)
            if self._drone:
                if direction == "clockwise":
                    self._drone.rotate_clockwise(degrees)
                else:
                    self._drone.rotate_counter_clockwise(degrees)
            return f"Rotated {direction} {degrees} degrees"

        elif command == "status":
            return self._get_drone_status()

        elif command == "battery":
            if self._drone:
                return f"Battery: {self._drone.get_battery()}%"
            return "Not connected"

        elif command == "telemetry":
            return self._get_telemetry()

        elif command == "stream_on":
            if self._drone:
                self._drone.streamon()
            return "Video stream started"

        elif command == "stream_off":
            if self._drone:
                self._drone.streamoff()
            return "Video stream stopped"

        elif command == "take_photo":
            return self._take_photo(params)

        elif command == "patrol":
            return self._patrol(params)

        elif command == "flight_log":
            return str(self.audit.get_recent(20))

        elif command == "rc":
            lr = int(params.get("lr", 0))
            fb = int(params.get("fb", 0))
            ud = int(params.get("ud", 0))
            yaw = int(params.get("yaw", 0))
            if self._drone:
                self._drone.rc(lr, fb, ud, yaw)
            return f"RC: lr={lr} fb={fb} ud={ud} yaw={yaw}"

        return f"Unknown command: {command}"

    def _do_connect(self, params: dict) -> str:
        """Connect to the Tello drone."""
        host = params.get("host", "192.168.10.1")
        sim = params.get("simulate", False)
        try:
            if sim:
                logger.info("Using SIMULATION mode")
                self._drone = None
                self._connected = True
                return "Connected (SIMULATION mode)"
            # Close existing drone socket before creating new one
            if self._drone:
                try:
                    self._drone._sock.close()
                except Exception:
                    pass
                self._drone = None
            tello = TelloUDP(host=host)
            tello.connect()
            self._drone = tello
            self._connected = True
            battery = tello.get_battery()
            return f"Connected to Tello at {host} — Battery: {battery}%"
        except Exception as e:
            return f"Connection failed: {e}"

    def _get_drone_status(self) -> str:
        """Get drone status string."""
        if not self._drone:
            return "Drone not connected"
        try:
            lines = [
                f"Battery: {self._drone.get_battery()}%",
                f"Height: {self._drone.get_height()}cm",
                f"Temperature: {self._drone.get_temperature()}C",
                f"Flight time: {self._drone.get_flight_time()}s",
                f"Barometer: {self._drone.get_barometer():.1f}cm",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Status error: {e}"

    def _get_telemetry(self) -> str:
        """Get detailed telemetry."""
        if not self._drone:
            return "Drone not connected"
        try:
            lines = [
                f"Battery: {self._drone.get_battery()}%",
                f"Height: {self._drone.get_height()}cm",
                f"Speed X: {self._drone.get_speed_x()} cm/s",
                f"Speed Y: {self._drone.get_speed_y()} cm/s",
                f"Speed Z: {self._drone.get_speed_z()} cm/s",
                f"Accel X: {self._drone.get_acceleration_x()} cm/s2",
                f"Accel Y: {self._drone.get_acceleration_y()} cm/s2",
                f"Accel Z: {self._drone.get_acceleration_z()} cm/s2",
                f"Barometer: {self._drone.get_barometer():.1f}cm",
                f"TOF: {self._drone.get_distance_tof()}cm",
                f"Temperature: {self._drone.get_temperature()}C",
                f"Flight time: {self._drone.get_flight_time()}s",
                f"Yaw: {self._drone.get_yaw()}",
                f"Pitch: {self._drone.get_pitch()}",
                f"Roll: {self._drone.get_roll()}",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Telemetry error: {e}"

    def _take_photo(self, params: dict) -> str:
        """Take a photo from the Tello camera via UDP video stream."""
        if not self._drone:
            return "Drone not connected"
        try:
            import cv2
            # Start video stream
            self._drone.streamon()
            time.sleep(1)

            # Tello streams H264 to UDP 11111
            cap = cv2.VideoCapture("udp://@0.0.0.0:11111", cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

            frame = None
            # Read a few frames to get a stable image
            for _ in range(15):
                ret, f = cap.read()
                if ret:
                    frame = f
            cap.release()
            self._drone.streamoff()

            if frame is None:
                return "ERROR: Could not capture frame from video stream"

            output = params.get("output", f"/tmp/drone_photo_{int(time.time())}.jpg")
            cv2.imwrite(output, frame)
            return f"PHOTO_SAVED:{output}"
        except Exception as e:
            try:
                self._drone.streamoff()
            except Exception:
                pass
            return f"Photo error: {e}"

    def _patrol(self, params: dict) -> str:
        """Execute a patrol pattern within geofence, optionally for a duration."""
        if not self._drone or not self._armed:
            return "Drone must be connected and in flight"
        pattern = params.get("pattern", "square")
        size = min(params.get("size", 100), self.geofence.max_distance_cm // 2)
        duration_s = params.get("duration", 0)  # 0 = single loop
        repeats = params.get("repeats", 0)  # 0 = use duration or single

        if pattern == "square":
            moves = [("forward", size), ("right", size), ("back", size), ("left", size)]
        elif pattern == "triangle":
            moves = [("forward", size), ("right", size), ("back", size)]
        elif pattern == "line":
            moves = [("forward", size), ("back", size)]
        else:
            return f"Unknown pattern: {pattern}"

        # Cap duration at 10 minutes for safety
        if duration_s > 600:
            duration_s = 600

        start = time.monotonic()
        loops_done = 0
        max_loops = repeats if repeats > 0 else 999

        while True:
            # Check time limit
            if duration_s > 0 and (time.monotonic() - start) >= duration_s:
                break
            # Check repeat limit
            if repeats > 0 and loops_done >= max_loops:
                break
            # Single loop mode (no duration, no repeats)
            if duration_s == 0 and repeats == 0 and loops_done >= 1:
                break

            # Check battery before each loop
            if self._drone:
                try:
                    bat = self._drone.get_battery()
                    if bat < self.geofence.min_battery_pct:
                        logger.warning(f"Patrol stopped: battery {bat}%")
                        self._drone.land()
                        self._armed = False
                        return f"Patrol stopped after {loops_done} loops — battery low ({bat}%)"
                except Exception:
                    pass

            # Check kill switch
            if self._kill_switch:
                return f"Patrol stopped after {loops_done} loops — kill switch activated"

            # Execute one loop
            for direction, distance in moves:
                # Recheck time mid-loop
                if duration_s > 0 and (time.monotonic() - start) >= duration_s:
                    break
                if self._kill_switch:
                    break

                allowed, reason = self._check_geofence_after_move(direction, distance)
                if not allowed:
                    return f"Patrol aborted at loop {loops_done + 1}: {reason}"
                if self._drone:
                    move_map = {
                        "forward": self._drone.move_forward,
                        "back": self._drone.move_back,
                        "left": self._drone.move_left,
                        "right": self._drone.move_right,
                    }
                    fn = move_map.get(direction)
                    if fn:
                        fn(distance)
                self._update_position(direction, distance)

            loops_done += 1

        elapsed = int(time.monotonic() - start)
        return f"Patrol '{pattern}' ({size}cm) completed — {loops_done} loops in {elapsed}s"

    def _update_position(self, direction: str, distance: int):
        """Track estimated position after movement."""
        if direction == "forward":
            self._current_position["y"] += distance
        elif direction == "back":
            self._current_position["y"] -= distance
        elif direction == "left":
            self._current_position["x"] -= distance
        elif direction == "right":
            self._current_position["x"] += distance
        elif direction == "up":
            self._current_position["z"] += distance
        elif direction == "down":
            self._current_position["z"] = max(0, self._current_position["z"] - distance)

    # -------------------------------------------------------------------
    # Watchdog — autonomous safety monitor
    # -------------------------------------------------------------------

    def _start_watchdog(self):
        """Start the safety watchdog thread."""
        if self._watchdog_running:
            return
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True
        )
        self._watchdog_thread.start()
        logger.info("Safety watchdog started")

    def _stop_watchdog(self):
        """Stop the watchdog thread."""
        self._watchdog_running = False

    def _watchdog_loop(self):
        """Background safety monitor."""
        while self._watchdog_running and self._armed:
            try:
                self._watchdog_check()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
            time.sleep(self.geofence.watchdog_interval_s)

    def _watchdog_check(self):
        """Perform safety checks."""
        if not self._armed or not self._drone:
            return

        # Battery check (ignore -1 which means read error)
        try:
            battery = self._drone.get_battery()
            if battery > 0 and battery < self.geofence.min_battery_pct:
                logger.critical(f"LOW BATTERY: {battery}% — auto-landing")
                self.audit.log("watchdog", "AUTO_LAND", {"reason": "low_battery"},
                               f"Battery {battery}%", blocked=False)
                self._drone.land()
                self._armed = False
                return
        except Exception:
            pass

        # Command timeout check
        elapsed = time.monotonic() - self._last_command_time
        if elapsed > self.geofence.command_timeout_s:
            logger.critical(f"COMMAND TIMEOUT: {elapsed:.0f}s — auto-landing")
            self.audit.log("watchdog", "AUTO_LAND", {"reason": "timeout"},
                           f"No command for {elapsed:.0f}s", blocked=False)
            try:
                self._drone.land()
            except Exception:
                self._drone.emergency()
            self._armed = False
            return

        # Position check (from drone telemetry if available)
        try:
            height = self._drone.get_height()
            if height > self.geofence.max_height_cm:
                logger.critical(f"HEIGHT BREACH: {height}cm — auto-landing")
                self.audit.log("watchdog", "AUTO_LAND", {"reason": "height_breach"},
                               f"Height {height}cm", blocked=False)
                self._drone.land()
                self._armed = False
                return
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------

    def get_status(self) -> dict:
        """Get safety proxy status."""
        return {
            "safety_level": self.safety_level.value,
            "kill_switch": self._kill_switch,
            "armed": self._armed,
            "connected": self._connected,
            "watchdog_running": self._watchdog_running,
            "position": dict(self._current_position),
            "home": dict(self._home_position),
            "geofence": {
                "max_height_cm": self.geofence.max_height_cm,
                "max_distance_cm": self.geofence.max_distance_cm,
                "max_speed_cms": self.geofence.max_speed_cms,
                "min_battery_pct": self.geofence.min_battery_pct,
                "command_timeout_s": self.geofence.command_timeout_s,
            },
            "rate_limiter": {
                "current_rate": round(self.rate_limiter.current_rate, 1),
            },
            "audit": {
                "total_commands": self.audit.total_count(),
                "blocked_commands": self.audit.blocked_count(),
            },
            "authorized_ips": sorted(AUTHORIZED_IPS),
        }


# ---------------------------------------------------------------------------
# Flask API for drone control (runs on Raspi)
# ---------------------------------------------------------------------------

def create_drone_api(proxy: DroneSafetyProxy):
    """Create Flask API for secure drone control."""
    from flask import Flask, jsonify, request

    api = Flask(__name__)

    @api.before_request
    def check_auth():
        """Block unauthorized IPs at the API level."""
        client_ip = request.remote_addr
        if client_ip not in AUTHORIZED_IPS:
            logger.warning(f"UNAUTHORIZED drone API access from {client_ip}")
            return jsonify({
                "error": "Unauthorized",
                "your_ip": client_ip,
            }), 403

    @api.route("/drone/status", methods=["GET"])
    def drone_status():
        return jsonify(proxy.get_status())

    @api.route("/drone/command", methods=["POST"])
    def drone_command():
        data = request.get_json(force=True, silent=True) or {}
        command = data.get("command", "")
        params = data.get("params", {})
        source_ip = request.remote_addr

        if not command:
            return jsonify({"error": "Missing 'command' field"}), 400

        result = proxy.execute(command, params, source_ip)
        blocked = result.startswith("BLOCKED")
        return jsonify({
            "command": command,
            "result": result,
            "blocked": blocked,
        }), 200 if not blocked else 403

    @api.route("/drone/kill", methods=["POST"])
    def kill_switch():
        result = proxy.activate_kill_switch(request.remote_addr)
        return jsonify({"result": result})

    @api.route("/drone/kill/reset", methods=["POST"])
    def reset_kill():
        result = proxy.reset_kill_switch(request.remote_addr)
        return jsonify({"result": result})

    @api.route("/drone/audit", methods=["GET"])
    def audit_log():
        n = request.args.get("n", 20, type=int)
        return jsonify(proxy.audit.get_recent(n))

    @api.route("/drone/geofence", methods=["GET"])
    def geofence_info():
        return jsonify({
            "max_height_cm": proxy.geofence.max_height_cm,
            "max_distance_cm": proxy.geofence.max_distance_cm,
            "max_speed_cms": proxy.geofence.max_speed_cms,
            "boundary": {
                "x": [proxy.geofence.boundary_min_x, proxy.geofence.boundary_max_x],
                "y": [proxy.geofence.boundary_min_y, proxy.geofence.boundary_max_y],
            },
        })

    # --- WiFi management endpoints ---

    @api.route("/drone/wifi/connect", methods=["POST"])
    def wifi_connect():
        """Connect wlan0 (internal WiFi) to Tello drone WiFi (T0K10-NET).

        wlan0 = internal WiFi, switches from home network to drone network.
        wlan1 = external USB adapter, stays in monitor mode for WiFi defense.
        Tailscale runs over eth0 (cable), so connectivity is never lost.
        """
        import subprocess

        iface = "wlan0"
        profile = "tello-drone"
        steps = []

        try:
            # Update profile to use wlan0
            subprocess.run(
                ["sudo", "nmcli", "connection", "modify", profile,
                 "802-11-wireless.band", "bg",
                 "connection.interface-name", iface],
                capture_output=True, timeout=5
            )
            steps.append(f"profile configured for {iface}")

            tello_ssid = ""

            # If we found a specific TELLO SSID, update the profile
            if tello_ssid:
                subprocess.run(
                    ["sudo", "nmcli", "connection", "modify", profile,
                     "802-11-wireless.ssid", tello_ssid],
                    capture_output=True, timeout=5
                )
                steps.append(f"profile ssid set to {tello_ssid}")

            # Step 4: Connect
            r = subprocess.run(
                ["sudo", "nmcli", "connection", "up", profile, "ifname", iface],
                capture_output=True, text=True, timeout=20
            )
            if r.returncode == 0:
                steps.append("connected!")
                logger.info(f"Drone WiFi connected via {iface}: {tello_ssid}")
                return jsonify({"ok": True, "steps": steps,
                                "result": f"Connected to {tello_ssid or profile} on {iface}"})
            else:
                steps.append(f"connect failed: {r.stderr.strip()}")

                # Fallback: try direct connect if profile doesn't work
                if tello_ssid:
                    r2 = subprocess.run(
                        ["sudo", "nmcli", "dev", "wifi", "connect", tello_ssid,
                         "ifname", iface],
                        capture_output=True, text=True, timeout=20
                    )
                    if r2.returncode == 0:
                        steps.append("fallback connect OK!")
                        return jsonify({"ok": True, "steps": steps,
                                        "result": f"Connected to {tello_ssid} on {iface}"})
                    steps.append(f"fallback failed: {r2.stderr.strip()}")

                return jsonify({"ok": False, "steps": steps,
                                "error": "Could not connect to Tello WiFi"}), 500

        except Exception as e:
            steps.append(f"exception: {str(e)}")
            return jsonify({"ok": False, "steps": steps, "error": str(e)}), 500

    @api.route("/drone/wifi/disconnect", methods=["POST"])
    def wifi_disconnect():
        """Disconnect wlan0 from Tello WiFi and reconnect to home network (MrM35G)."""
        import subprocess

        steps = []

        try:
            # Disconnect from Tello
            subprocess.run(
                ["sudo", "nmcli", "connection", "down", "tello-drone"],
                capture_output=True, timeout=10
            )
            steps.append("tello-drone disconnected")

            # Reconnect wlan0 to home network
            subprocess.run(
                ["sudo", "nmcli", "connection", "up", "MrM35G", "ifname", "wlan0"],
                capture_output=True, timeout=15
            )
            steps.append("MrM35G reconnected on wlan0")

            logger.info("Drone WiFi disconnected, home network restored on wlan0")
            return jsonify({"ok": True, "steps": steps,
                            "result": "Home network restored on wlan0"})
        except Exception as e:
            return jsonify({"ok": False, "steps": steps, "error": str(e)}), 500

    @api.route("/drone/wifi/status", methods=["GET"])
    def wifi_status():
        """Get current WiFi connection status for drone."""
        import subprocess
        try:
            r = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
                capture_output=True, text=True, timeout=5
            )
            connections = r.stdout.strip().split("\n") if r.stdout.strip() else []
            on_tello = any("tello" in c.lower() for c in connections)

            # Check wlan1 mode
            iw_r = subprocess.run(
                ["iw", "dev", "wlan1", "info"],
                capture_output=True, text=True, timeout=5
            )
            wlan1_mode = "unknown"
            for line in iw_r.stdout.split("\n"):
                if "type" in line.lower():
                    wlan1_mode = line.strip().split()[-1] if line.strip() else "unknown"

            return jsonify({
                "on_tello_wifi": on_tello,
                "wlan1_mode": wlan1_mode,
                "active_connections": connections,
                "drone_connected": proxy._connected,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # --- Snapshot endpoint (selfie / photo) ---

    @api.route("/drone/snapshot", methods=["POST"])
    def drone_snapshot():
        """Take a photo and return it as JPEG."""
        from flask import Response, send_file
        result = proxy.execute("take_photo", {}, request.remote_addr)
        if result.startswith("PHOTO_SAVED:"):
            filepath = result.split(":", 1)[1]
            try:
                return send_file(filepath, mimetype="image/jpeg")
            except Exception as e:
                return jsonify({"error": f"File send error: {e}"}), 500
        return jsonify({"error": result}), 500

    # --- Visual Tracker endpoints ---

    _tracker = None

    @api.route("/drone/tracker/start", methods=["POST"])
    def tracker_start():
        nonlocal _tracker
        if _tracker is None:
            from .drone_tracker import DroneVisualTracker
            _tracker = DroneVisualTracker(proxy)
            _tracker.start()
            proxy._tracker = _tracker
        return jsonify({"ok": True, "status": "tracker started"})

    @api.route("/drone/tracker/stop", methods=["POST"])
    def tracker_stop():
        nonlocal _tracker
        if _tracker:
            _tracker.stop()
            _tracker = None
        return jsonify({"ok": True, "status": "tracker stopped"})

    @api.route("/drone/tracker/activate", methods=["POST"])
    def tracker_activate():
        if _tracker:
            _tracker.activate()
            return jsonify({"ok": True, "status": "tracking active"})
        return jsonify({"error": "Tracker not started"}), 400

    @api.route("/drone/tracker/status", methods=["GET"])
    def tracker_status():
        if _tracker:
            return jsonify(_tracker.get_status())
        return jsonify({"running": False})

    @api.route("/drone/tracker/snapshot", methods=["GET"])
    def tracker_snapshot():
        from flask import Response
        if not _tracker:
            return Response("Tracker not running", status=503)
        jpg = _tracker.get_frame()
        if not jpg:
            return Response("No frame", status=503)
        return Response(jpg, mimetype="image/jpeg")

    # --- Drone Vision (visual servoing from entity camera) ---

    _vision_player = None

    @api.route("/drone/vision/status", methods=["GET"])
    def vision_status():
        if _vision_player:
            return jsonify(_vision_player.get_status())
        return jsonify({"mode": "idle", "registered": False, "detected": False})

    @api.route("/drone/vision/mode", methods=["POST"])
    def vision_mode():
        nonlocal _vision_player
        data = request.get_json(force=True, silent=True) or {}
        mode = data.get("mode", "track")
        if _vision_player is None:
            from .drone_vision import DroneVisionPlayer
            _vision_player = DroneVisionPlayer()
            _vision_player.start()
            proxy._vision_player = _vision_player
        _vision_player.set_mode(mode)
        return jsonify({"ok": True, "mode": mode})

    @api.route("/drone/vision/register", methods=["POST"])
    def vision_register():
        nonlocal _vision_player
        if _vision_player is None:
            from .drone_vision import DroneVisionPlayer
            _vision_player = DroneVisionPlayer()
            _vision_player.start()
            proxy._vision_player = _vision_player
        _vision_player.set_mode("register")
        return jsonify({"ok": True, "status": "Show drone to camera, registering..."})

    return api


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    level_name = os.getenv("DRONE_SAFETY_LEVEL", "demo").upper()
    try:
        level = SafetyLevel[level_name]
    except KeyError:
        level = SafetyLevel.DEMO

    proxy = DroneSafetyProxy(safety_level=level)

    # Auto-connect thread: watches for Tello and sends SDK command instantly
    def auto_connect_loop():
        import subprocess
        while True:
            if proxy._connected:
                # Keepalive — send battery query every 10s to prevent auto-shutdown
                try:
                    if proxy._drone:
                        proxy._drone.get_battery()
                except Exception:
                    proxy._connected = False
                    proxy._drone = None
                    logger.warning("Tello connection lost, will retry...")
                time.sleep(10)
            else:
                # Check if Tello is reachable
                try:
                    r = subprocess.run(
                        ["ping", "-c", "1", "-W", "1", "192.168.10.1"],
                        capture_output=True, timeout=3
                    )
                    if r.returncode == 0:
                        logger.info("Tello detected! Auto-connecting...")
                        result = proxy._do_connect({})
                        logger.info(f"Auto-connect: {result}")
                except Exception:
                    pass
                time.sleep(3)

    t = threading.Thread(target=auto_connect_loop, daemon=True)
    t.start()
    logger.info("Auto-connect thread started")

    port = int(os.getenv("DRONE_API_PORT", "5001"))
    print(f"\nDrone Safety Proxy")
    print(f"  Safety level: {level.value}")
    print(f"  Geofence: {proxy.geofence.max_distance_cm}cm radius, "
          f"{proxy.geofence.max_height_cm}cm height")
    print(f"  Boundary: {proxy.geofence.boundary_min_x} to {proxy.geofence.boundary_max_x} cm (x), "
          f"{proxy.geofence.boundary_min_y} to {proxy.geofence.boundary_max_y} cm (y)")
    print(f"  Auto-connect: ENABLED (keepalive every 10s)")
    print(f"  Authorized IPs: {sorted(AUTHORIZED_IPS)}")
    print(f"  API port: {port}")
    print()

    app = create_drone_api(proxy)
    app.run(host="0.0.0.0", port=port)
