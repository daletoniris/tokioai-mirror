#!/usr/bin/env python3
"""
TokioAI PiCar-X Safety Proxy — REST API for controlling PiCar-X robot.

Runs on the PiCar-X Raspberry Pi. Provides:
- Motor control (forward, backward, stop, turn)
- Servo control (camera pan/tilt, steering)
- Sensor readings (ultrasonic, grayscale, battery)
- Camera (snapshot, stream)
- Autonomous modes (obstacle avoidance, line tracking, patrol)
- Safety limits (speed cap, timeout auto-stop)

Port: 5002
"""
from __future__ import annotations

import io
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, send_file

# ── PiCar-X imports ──
try:
    from picarx import Picarx
    PICAR_AVAILABLE = True
except ImportError:
    PICAR_AVAILABLE = False

try:
    from vilib import Vilib
    VILIB_AVAILABLE = True
except ImportError:
    VILIB_AVAILABLE = False

try:
    from robot_hat import ADC
    ROBOT_HAT_AVAILABLE = True
except ImportError:
    ROBOT_HAT_AVAILABLE = False

# ── Config ──
PORT = int(os.getenv("PICAR_PORT", "5002"))
MAX_SPEED = int(os.getenv("PICAR_MAX_SPEED", "50"))
AUTO_STOP_TIMEOUT = float(os.getenv("PICAR_AUTO_STOP_TIMEOUT", "10.0"))
LOG_LEVEL = os.getenv("PICAR_LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("picar-proxy")

app = Flask(__name__)

# ── Global state ──
px = None  # Picarx instance
_lock = threading.Lock()
_auto_stop_timer = None
_is_moving = False
_current_speed = 0
_current_direction = "stopped"
_cam_pan = 0
_cam_tilt = 0
_steering = 0
_autonomous_mode = None  # None, "obstacle_avoid", "line_track", "patrol"
_autonomous_thread = None
_autonomous_stop = threading.Event()
_start_time = time.time()
_command_count = 0
_audit_log = []

# ── Audit ──
def _audit(action: str, params: dict = None, result: str = "ok"):
    global _command_count
    _command_count += 1
    entry = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "params": params or {},
        "result": result
    }
    _audit_log.append(entry)
    if len(_audit_log) > 200:
        _audit_log.pop(0)
    logger.info(f"[{action}] {params or ''} -> {result}")

# ── Safety: auto-stop ──
def _schedule_auto_stop(timeout: float = None):
    global _auto_stop_timer
    if _auto_stop_timer:
        _auto_stop_timer.cancel()
    t = timeout or AUTO_STOP_TIMEOUT
    _auto_stop_timer = threading.Timer(t, _force_stop)
    _auto_stop_timer.daemon = True
    _auto_stop_timer.start()

def _force_stop():
    global _is_moving, _current_speed, _current_direction
    with _lock:
        if px and _is_moving:
            px.stop()
            _is_moving = False
            _current_speed = 0
            _current_direction = "stopped"
            _audit("auto_stop", result="safety timeout")

def _cancel_auto_stop():
    global _auto_stop_timer
    if _auto_stop_timer:
        _auto_stop_timer.cancel()
        _auto_stop_timer = None

# ── Stop autonomous mode ──
def _stop_autonomous():
    global _autonomous_mode, _autonomous_thread
    if _autonomous_mode:
        _autonomous_stop.set()
        if _autonomous_thread and _autonomous_thread.is_alive():
            _autonomous_thread.join(timeout=3)
        _autonomous_mode = None
        _autonomous_thread = None
        _autonomous_stop.clear()
        with _lock:
            if px:
                px.stop()

# ── Init ──
def _init_picar():
    global px
    if not PICAR_AVAILABLE:
        logger.error("picarx library not installed!")
        return False
    try:
        px = Picarx()
        px.stop()
        px.set_cam_pan_angle(0)
        px.set_cam_tilt_angle(0)
        px.set_dir_servo_angle(0)
        logger.info("PiCar-X initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to init PiCar-X: {e}")
        return False

# ══════════════════════════════════════════════════════
# REST API
# ══════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "TokioAI PiCar-X Proxy",
        "version": "1.0.0",
        "status": "online" if px else "no_hardware",
        "uptime_s": round(time.time() - _start_time),
        "commands": _command_count,
    })

@app.route("/status", methods=["GET"])
def status():
    data = {
        "hardware": PICAR_AVAILABLE,
        "initialized": px is not None,
        "moving": _is_moving,
        "speed": _current_speed,
        "direction": _current_direction,
        "steering": _steering,
        "cam_pan": _cam_pan,
        "cam_tilt": _cam_tilt,
        "autonomous_mode": _autonomous_mode,
        "uptime_s": round(time.time() - _start_time),
        "commands": _command_count,
        "max_speed": MAX_SPEED,
    }
    # Add sensor readings
    if px:
        try:
            data["ultrasonic_cm"] = round(px.ultrasonic.read(), 1)
        except:
            data["ultrasonic_cm"] = -1
        try:
            data["grayscale"] = list(px.grayscale.read())
        except:
            data["grayscale"] = []
        try:
            batt_adc = ADC("A4")
            data["battery_v"] = round(batt_adc.read() * 3.3 / 4095 * 3, 2)
        except:
            data["battery_v"] = -1
    return jsonify(data)

@app.route("/sensors", methods=["GET"])
def sensors():
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    data = {}
    try:
        data["ultrasonic_cm"] = round(px.ultrasonic.read(), 1)
    except:
        data["ultrasonic_cm"] = -1
    try:
        data["grayscale"] = list(px.grayscale.read())
    except:
        data["grayscale"] = []
    try:
        batt_adc = ADC("A4")
        data["battery_v"] = round(batt_adc.read() * 3.3 / 4095 * 3, 2)
    except:
        data["battery_v"] = -1
    _audit("sensors", result=str(data))
    return jsonify(data)

# ── Movement ──

@app.route("/move", methods=["POST"])
def move():
    global _is_moving, _current_speed, _current_direction
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    direction = data.get("direction", "forward")
    speed = min(int(data.get("speed", 30)), MAX_SPEED)
    duration = min(float(data.get("duration", 1.0)), 30.0)  # max 30s
    angle = int(data.get("angle", 0))  # steering angle
    
    _stop_autonomous()
    
    with _lock:
        if angle:
            px.set_dir_servo_angle(max(-30, min(30, angle)))
        
        if direction == "forward":
            px.forward(speed)
        elif direction == "backward":
            px.backward(speed)
        elif direction == "left":
            px.set_dir_servo_angle(-30)
            px.forward(speed)
        elif direction == "right":
            px.set_dir_servo_angle(30)
            px.forward(speed)
        elif direction == "stop":
            px.stop()
            px.set_dir_servo_angle(0)
            _is_moving = False
            _current_speed = 0
            _current_direction = "stopped"
            _cancel_auto_stop()
            _audit("move", {"direction": "stop"})
            return jsonify({"status": "stopped"})
        else:
            return jsonify({"error": f"Unknown direction: {direction}"}), 400
        
        _is_moving = True
        _current_speed = speed
        _current_direction = direction
    
    _schedule_auto_stop(duration + 0.5)
    
    # Wait for duration then stop
    time.sleep(duration)
    with _lock:
        px.stop()
        if angle and direction not in ("left", "right"):
            pass  # keep steering angle
        else:
            px.set_dir_servo_angle(0)
        _is_moving = False
        _current_speed = 0
        _current_direction = "stopped"
    _cancel_auto_stop()
    
    _audit("move", {"direction": direction, "speed": speed, "duration": duration, "angle": angle})
    return jsonify({"status": "ok", "direction": direction, "speed": speed, "duration": duration})

@app.route("/stop", methods=["POST"])
def stop():
    global _is_moving, _current_speed, _current_direction
    _stop_autonomous()
    _cancel_auto_stop()
    with _lock:
        if px:
            px.stop()
            px.set_dir_servo_angle(0)
    _is_moving = False
    _current_speed = 0
    _current_direction = "stopped"
    _audit("stop")
    return jsonify({"status": "stopped"})

# ── Camera servos ──

@app.route("/camera", methods=["POST"])
def camera_servo():
    global _cam_pan, _cam_tilt
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    pan = data.get("pan")  # -90 to 90
    tilt = data.get("tilt")  # -35 to 65
    
    with _lock:
        if pan is not None:
            pan = max(-90, min(90, int(pan)))
            px.set_cam_pan_angle(pan)
            _cam_pan = pan
        if tilt is not None:
            tilt = max(-35, min(65, int(tilt)))
            px.set_cam_tilt_angle(tilt)
            _cam_tilt = tilt
    
    _audit("camera", {"pan": pan, "tilt": tilt})
    return jsonify({"status": "ok", "pan": _cam_pan, "tilt": _cam_tilt})

@app.route("/camera/look", methods=["POST"])
def camera_look():
    """Look in a named direction."""
    global _cam_pan, _cam_tilt
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    direction = data.get("direction", "center")
    
    presets = {
        "center": (0, 0),
        "left": (-60, 0),
        "right": (60, 0),
        "up": (0, -30),
        "down": (0, 50),
        "front_left": (-45, -10),
        "front_right": (45, -10),
    }
    
    pan, tilt = presets.get(direction, (0, 0))
    with _lock:
        px.set_cam_pan_angle(pan)
        px.set_cam_tilt_angle(tilt)
        _cam_pan = pan
        _cam_tilt = tilt
    
    _audit("camera_look", {"direction": direction, "pan": pan, "tilt": tilt})
    return jsonify({"status": "ok", "direction": direction, "pan": pan, "tilt": tilt})

# ── Steering ──

@app.route("/steer", methods=["POST"])
def steer():
    global _steering
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    angle = max(-30, min(30, int(data.get("angle", 0))))
    
    with _lock:
        px.set_dir_servo_angle(angle)
        _steering = angle
    
    _audit("steer", {"angle": angle})
    return jsonify({"status": "ok", "angle": angle})

# ── Snapshot ──

@app.route("/snapshot", methods=["GET", "POST"])
def snapshot():
    if not VILIB_AVAILABLE:
        return jsonify({"error": "vilib not available"}), 503
    try:
        Vilib.camera_start(vflip=False, hflip=False)
        time.sleep(0.5)
        snap_path = "/tmp/picar_snapshot.jpg"
        Vilib.take_photo("picar_snapshot", path="/tmp")
        time.sleep(0.3)
        Vilib.camera_close()
        if os.path.exists(snap_path):
            return send_file(snap_path, mimetype="image/jpeg")
        return jsonify({"error": "Snapshot failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Autonomous modes ──

@app.route("/obstacle_avoid", methods=["POST"])
def obstacle_avoid():
    global _autonomous_mode, _autonomous_thread
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    duration = min(float(data.get("duration", 20)), 120)  # max 2min
    speed = min(int(data.get("speed", 30)), MAX_SPEED)
    
    _stop_autonomous()
    
    def _obstacle_loop():
        global _is_moving, _current_speed, _current_direction
        _is_moving = True
        _current_speed = speed
        log = []
        start = time.time()
        
        while not _autonomous_stop.is_set() and (time.time() - start) < duration:
            try:
                dist = px.ultrasonic.read()
                if dist < 0:
                    dist = 100
                
                if dist < 25:
                    # Obstacle detected
                    px.stop()
                    time.sleep(0.1)
                    px.backward(speed)
                    time.sleep(0.3)
                    px.stop()
                    
                    # Look left and right
                    px.set_cam_pan_angle(60)
                    time.sleep(0.3)
                    left_dist = px.ultrasonic.read()
                    
                    px.set_cam_pan_angle(-60)
                    time.sleep(0.3)
                    right_dist = px.ultrasonic.read()
                    
                    px.set_cam_pan_angle(0)
                    
                    log.append(f"Obstacle at {dist:.0f}cm, L={left_dist:.0f} R={right_dist:.0f}")
                    
                    if left_dist > right_dist:
                        px.set_dir_servo_angle(-30)
                        px.forward(speed)
                        time.sleep(0.5)
                    else:
                        px.set_dir_servo_angle(30)
                        px.forward(speed)
                        time.sleep(0.5)
                    
                    px.set_dir_servo_angle(0)
                else:
                    px.forward(speed)
                
                time.sleep(0.1)
            except Exception as e:
                log.append(f"Error: {e}")
                break
        
        px.stop()
        px.set_dir_servo_angle(0)
        _is_moving = False
        _current_speed = 0
        _current_direction = "stopped"
        _audit("obstacle_avoid_done", {"duration": round(time.time()-start, 1), "events": len(log)})
    
    _autonomous_mode = "obstacle_avoid"
    _autonomous_stop.clear()
    _autonomous_thread = threading.Thread(target=_obstacle_loop, daemon=True)
    _autonomous_thread.start()
    
    _audit("obstacle_avoid", {"duration": duration, "speed": speed})
    return jsonify({"status": "running", "mode": "obstacle_avoid", "duration": duration, "speed": speed})

@app.route("/line_track", methods=["POST"])
def line_track():
    global _autonomous_mode, _autonomous_thread
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    duration = min(float(data.get("duration", 30)), 120)
    speed = min(int(data.get("speed", 25)), MAX_SPEED)
    
    _stop_autonomous()
    
    def _line_loop():
        global _is_moving
        _is_moving = True
        start = time.time()
        
        while not _autonomous_stop.is_set() and (time.time() - start) < duration:
            try:
                gs = px.grayscale.read()
                if gs[0] < 500 and gs[1] > 500 and gs[2] > 500:
                    px.set_dir_servo_angle(-15)
                elif gs[0] > 500 and gs[1] > 500 and gs[2] < 500:
                    px.set_dir_servo_angle(15)
                elif gs[1] < 500:
                    px.set_dir_servo_angle(0)
                else:
                    px.set_dir_servo_angle(0)
                
                px.forward(speed)
                time.sleep(0.05)
            except:
                break
        
        px.stop()
        px.set_dir_servo_angle(0)
        _is_moving = False
        _audit("line_track_done", {"duration": round(time.time()-start, 1)})
    
    _autonomous_mode = "line_track"
    _autonomous_stop.clear()
    _autonomous_thread = threading.Thread(target=_line_loop, daemon=True)
    _autonomous_thread.start()
    
    _audit("line_track", {"duration": duration, "speed": speed})
    return jsonify({"status": "running", "mode": "line_track", "duration": duration, "speed": speed})

@app.route("/patrol", methods=["POST"])
def patrol():
    global _autonomous_mode, _autonomous_thread
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    duration = min(float(data.get("duration", 30)), 180)
    speed = min(int(data.get("speed", 25)), MAX_SPEED)
    pattern = data.get("pattern", "square")
    
    _stop_autonomous()
    
    def _patrol_loop():
        global _is_moving
        _is_moving = True
        start = time.time()
        
        steps = {
            "square": [
                ("forward", 0, 2), ("forward", 30, 1.5),
                ("forward", 0, 2), ("forward", 30, 1.5),
                ("forward", 0, 2), ("forward", 30, 1.5),
                ("forward", 0, 2), ("forward", 30, 1.5),
            ],
            "zigzag": [
                ("forward", -25, 1.5), ("forward", 25, 1.5),
                ("forward", -25, 1.5), ("forward", 25, 1.5),
            ],
            "circle": [
                ("forward", 20, 8),
            ],
        }
        
        route = steps.get(pattern, steps["square"])
        
        while not _autonomous_stop.is_set() and (time.time() - start) < duration:
            for direction, angle, step_dur in route:
                if _autonomous_stop.is_set():
                    break
                
                # Check obstacle
                try:
                    dist = px.ultrasonic.read()
                    if 0 < dist < 20:
                        px.stop()
                        px.backward(speed)
                        time.sleep(0.5)
                        px.stop()
                        continue
                except:
                    pass
                
                px.set_dir_servo_angle(angle)
                if direction == "forward":
                    px.forward(speed)
                else:
                    px.backward(speed)
                
                # Wait with early exit
                step_start = time.time()
                while (time.time() - step_start) < step_dur:
                    if _autonomous_stop.is_set():
                        break
                    time.sleep(0.1)
                
                px.stop()
                time.sleep(0.2)
        
        px.stop()
        px.set_dir_servo_angle(0)
        _is_moving = False
        _audit("patrol_done", {"duration": round(time.time()-start, 1), "pattern": pattern})
    
    _autonomous_mode = "patrol"
    _autonomous_stop.clear()
    _autonomous_thread = threading.Thread(target=_patrol_loop, daemon=True)
    _autonomous_thread.start()
    
    _audit("patrol", {"duration": duration, "speed": speed, "pattern": pattern})
    return jsonify({"status": "running", "mode": "patrol", "duration": duration, "pattern": pattern})

@app.route("/dance", methods=["POST"])
def dance():
    """Fun dance mode — servos and movement."""
    global _autonomous_mode, _autonomous_thread
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    _stop_autonomous()
    
    def _dance_loop():
        global _is_moving
        _is_moving = True
        moves = [
            lambda: (px.set_cam_pan_angle(-60), time.sleep(0.3)),
            lambda: (px.set_cam_pan_angle(60), time.sleep(0.3)),
            lambda: (px.set_cam_pan_angle(0), time.sleep(0.2)),
            lambda: (px.set_cam_tilt_angle(-30), time.sleep(0.3)),
            lambda: (px.set_cam_tilt_angle(50), time.sleep(0.3)),
            lambda: (px.set_cam_tilt_angle(0), time.sleep(0.2)),
            lambda: (px.set_dir_servo_angle(-30), px.forward(25), time.sleep(0.5), px.stop()),
            lambda: (px.set_dir_servo_angle(30), px.forward(25), time.sleep(0.5), px.stop()),
            lambda: (px.set_dir_servo_angle(0), px.forward(30), time.sleep(0.3), px.stop()),
            lambda: (px.set_dir_servo_angle(0), px.backward(30), time.sleep(0.3), px.stop()),
        ]
        
        for m in moves:
            if _autonomous_stop.is_set():
                break
            try:
                m()
            except:
                pass
        
        px.stop()
        px.set_dir_servo_angle(0)
        px.set_cam_pan_angle(0)
        px.set_cam_tilt_angle(0)
        _is_moving = False
        _audit("dance_done")
    
    _autonomous_mode = "dance"
    _autonomous_stop.clear()
    _autonomous_thread = threading.Thread(target=_dance_loop, daemon=True)
    _autonomous_thread.start()
    
    _audit("dance")
    return jsonify({"status": "dancing"})

# ── Calibration ──

@app.route("/calibrate", methods=["POST"])
def calibrate():
    """Set servo to zero position for calibration."""
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    servo = data.get("servo", "all")  # pan, tilt, steering, all
    angle = int(data.get("angle", 0))
    
    with _lock:
        if servo in ("pan", "all"):
            px.set_cam_pan_angle(angle)
        if servo in ("tilt", "all"):
            px.set_cam_tilt_angle(angle)
        if servo in ("steering", "all"):
            px.set_dir_servo_angle(angle)
    
    _audit("calibrate", {"servo": servo, "angle": angle})
    return jsonify({"status": "ok", "servo": servo, "angle": angle})

# ── Audit log ──

@app.route("/audit", methods=["GET"])
def audit():
    limit = int(request.args.get("limit", 20))
    return jsonify(_audit_log[-limit:])

# ── Kill / Emergency ──

@app.route("/kill", methods=["POST"])
def kill():
    global _is_moving, _current_speed, _current_direction
    _stop_autonomous()
    _cancel_auto_stop()
    with _lock:
        if px:
            px.stop()
            px.set_dir_servo_angle(0)
            px.set_cam_pan_angle(0)
            px.set_cam_tilt_angle(0)
    _is_moving = False
    _current_speed = 0
    _current_direction = "stopped"
    _audit("KILL", result="emergency stop")
    return jsonify({"status": "EMERGENCY STOP", "all_stopped": True})

# ── Servo test ──

@app.route("/servo_test", methods=["POST"])
def servo_test():
    """Test all servos with gentle movements."""
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    results = []
    with _lock:
        # Pan
        for a in [-45, 45, 0]:
            px.set_cam_pan_angle(a)
            time.sleep(0.4)
        results.append("pan: ok")
        
        # Tilt
        for a in [-20, 40, 0]:
            px.set_cam_tilt_angle(a)
            time.sleep(0.4)
        results.append("tilt: ok")
        
        # Steering
        for a in [-25, 25, 0]:
            px.set_dir_servo_angle(a)
            time.sleep(0.4)
        results.append("steering: ok")
    
    _audit("servo_test", result=str(results))
    return jsonify({"status": "ok", "results": results})

# ── Motor test ──

@app.route("/motor_test", methods=["POST"])
def motor_test():
    if not px:
        return jsonify({"error": "PiCar-X not initialized"}), 503
    
    data = request.get_json(force=True) or {}
    speed = min(int(data.get("speed", 25)), MAX_SPEED)
    
    with _lock:
        px.forward(speed)
        time.sleep(1)
        px.stop()
        time.sleep(0.3)
        px.backward(speed)
        time.sleep(1)
        px.stop()
    
    _audit("motor_test", {"speed": speed})
    return jsonify({"status": "ok", "speed_tested": speed})

# ══════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════

def _shutdown_handler(sig, frame):
    logger.info("Shutting down PiCar-X proxy...")
    _stop_autonomous()
    _cancel_auto_stop()
    if px:
        px.stop()
        px.set_dir_servo_angle(0)
        px.set_cam_pan_angle(0)
        px.set_cam_tilt_angle(0)
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT, _shutdown_handler)

if __name__ == "__main__":
    logger.info(f"Starting TokioAI PiCar-X Proxy on port {PORT}...")
    if _init_picar():
        logger.info("PiCar-X hardware ready")
    else:
        logger.warning("Running without hardware (API will return errors)")
    
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
