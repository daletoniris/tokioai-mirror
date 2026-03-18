"""
TokioAI Raspi Vision Tools — Control the Raspberry Pi vision system.

Allows TokioAI to see through the camera, control emotions on the
touch screen, switch AI models, and toggle guard mode.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

RASPI_API = os.getenv("RASPI_API_URL", "http://YOUR_RASPI_TAILSCALE_IP:5000")
TIMEOUT = 10.0


async def _get(path: str):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{RASPI_API}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def _post(path: str, data: dict = None):
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{RASPI_API}{path}", json=data or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def raspi_vision(args: dict) -> str:
    """Unified handler for Raspi vision actions."""
    action = args.get("action", "status")
    params = args.get("params", {})

    if action == "status":
        s = await _get("/status")
        if "error" in s:
            return f"Error: {s['error']}"
        v = s.get("vision", {})
        lines = [
            f"System: {s.get('system')}",
            f"Emotion: {s.get('emotion')}",
            f"Model: {s.get('active_model')}",
            f"Guard: {'ON' if s.get('guard_mode') else 'OFF'}",
            f"Camera: {'open' if v.get('camera_open') else 'CLOSED'}",
            f"Hailo AI: {'online' if v.get('hailo_available') else 'OFFLINE'}",
            f"FPS: {v.get('fps', 0):.1f}",
            f"Detections: {v.get('detections_count', 0)}",
        ]
        for d in v.get("detections", []):
            lines.append(f"  - {d['label']}: {d['confidence']:.0%}")
        return "\n".join(lines)

    elif action == "look":
        dets = await _get("/detections")
        status = await _get("/status")
        if isinstance(dets, dict) and "error" in dets:
            return f"Error: {dets['error']}"
        det_list = dets if isinstance(dets, list) else []
        v = status.get("vision", {}) if isinstance(status, dict) else {}
        if not det_list:
            return (f"Camera active (FPS: {v.get('fps', 0):.1f}) — "
                    "no objects detected in current frame.")
        labels = {}
        for d in det_list:
            labels[d["label"]] = labels.get(d["label"], 0) + 1
        lines = ["Objects detected:"]
        for lbl, cnt in sorted(labels.items(), key=lambda x: -x[1]):
            lines.append(f"  - {lbl}: {cnt}x")
        lines.append(f"Total: {len(det_list)} objects at {v.get('fps', 0):.1f} FPS")
        return "\n".join(lines)

    elif action == "emotion":
        emotion = params.get("emotion", "neutral")
        message = params.get("message", "")
        valid = ["neutral", "happy", "alert", "scanning", "angry",
                 "curious", "sleeping", "thinking", "excited"]
        if emotion.lower() not in valid:
            return f"Unknown emotion. Valid: {', '.join(valid)}"
        r = await _post("/emotion", {"emotion": emotion, "message": message})
        if "error" in r:
            return f"Error: {r['error']}"
        return f"Emotion set to: {emotion}" + (f" — {message}" if message else "")

    elif action == "model":
        model = params.get("model", "detect")
        valid = {"detect": "general objects (80 classes)",
                 "faces": "person + face detection",
                 "pose": "human pose estimation"}
        if model not in valid:
            return "Valid models:\n" + "\n".join(f"  - {k}: {v}" for k, v in valid.items())
        r = await _post("/model", {"model": model})
        if "error" in r:
            return f"Error: {r['error']}"
        return f"Model switched to: {model} ({valid[model]})"

    elif action == "guard":
        r = await _post("/guard")
        if "error" in r:
            return f"Error: {r['error']}"
        return f"Guard mode {'ACTIVATED' if r.get('guard_mode') else 'DEACTIVATED'}"

    elif action == "look_at":
        x = float(params.get("x", 0))
        y = float(params.get("y", 0))
        r = await _post("/look", {"x": x, "y": y})
        if "error" in r:
            return f"Error: {r['error']}"
        dirs = []
        if x < -0.3: dirs.append("left")
        elif x > 0.3: dirs.append("right")
        if y < -0.3: dirs.append("up")
        elif y > 0.3: dirs.append("down")
        return f"Tokio looking {' '.join(dirs) or 'center'}"

    elif action == "log":
        log = await _get("/log")
        if isinstance(log, dict) and "error" in log:
            return f"Error: {log['error']}"
        if not log:
            return "No recent events"
        return "\n".join(log[-20:])

    else:
        return (
            "Unknown action. Available actions:\n"
            "  - status: Vision system status\n"
            "  - look: See what camera detects right now\n"
            "  - emotion: Set Tokio's face emotion\n"
            "  - model: Switch AI model (detect/faces/pose)\n"
            "  - guard: Toggle guard mode\n"
            "  - look_at: Make Tokio's eyes look somewhere\n"
            "  - log: Recent detection events"
        )
