<div align="center">

# 🤖 Robotics Integration

**Control physical robots with natural language through TokioAI**

</div>

---

## Overview

TokioAI integrates with physical robots through safety proxies running on Raspberry Pi. Each robot has a dedicated proxy that enforces safety limits, rate limiting, and emergency stops while exposing a REST API for the AI agent.

## Supported Robots

### 🚗 PiCar-X v2.0 (Sunfounder)

| Spec | Detail |
|------|--------|
| **Platform** | Sunfounder PiCar-X v2.0 |
| **Computer** | Raspberry Pi 5 (4GB) |
| **Camera** | IMX219 (Pi Camera v2) |
| **Sensors** | Ultrasonic (HC-SR04), 3x Grayscale |
| **Motors** | 2x DC motors (differential drive) |
| **Servos** | 3x (steering, camera pan, camera tilt) |
| **Proxy port** | 5002 |
| **Documentation** | [docs.sunfounder.com](https://docs.sunfounder.com/projects/picar-x-v20/en/latest/) |

### Architecture

```
┌────────────────┐         ┌──────────────────────────┐
│  TokioAI Agent │   HTTP  │   Raspberry Pi 5         │
│  (GCP Cloud)   │────────→│   PiCar Safety Proxy     │
│  picar_tools   │  :5002  │   ├── Rate limiting      │
└────────────────┘         │   ├── Speed limits (0-80) │
        ↑                  │   ├── Geofence            │
        │                  │   ├── Emergency kill       │
┌───────┴────────┐         │   └── Audit log           │
│  CLI/Telegram  │         └─────────┬────────────────┘
│  "move forward"│                   │
└────────────────┘                   ↓
                           ┌─────────────────┐
                           │  PiCar-X Robot  │
                           │  Motors/Servos  │
                           │  Camera/Sensors │
                           └─────────────────┘
```

### Commands

| Command | Example | Description |
|---------|---------|-------------|
| `move` | "move forward 50cm" | Move in any direction |
| `stop` | "stop" | Stop all motors |
| `kill` | "emergency stop" | Immediate halt (safety) |
| `camera` | "look left" | Pan/tilt camera |
| `patrol` | "patrol square" | Autonomous patrol pattern |
| `dance` | "dance" | Fun movement sequence |
| `obstacle_avoid` | "avoid obstacles" | Autonomous obstacle avoidance |
| `line_track` | "follow the line" | Follow line on floor |
| `sensors` | "check sensors" | Read ultrasonic + grayscale |
| `snapshot` | "take a photo" | Capture from camera |
| `status` | "picar status" | Full robot status |

### Safety Proxy Features

| Feature | Detail |
|---------|--------|
| **Speed limit** | Max 80 (configurable) |
| **Duration limit** | Max 10s per command |
| **Emergency kill** | Instant motor stop, no questions |
| **Rate limiting** | Prevents command flooding |
| **Audit log** | Every command logged with timestamp |
| **Systemd service** | Auto-start on boot, auto-restart on crash |

### CLI Usage

```bash
# Quick status
tokio> /picar

# Natural language control
tokio> move the picar forward 30cm
tokio> turn left 90 degrees
tokio> start obstacle avoidance mode
tokio> take a photo with the picar
tokio> make it dance!
```

### Telegram Usage

```
"Move the robot forward"
"PiCar, avoid obstacles"
"Robot status"
"Take a photo with the robot"
```

---

### 🐕 DogBot / QuadBot (Sunfounder — Coming Soon)

Future integration planned for Sunfounder's quadruped robot using the same safety proxy architecture.

---

## 🚁 DJI Tello Drone

See [Drone Control](../README.md#-drone-control) in the main README for full drone documentation.

The drone uses the same safety proxy pattern:

| Feature | PiCar-X | Drone |
|---------|---------|-------|
| Proxy port | 5002 | 5001 |
| Raspberry Pi | Pi 5 (dedicated) | Pi 5 (shared with Entity) |
| Safety | Speed/duration limits | Geofence + altitude limits |
| Emergency | Motor kill | Forced landing |
| Vision | Camera snapshot | FPV + face tracking |

---

## Adding a New Robot

To integrate a new robot with TokioAI:

### 1. Create a Safety Proxy

```python
# my_robot_proxy.py
from flask import Flask, jsonify, request

app = Flask(__name__)

SPEED_LIMIT = 80
KILL_SWITCH = False

@app.route('/status')
def status():
    return jsonify({
        "robot": "MyRobot",
        "status": "ready",
        "battery": get_battery(),
        "safety_level": "demo"
    })

@app.route('/move', methods=['POST'])
def move():
    if KILL_SWITCH:
        return jsonify({"error": "Kill switch active"}), 403
    # ... validate and execute
```

### 2. Create Agent Tools

```python
# my_robot_tools.py
def register_tools(server):
    @server.tool("my_robot")
    async def my_robot(action: str, params: dict):
        """Control MyRobot via safety proxy"""
        response = httpx.post(f"{PROXY_URL}/{action}", json=params)
        return response.json()
```

### 3. Deploy

```bash
# Copy proxy to robot's Raspberry Pi
scp my_robot_proxy.py pi@robot:/home/pi/

# Create systemd service
# Deploy tools to GCP agent
# Add to sitrep for monitoring
```

## Files

| File | Location | Description |
|------|----------|-------------|
| `picar_proxy.py` | `tokio_raspi/` | PiCar-X safety proxy (778 lines) |
| `picar_tools.py` | `tokio_agent/engine/tools/builtin/` | Agent tools for PiCar (277 lines) |
| `drone_safety_proxy.py` | `tokio_raspi/` | Drone safety proxy |
| `drone_secure_tools.py` | `tokio_agent/engine/tools/builtin/` | Agent tools for drone |

