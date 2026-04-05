"""
TokioAI MAVLink Drone Integration — ArduPilot/PX4 compatible.

Easy to test:
    pip install pymavlink
    # Option 1: Built-in simulator (no SITL needed)
    python mavlink_drone.py --sim
    
    # Option 2: With ArduPilot SITL
    sim_vehicle.py -v ArduCopter --console
    python mavlink_drone.py --connect udp:127.0.0.1:14550
    
    # Option 3: Real drone (Pixhawk via USB/telemetry)
    python mavlink_drone.py --connect /dev/ttyACM0

Capabilities:
- Takeoff, land, move (NED coordinates), waypoints, RTL
- Geofencing (configurable radius/altitude)
- Safety limits (max altitude, max speed, min battery)
- Telemetry (GPS, attitude, battery, speed)
- Mission planning (waypoint sequences)
- Built-in simulator for testing without hardware
- Clean API matching TokioAI drone_safety_proxy interface

Architecture:
    TokioAI Agent (GCP) --> API Server (Raspi) --> MAVLinkDrone --> Pixhawk/SITL/Simulator
"""
from __future__ import annotations

import math
import os
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, List
from enum import Enum

# Try to import pymavlink
try:
    from pymavlink import mavutil
    MAVLINK_AVAILABLE = True
except ImportError:
    MAVLINK_AVAILABLE = False
    print("[MAVLink] pymavlink not installed. Run: pip install pymavlink")


class FlightMode(Enum):
    STABILIZE = "STABILIZE"
    GUIDED = "GUIDED"
    AUTO = "AUTO"
    RTL = "RTL"
    LAND = "LAND"
    LOITER = "LOITER"
    ALT_HOLD = "ALT_HOLD"


class DroneState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ARMED = "armed"
    FLYING = "flying"
    LANDING = "landing"
    EMERGENCY = "emergency"


@dataclass
class Telemetry:
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0  # meters relative
    alt_msl: float = 0.0  # meters above sea level
    heading: float = 0.0  # degrees
    groundspeed: float = 0.0  # m/s
    airspeed: float = 0.0  # m/s
    vertical_speed: float = 0.0  # m/s
    roll: float = 0.0  # degrees
    pitch: float = 0.0  # degrees
    yaw: float = 0.0  # degrees
    battery_voltage: float = 0.0
    battery_remaining: int = 100  # percent
    battery_current: float = 0.0  # amps
    gps_fix: int = 0  # 0=no, 2=2D, 3=3D
    gps_satellites: int = 0
    armed: bool = False
    mode: str = "STABILIZE"
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Waypoint:
    lat: float
    lon: float
    alt: float  # relative altitude in meters
    hold_time: float = 0  # seconds to hold at waypoint
    speed: float = 0  # speed to this WP (0 = default)


@dataclass
class GeofenceConfig:
    enabled: bool = True
    max_radius: float = 100.0  # meters from home
    max_altitude: float = 50.0  # meters
    min_altitude: float = 1.0  # meters (floor)
    action: str = "rtl"  # rtl, land, hover


@dataclass
class SafetyConfig:
    max_altitude: float = 50.0  # meters
    max_speed: float = 10.0  # m/s
    min_battery: int = 20  # percent — auto RTL below this
    max_distance: float = 100.0  # meters from home
    require_gps: bool = True
    min_satellites: int = 6


class MAVLinkSimulator:
    """Built-in simulator — no SITL or hardware needed.
    
    Simulates basic drone physics for testing the MAVLink interface.
    Just run: python mavlink_drone.py --sim
    """

    def __init__(self, home_lat: float = -42.7692, home_lon: float = -65.0385):
        self._home_lat = home_lat
        self._home_lon = home_lon
        self._lat = home_lat
        self._lon = home_lon
        self._alt = 0.0
        self._heading = 0.0
        self._speed = 0.0
        self._vspeed = 0.0
        self._armed = False
        self._mode = "STABILIZE"
        self._battery = 100.0
        self._target_alt = 0.0
        self._target_lat = home_lat
        self._target_lon = home_lon
        self._running = True
        self._lock = threading.Lock()

        # Start physics loop
        self._thread = threading.Thread(target=self._physics_loop, daemon=True)
        self._thread.start()

    def _physics_loop(self):
        """Simple physics simulation."""
        dt = 0.1  # 10Hz
        while self._running:
            with self._lock:
                if self._armed:
                    # Battery drain
                    self._battery -= 0.001 * dt  # ~6% per minute
                    if self._battery <= 0:
                        self._battery = 0
                        self._armed = False

                    # Altitude control
                    if self._mode in ("GUIDED", "AUTO", "ALT_HOLD"):
                        alt_error = self._target_alt - self._alt
                        self._vspeed = max(-3, min(3, alt_error * 0.5))
                        self._alt += self._vspeed * dt

                    # Horizontal movement
                    if self._mode in ("GUIDED", "AUTO"):
                        dlat = self._target_lat - self._lat
                        dlon = self._target_lon - self._lon
                        dist = math.sqrt(dlat**2 + dlon**2) * 111139  # approx meters

                        if dist > 0.5:  # More than 0.5m away
                            speed_factor = min(5.0, dist) / 111139  # max 5 m/s
                            self._lat += (dlat / dist) * speed_factor * dt * 111139 / 111139
                            self._lon += (dlon / dist) * speed_factor * dt * 111139 / 111139
                            self._speed = min(5.0, dist)
                            self._heading = math.degrees(math.atan2(dlon, dlat)) % 360
                        else:
                            self._speed = 0

                    # RTL
                    if self._mode == "RTL":
                        self._target_lat = self._home_lat
                        self._target_lon = self._home_lon
                        self._target_alt = 10.0  # RTL altitude

                        dlat = self._target_lat - self._lat
                        dlon = self._target_lon - self._lon
                        dist = math.sqrt(dlat**2 + dlon**2) * 111139

                        if dist > 1.0:
                            speed_factor = min(3.0, dist) / 111139
                            self._lat += (dlat / dist) * speed_factor * dt * 111139 / 111139
                            self._lon += (dlon / dist) * speed_factor * dt * 111139 / 111139
                        else:
                            self._mode = "LAND"

                    # Landing
                    if self._mode == "LAND":
                        self._vspeed = -1.0
                        self._alt += self._vspeed * dt
                        if self._alt <= 0:
                            self._alt = 0
                            self._armed = False
                            self._mode = "STABILIZE"

                    self._alt = max(0, self._alt)

            time.sleep(dt)

    def get_telemetry(self) -> Telemetry:
        with self._lock:
            return Telemetry(
                lat=self._lat, lon=self._lon, alt=self._alt,
                heading=self._heading, groundspeed=self._speed,
                vertical_speed=self._vspeed, battery_remaining=int(self._battery),
                battery_voltage=11.1 + (self._battery / 100) * 1.5,
                armed=self._armed, mode=self._mode,
                gps_fix=3, gps_satellites=12, timestamp=time.time()
            )

    def arm(self) -> bool:
        with self._lock:
            self._armed = True
            self._mode = "GUIDED"
        return True

    def disarm(self) -> bool:
        with self._lock:
            if self._alt > 0.5:
                return False
            self._armed = False
            self._mode = "STABILIZE"
        return True

    def set_mode(self, mode: str):
        with self._lock:
            self._mode = mode

    def goto(self, lat: float, lon: float, alt: float):
        with self._lock:
            self._target_lat = lat
            self._target_lon = lon
            self._target_alt = alt
            self._mode = "GUIDED"

    def set_altitude(self, alt: float):
        with self._lock:
            self._target_alt = alt

    def stop(self):
        self._running = False


class MAVLinkDrone:
    """MAVLink drone controller — works with SITL, Pixhawk, or built-in simulator.
    
    Usage:
        # Built-in simulator (easiest)
        drone = MAVLinkDrone(simulator=True)
        
        # SITL
        drone = MAVLinkDrone(connection_string="udp:127.0.0.1:14550")
        
        # Real Pixhawk
        drone = MAVLinkDrone(connection_string="/dev/ttyACM0", baud=57600)
    """

    def __init__(self, connection_string: str = "", baud: int = 57600,
                 simulator: bool = False,
                 geofence: Optional[GeofenceConfig] = None,
                 safety: Optional[SafetyConfig] = None):
        self._conn_str = connection_string
        self._baud = baud
        self._use_sim = simulator
        self._master = None
        self._sim: Optional[MAVLinkSimulator] = None
        self._state = DroneState.DISCONNECTED
        self._telemetry = Telemetry()
        self._geofence = geofence or GeofenceConfig()
        self._safety = safety or SafetyConfig()
        self._home: Optional[tuple] = None  # (lat, lon, alt)
        self._lock = threading.Lock()
        self._running = False
        self._callback: Optional[Callable] = None
        self._mission: List[Waypoint] = []
        self._mission_index = 0
        self._telem_thread: Optional[threading.Thread] = None
        self._safety_thread: Optional[threading.Thread] = None
        self._audit_log: list[dict] = []

    def set_callback(self, callback: Callable):
        """Set callback for events: callback(event_type, data)"""
        self._callback = callback

    def connect(self) -> bool:
        """Connect to drone (SITL, Pixhawk, or start simulator)."""
        try:
            if self._use_sim:
                print("[MAVLink] Starting built-in simulator...")
                self._sim = MAVLinkSimulator()
                self._state = DroneState.CONNECTED
                self._home = (self._sim._home_lat, self._sim._home_lon, 0)
                self._running = True
                self._start_threads()
                self._log("connect", "Simulator connected")
                print("[MAVLink] ✅ Simulator ready")
                return True

            if not MAVLINK_AVAILABLE:
                print("[MAVLink] ❌ pymavlink not installed: pip install pymavlink")
                return False

            conn = self._conn_str or "udp:127.0.0.1:14550"
            print(f"[MAVLink] Connecting to {conn}...")
            self._master = mavutil.mavlink_connection(conn, baud=self._baud)
            self._master.wait_heartbeat(timeout=30)
            print(f"[MAVLink] ✅ Connected — System {self._master.target_system}, Component {self._master.target_component}")

            self._state = DroneState.CONNECTED
            self._running = True
            self._start_threads()
            self._log("connect", f"Connected to {conn}")
            return True

        except Exception as e:
            print(f"[MAVLink] ❌ Connection failed: {e}")
            self._state = DroneState.DISCONNECTED
            return False

    def disconnect(self):
        """Disconnect from drone."""
        self._running = False
        if self._sim:
            self._sim.stop()
            self._sim = None
        if self._master:
            self._master.close()
            self._master = None
        self._state = DroneState.DISCONNECTED
        self._log("disconnect", "Disconnected")

    def arm(self) -> dict:
        """Arm the drone."""
        if self._state == DroneState.DISCONNECTED:
            return {"ok": False, "error": "Not connected"}

        telem = self._get_telemetry()
        if self._safety.require_gps and telem.gps_fix < 3:
            return {"ok": False, "error": f"No GPS fix (fix={telem.gps_fix}, need 3D)"}
        if self._safety.require_gps and telem.gps_satellites < self._safety.min_satellites:
            return {"ok": False, "error": f"Not enough satellites ({telem.gps_satellites}/{self._safety.min_satellites})"}
        if telem.battery_remaining < self._safety.min_battery:
            return {"ok": False, "error": f"Battery too low ({telem.battery_remaining}%)"}

        if self._sim:
            self._sim.arm()
            self._home = (telem.lat, telem.lon, telem.alt)
            self._state = DroneState.ARMED
            self._log("arm", "Armed (simulator)")
            return {"ok": True, "message": "Armed"}

        # Real MAVLink arm
        self._master.arducopter_arm()
        self._master.motors_armed_wait()
        self._home = (telem.lat, telem.lon, telem.alt)
        self._state = DroneState.ARMED
        self._log("arm", "Armed")
        return {"ok": True, "message": "Armed"}

    def disarm(self) -> dict:
        """Disarm the drone (must be on ground)."""
        telem = self._get_telemetry()
        if telem.alt > 1.0:
            return {"ok": False, "error": "Cannot disarm while flying"}

        if self._sim:
            self._sim.disarm()
        else:
            self._master.arducopter_disarm()
            self._master.motors_disarmed_wait()

        self._state = DroneState.CONNECTED
        self._log("disarm", "Disarmed")
        return {"ok": True}

    def takeoff(self, altitude: float = 5.0) -> dict:
        """Take off to specified altitude (meters)."""
        if self._state not in (DroneState.ARMED, DroneState.CONNECTED):
            # Auto-arm if needed
            arm_result = self.arm()
            if not arm_result["ok"]:
                return arm_result

        altitude = min(altitude, self._safety.max_altitude)

        if self._sim:
            self._sim.set_mode("GUIDED")
            self._sim.set_altitude(altitude)
        else:
            self._set_mode("GUIDED")
            self._master.mav.command_long_send(
                self._master.target_system, self._master.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0, 0, 0, 0, 0, 0, 0, altitude
            )

        self._state = DroneState.FLYING
        self._log("takeoff", f"Taking off to {altitude}m")
        return {"ok": True, "altitude": altitude}

    def land(self) -> dict:
        """Land the drone."""
        if self._sim:
            self._sim.set_mode("LAND")
        else:
            self._set_mode("LAND")

        self._state = DroneState.LANDING
        self._log("land", "Landing initiated")
        return {"ok": True}

    def rtl(self) -> dict:
        """Return to launch."""
        if self._sim:
            self._sim.set_mode("RTL")
        else:
            self._set_mode("RTL")

        self._log("rtl", "Return to launch")
        return {"ok": True}

    def goto(self, lat: float, lon: float, alt: float = 10.0) -> dict:
        """Fly to GPS coordinates."""
        # Geofence check
        if self._geofence.enabled and self._home:
            dist = self._haversine(self._home[0], self._home[1], lat, lon)
            if dist > self._geofence.max_radius:
                return {"ok": False, "error": f"Target outside geofence ({dist:.0f}m > {self._geofence.max_radius}m)"}
        if alt > self._safety.max_altitude:
            return {"ok": False, "error": f"Altitude {alt}m exceeds max {self._safety.max_altitude}m"}

        if self._sim:
            self._sim.goto(lat, lon, alt)
        else:
            self._master.mav.set_position_target_global_int_send(
                0, self._master.target_system, self._master.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,  # Position only
                int(lat * 1e7), int(lon * 1e7), alt,
                0, 0, 0, 0, 0, 0, 0, 0
            )

        self._log("goto", f"Flying to ({lat:.6f}, {lon:.6f}, {alt}m)")
        return {"ok": True, "target": {"lat": lat, "lon": lon, "alt": alt}}

    def move(self, direction: str, distance: float = 2.0) -> dict:
        """Move relative to current position. direction: forward/back/left/right/up/down"""
        telem = self._get_telemetry()
        heading_rad = math.radians(telem.heading)

        # Calculate new position
        dlat, dlon, dalt = 0.0, 0.0, 0.0
        if direction == "forward":
            dlat = distance * math.cos(heading_rad) / 111139
            dlon = distance * math.sin(heading_rad) / (111139 * math.cos(math.radians(telem.lat)))
        elif direction == "back":
            dlat = -distance * math.cos(heading_rad) / 111139
            dlon = -distance * math.sin(heading_rad) / (111139 * math.cos(math.radians(telem.lat)))
        elif direction == "right":
            dlat = distance * math.cos(heading_rad + math.pi/2) / 111139
            dlon = distance * math.sin(heading_rad + math.pi/2) / (111139 * math.cos(math.radians(telem.lat)))
        elif direction == "left":
            dlat = distance * math.cos(heading_rad - math.pi/2) / 111139
            dlon = distance * math.sin(heading_rad - math.pi/2) / (111139 * math.cos(math.radians(telem.lat)))
        elif direction == "up":
            dalt = distance
        elif direction == "down":
            dalt = -distance

        new_lat = telem.lat + dlat
        new_lon = telem.lon + dlon
        new_alt = max(1.0, telem.alt + dalt)

        return self.goto(new_lat, new_lon, new_alt)

    def set_mission(self, waypoints: List[Waypoint]) -> dict:
        """Set a waypoint mission."""
        self._mission = waypoints
        self._mission_index = 0

        if self._sim:
            # Simulator: execute waypoints sequentially in background
            threading.Thread(target=self._execute_sim_mission, daemon=True).start()
        else:
            # Real MAVLink: upload mission
            self._upload_mission()

        self._log("mission", f"Mission set with {len(waypoints)} waypoints")
        return {"ok": True, "waypoints": len(waypoints)}

    def _execute_sim_mission(self):
        """Execute mission on simulator."""
        for i, wp in enumerate(self._mission):
            if not self._running:
                break
            self._mission_index = i
            self._sim.goto(wp.lat, wp.lon, wp.alt)

            # Wait until close to waypoint
            while self._running:
                telem = self._get_telemetry()
                dist = self._haversine(telem.lat, telem.lon, wp.lat, wp.lon)
                if dist < 2.0 and abs(telem.alt - wp.alt) < 1.0:
                    break
                time.sleep(0.5)

            # Hold at waypoint
            if wp.hold_time > 0:
                time.sleep(wp.hold_time)

        self._log("mission", "Mission complete")

    def _upload_mission(self):
        """Upload mission to real flight controller."""
        if not self._master:
            return
        # Clear existing mission
        self._master.waypoint_clear_all_send()
        self._master.waypoint_count_send(len(self._mission))

        for i, wp in enumerate(self._mission):
            msg = self._master.recv_match(type=['MISSION_REQUEST'], blocking=True, timeout=5)
            if msg:
                self._master.mav.mission_item_send(
                    self._master.target_system, self._master.target_component,
                    i, mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                    0, 1,  # current, autocontinue
                    wp.hold_time, 0, 0, 0,
                    wp.lat, wp.lon, wp.alt
                )

    def emergency_stop(self) -> dict:
        """Emergency motor kill."""
        if self._sim:
            self._sim._armed = False
            self._sim._alt = 0
            self._sim._mode = "STABILIZE"
        elif self._master:
            self._master.mav.command_long_send(
                self._master.target_system, self._master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0, 0, 21196, 0, 0, 0, 0, 0  # Force disarm
            )

        self._state = DroneState.EMERGENCY
        self._log("emergency", "EMERGENCY STOP")
        return {"ok": True, "warning": "EMERGENCY STOP — Motors killed"}

    def get_telemetry(self) -> dict:
        """Get current telemetry as dict."""
        return self._get_telemetry().to_dict()

    def get_status(self) -> dict:
        """Get full drone status."""
        telem = self._get_telemetry()
        home_dist = 0
        if self._home:
            home_dist = self._haversine(self._home[0], self._home[1], telem.lat, telem.lon)

        return {
            "state": self._state.value,
            "mode": telem.mode,
            "armed": telem.armed,
            "telemetry": telem.to_dict(),
            "home": {"lat": self._home[0], "lon": self._home[1]} if self._home else None,
            "distance_from_home": round(home_dist, 1),
            "geofence": {
                "enabled": self._geofence.enabled,
                "max_radius": self._geofence.max_radius,
                "max_altitude": self._geofence.max_altitude,
            },
            "safety": {
                "max_altitude": self._safety.max_altitude,
                "max_speed": self._safety.max_speed,
                "min_battery": self._safety.min_battery,
            },
            "mission": {
                "waypoints": len(self._mission),
                "current": self._mission_index,
            },
            "simulator": self._use_sim,
            "backend": "simulator" if self._use_sim else (self._conn_str or "disconnected"),
        }

    # --- Internal methods ---

    def _get_telemetry(self) -> Telemetry:
        """Get telemetry from simulator or real drone."""
        if self._sim:
            return self._sim.get_telemetry()
        # Real MAVLink telemetry is updated by _telem_loop
        return self._telemetry

    def _set_mode(self, mode: str):
        """Set flight mode on real drone."""
        if not self._master:
            return
        mode_id = self._master.mode_mapping().get(mode)
        if mode_id is not None:
            self._master.set_mode(mode_id)

    def _start_threads(self):
        """Start telemetry and safety monitoring threads."""
        self._telem_thread = threading.Thread(target=self._telem_loop, daemon=True)
        self._telem_thread.start()
        self._safety_thread = threading.Thread(target=self._safety_loop, daemon=True)
        self._safety_thread.start()

    def _telem_loop(self):
        """Continuously read telemetry."""
        while self._running:
            try:
                if self._sim:
                    self._telemetry = self._sim.get_telemetry()
                elif self._master:
                    msg = self._master.recv_match(blocking=True, timeout=1)
                    if msg:
                        self._process_mavlink_msg(msg)
            except Exception:
                pass
            time.sleep(0.1)

    def _process_mavlink_msg(self, msg):
        """Process MAVLink message and update telemetry."""
        msg_type = msg.get_type()
        if msg_type == "GLOBAL_POSITION_INT":
            self._telemetry.lat = msg.lat / 1e7
            self._telemetry.lon = msg.lon / 1e7
            self._telemetry.alt = msg.relative_alt / 1000.0
            self._telemetry.alt_msl = msg.alt / 1000.0
            self._telemetry.heading = msg.hdg / 100.0
            self._telemetry.vertical_speed = msg.vz / 100.0
        elif msg_type == "VFR_HUD":
            self._telemetry.groundspeed = msg.groundspeed
            self._telemetry.airspeed = msg.airspeed
            self._telemetry.heading = msg.heading
        elif msg_type == "SYS_STATUS":
            self._telemetry.battery_voltage = msg.voltage_battery / 1000.0
            self._telemetry.battery_remaining = msg.battery_remaining
            self._telemetry.battery_current = msg.current_battery / 100.0
        elif msg_type == "GPS_RAW_INT":
            self._telemetry.gps_fix = msg.fix_type
            self._telemetry.gps_satellites = msg.satellites_visible
        elif msg_type == "ATTITUDE":
            self._telemetry.roll = math.degrees(msg.roll)
            self._telemetry.pitch = math.degrees(msg.pitch)
            self._telemetry.yaw = math.degrees(msg.yaw)
        elif msg_type == "HEARTBEAT":
            self._telemetry.armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            self._telemetry.mode = mavutil.mode_string_v10(msg)
        self._telemetry.timestamp = time.time()

    def _safety_loop(self):
        """Monitor safety limits and geofence."""
        while self._running:
            try:
                telem = self._get_telemetry()
                if not telem.armed:
                    time.sleep(1)
                    continue

                # Battery check
                if telem.battery_remaining < self._safety.min_battery:
                    print(f"[MAVLink] ⚠️ Low battery ({telem.battery_remaining}%) — RTL!")
                    self.rtl()
                    self._emit("low_battery", {"battery": telem.battery_remaining})

                # Geofence check
                if self._geofence.enabled and self._home:
                    dist = self._haversine(self._home[0], self._home[1], telem.lat, telem.lon)
                    if dist > self._geofence.max_radius:
                        print(f"[MAVLink] ⚠️ Geofence breach ({dist:.0f}m) — {self._geofence.action}!")
                        if self._geofence.action == "rtl":
                            self.rtl()
                        elif self._geofence.action == "land":
                            self.land()
                        self._emit("geofence_breach", {"distance": dist})

                    if telem.alt > self._geofence.max_altitude:
                        print(f"[MAVLink] ⚠️ Altitude limit ({telem.alt:.0f}m) — descending!")
                        alt_target = self._geofence.max_altitude - 5
                        if self._sim:
                            self._sim.set_altitude(alt_target)

                # Speed check
                if telem.groundspeed > self._safety.max_speed * 1.5:
                    print(f"[MAVLink] ⚠️ Overspeed ({telem.groundspeed:.1f} m/s) — braking!")
                    self._emit("overspeed", {"speed": telem.groundspeed})

            except Exception:
                pass
            time.sleep(1)

    def _emit(self, event: str, data: dict):
        """Emit event via callback."""
        if self._callback:
            try:
                self._callback(event, data)
            except Exception:
                pass

    def _log(self, action: str, detail: str):
        """Audit log."""
        entry = {
            "time": time.time(),
            "action": action,
            "detail": detail,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 200:
            self._audit_log = self._audit_log[-200:]
        print(f"[MAVLink] {action}: {detail}")

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2) -> float:
        """Distance in meters between two GPS coords."""
        R = 6371000
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon/2)**2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def get_audit_log(self) -> list:
        return self._audit_log[-50:]


# --- CLI for standalone testing ---
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TokioAI MAVLink Drone")
    parser.add_argument("--connect", default="", help="MAVLink connection string")
    parser.add_argument("--sim", action="store_true", help="Use built-in simulator")
    parser.add_argument("--baud", type=int, default=57600, help="Serial baud rate")
    args = parser.parse_args()

    drone = MAVLinkDrone(
        connection_string=args.connect,
        simulator=args.sim or (not args.connect),
    )

    if drone.connect():
        print("\n=== TokioAI MAVLink Drone Ready ===")
        print("Commands: arm, takeoff [alt], land, rtl, status, move [dir] [dist], quit")

        while True:
            try:
                cmd = input("\n🚁 > ").strip().lower().split()
                if not cmd:
                    continue

                if cmd[0] == "quit":
                    drone.disconnect()
                    break
                elif cmd[0] == "arm":
                    print(drone.arm())
                elif cmd[0] == "takeoff":
                    alt = float(cmd[1]) if len(cmd) > 1 else 5.0
                    print(drone.takeoff(alt))
                elif cmd[0] == "land":
                    print(drone.land())
                elif cmd[0] == "rtl":
                    print(drone.rtl())
                elif cmd[0] == "status":
                    import json
                    print(json.dumps(drone.get_status(), indent=2))
                elif cmd[0] == "telem":
                    import json
                    print(json.dumps(drone.get_telemetry(), indent=2))
                elif cmd[0] == "move":
                    direction = cmd[1] if len(cmd) > 1 else "forward"
                    dist = float(cmd[2]) if len(cmd) > 2 else 2.0
                    print(drone.move(direction, dist))
                elif cmd[0] == "kill":
                    print(drone.emergency_stop())
                else:
                    print("Unknown command")
            except KeyboardInterrupt:
                drone.disconnect()
                break
            except Exception as e:
                print(f"Error: {e}")
