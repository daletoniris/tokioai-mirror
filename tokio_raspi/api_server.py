"""
TokioAI Raspi API — Flask server for remote control from GCP.

Endpoints:
    GET  /status          -- System status + detections
    GET  /snapshot         -- JPEG snapshot with AI overlay
    POST /emotion          -- Set Tokio's emotion
    POST /model            -- Switch AI model
    POST /look             -- Make Tokio look at coordinates
    GET  /detections       -- Current detections list
    POST /face/register    -- Register a face (name, role)
    GET  /face/list        -- List known faces
    DELETE /face/<id>      -- Delete a face
    GET  /thoughts         -- Tokio's recent thoughts
    GET  /drone/fpv/status  -- FPV stream status
    POST /drone/fpv/start   -- Start FPV stream
    POST /drone/fpv/stop    -- Stop FPV stream
    POST /drone/fpv/mode    -- Set FPV mode (follow/explore/hover/idle)
    GET  /drone/fpv/snapshot -- JPEG from drone camera
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, Response, jsonify, request

from .gesture_detector import Gesture

if TYPE_CHECKING:
    from .main import TokioRaspiApp


def create_api(app_instance: TokioRaspiApp) -> Flask:
    """Create Flask API bound to the running TokioRaspi app."""
    import os
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    api = Flask(__name__, static_folder=static_dir, static_url_path="/static")
    raspi = app_instance

    @api.route("/status", methods=["GET"])
    def status():
        result = {
            "system": "tokio-raspi",
            "active_model": raspi._active_model,
            "emotion": raspi.face.emotion.value,
            "persons_detected": raspi._person_count,
            "faces_known": raspi.face_db.count,
            "security_connected": raspi.security.connected,
        }
        if raspi.vision:
            result["vision"] = raspi.vision.get_status()
        return jsonify(result)

    @api.route("/snapshot", methods=["GET"])
    def snapshot():
        if not raspi.vision:
            return Response("No camera", status=503)
        jpg = raspi.vision.capture_snapshot()
        if not jpg:
            return Response("No frame", status=503)
        return Response(jpg, mimetype="image/jpeg")

    @api.route("/emotion", methods=["POST"])
    def set_emotion():
        from .tokio_face import Emotion
        data = request.get_json(force=True, silent=True) or {}
        emotion_name = data.get("emotion", "neutral").upper()
        message = data.get("message", "")
        try:
            emotion = Emotion[emotion_name]
            raspi.face.set_emotion(emotion, message)
            return jsonify({"ok": True, "emotion": emotion.value})
        except KeyError:
            valid = [e.value for e in Emotion]
            return jsonify({"error": f"Unknown emotion. Valid: {valid}"}), 400

    @api.route("/model", methods=["POST"])
    def switch_model():
        data = request.get_json(force=True, silent=True) or {}
        model = data.get("model", "detect")
        if model not in ("detect", "faces", "pose", "face_scrfd"):
            return jsonify({"error": "Unknown model"}), 400
        raspi._active_model = model
        if raspi.vision:
            raspi.vision.switch_model(model)
        return jsonify({"ok": True, "model": model})

    @api.route("/look", methods=["POST"])
    def look_at():
        data = request.get_json(force=True, silent=True) or {}
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        raspi.face.look_at(x, y)
        return jsonify({"ok": True})

    @api.route("/detections", methods=["GET"])
    def detections():
        if not raspi.vision:
            return jsonify([])
        dets = raspi.vision.get_detections()
        return jsonify([
            {"label": d.label, "confidence": round(d.confidence, 2),
             "bbox": [d.x1, d.y1, d.x2, d.y2],
             "center": list(d.center), "area": d.area}
            for d in dets
        ])

    # --- Face Recognition ---

    @api.route("/face/register", methods=["POST"])
    def face_register():
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "").strip()
        role = data.get("role", "visitor").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        if role not in ("admin", "friend", "visitor"):
            return jsonify({"error": "role must be: admin, friend, visitor"}), 400
        raspi.start_register(name, role)
        return jsonify({"ok": True, "message": f"Registration started for {name} ({role}). Show face to camera."})

    @api.route("/face/list", methods=["GET"])
    def face_list():
        faces = raspi.face_db.get_all_faces()
        return jsonify([
            {"id": f.face_id, "name": f.name, "role": f.role,
             "first_seen": f.first_seen, "last_seen": f.last_seen,
             "times_seen": f.times_seen}
            for f in faces
        ])

    @api.route("/face/<int:face_id>", methods=["DELETE"])
    def face_delete(face_id):
        raspi.face_db.delete_face(face_id)
        return jsonify({"ok": True, "deleted": face_id})

    # --- AI Brain / Thoughts ---

    @api.route("/thoughts", methods=["GET"])
    def thoughts():
        import time
        now = time.monotonic()
        return jsonify([
            {"text": t, "age_s": round(now - ts, 1), "emotion": e}
            for t, ts, e in raspi._thoughts[-20:]
        ])

    @api.route("/ai/status", methods=["GET"])
    def ai_status():
        brain = raspi.ai_brain
        stats = brain.get_stats()
        stats["last_response"] = brain.last_response
        stats["last_emotion"] = brain._last_emotion
        return jsonify(stats)

    @api.route("/visitors", methods=["GET"])
    def visitor_count():
        return jsonify({
            "count": raspi.ai_brain.visitor_count,
            "stats": raspi.ai_brain.get_stats(),
        })

    @api.route("/ai/lang", methods=["POST"])
    def ai_lang():
        data = request.get_json(force=True, silent=True) or {}
        lang = data.get("lang", "es")
        if lang not in ("es", "en"):
            return jsonify({"error": "lang must be 'es' or 'en'"}), 400
        raspi.ai_brain.set_lang(lang)
        return jsonify({"ok": True, "lang": lang})

    # --- AI Memory ---

    @api.route("/ai/memory", methods=["GET"])
    def ai_memory():
        return jsonify(raspi.ai_brain.get_memory_summary())

    @api.route("/ai/memory/teach", methods=["POST"])
    def ai_teach():
        """Teach Tokio something: {"key": "cartel", "value": "Niperia Lab"}"""
        data = request.get_json(force=True, silent=True) or {}
        key = data.get("key", "").strip()
        value = data.get("value", "").strip()
        if not key or not value:
            return jsonify({"error": "key and value required"}), 400
        raspi.ai_brain.add_observation(key, value)
        return jsonify({"ok": True, "key": key, "value": value})

    @api.route("/ai/memory/correct", methods=["POST"])
    def ai_correct():
        """Correct Tokio: {"correction": "No estoy haciendo cuernitos, no detectes gestos falsos"}"""
        data = request.get_json(force=True, silent=True) or {}
        correction = data.get("correction", "").strip()
        if not correction:
            return jsonify({"error": "correction required"}), 400
        raspi.ai_brain.add_correction(correction)
        return jsonify({"ok": True, "correction": correction})

    @api.route("/ai/memory/person", methods=["POST"])
    def ai_person():
        """Add/update person: {"name": "Daniel", "role": "admin", "notes": "Creator of TokioAI"}"""
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        role = data.get("role", "visitor")
        notes = data.get("notes", "")
        raspi.ai_brain.update_person(name, role, notes)
        return jsonify({"ok": True, "name": name})

    @api.route("/ai/memory/environment", methods=["POST"])
    def ai_environment():
        """Add environment fact: {"fact": "This is the Niperia Lab in Patagonia"}"""
        data = request.get_json(force=True, silent=True) or {}
        fact = data.get("fact", "").strip()
        if not fact:
            return jsonify({"error": "fact required"}), 400
        raspi.ai_brain.add_environment(fact)
        return jsonify({"ok": True, "fact": fact})

    @api.route("/ai/memory/forget", methods=["POST"])
    def ai_forget():
        """Remove an observation: {"key": "cartel_viejo"}"""
        data = request.get_json(force=True, silent=True) or {}
        key = data.get("key", "").strip()
        if not key:
            return jsonify({"error": "key required"}), 400
        raspi.ai_brain.remove_observation(key)
        return jsonify({"ok": True, "removed": key})

    # --- Say / Display message on screen ---

    @api.route("/say", methods=["POST"])
    def say_message():
        data = request.get_json(force=True, silent=True) or {}
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "text is required"}), 400
        color_name = data.get("color", "bright")
        color_map = {
            "bright": (0, 255, 255), "warn": (255, 180, 0),
            "danger": (255, 40, 60), "ok": (0, 255, 100),
            "accent": (120, 50, 255), "dim": (0, 80, 120),
        }
        color = color_map.get(color_name, (0, 255, 255))
        raspi._say(text, color)
        return jsonify({"ok": True, "text": text})

    # --- Snapshot as base64 (for Telegram embedding) ---

    @api.route("/snapshot/base64", methods=["GET"])
    def snapshot_b64():
        import base64
        if not raspi.vision:
            return jsonify({"error": "No camera"}), 503
        jpg = raspi.vision.capture_snapshot()
        if not jpg:
            return jsonify({"error": "No frame"}), 503
        b64 = base64.b64encode(jpg).decode("utf-8")
        return jsonify({"image": b64, "format": "jpeg"})

    # --- What does Tokio see right now (summary for agent) ---

    @api.route("/vision/summary", methods=["GET"])
    def vision_summary():
        import time
        now = time.monotonic()
        result = {
            "detections": [{"label": d.label, "confidence": round(d.confidence, 2)}
                           for d in raspi._last_detections] if raspi._last_detections else [],
            "persons": raspi._person_count,
            "face_recognized": raspi._info_face if now - raspi._info_face_time < 10 else None,
            "identities": [
                {"name": k.name if k else "unknown", "role": k.role if k else None,
                 "confidence": round(c, 2)}
                for _, k, c in raspi._current_identities
            ],
            "last_gesture": raspi._last_gesture.value if raspi._last_gesture != Gesture.NONE and
                            now - raspi._last_gesture_time < 10 else None,
            "ai_thought": raspi.ai_brain.last_response if raspi.ai_brain.available else None,
            "fps": raspi.vision.get_fps() if raspi.vision else 0,
        }
        return jsonify(result)

    # --- WiFi Defense ---

    @api.route("/wifi/status", methods=["GET"])
    def wifi_defense_status():
        if not raspi.wifi_defense:
            return jsonify({"available": False})
        stats = raspi.wifi_defense.get_stats()
        return jsonify({
            "available": True,
            "monitoring": stats.monitoring,
            "interface": stats.monitor_interface,
            "deauth_detected": stats.deauth_detected,
            "evil_twins": stats.evil_twins,
            "mitigations": stats.mitigations_applied,
            "counter_deauth": raspi.wifi_defense.counter_deauth,
            "recent_attacks": raspi.wifi_defense.get_attack_log(10),
        })

    @api.route("/wifi/attacks", methods=["GET"])
    def wifi_attacks():
        if not raspi.wifi_defense:
            return jsonify([])
        return jsonify(raspi.wifi_defense.get_attack_log(50))

    @api.route("/wifi/counter_deauth", methods=["POST"])
    def wifi_counter_deauth():
        if not raspi.wifi_defense:
            return jsonify({"error": "WiFi defense not available"}), 503
        data = request.get_json(force=True, silent=True) or {}
        enabled = data.get("enabled", False)
        raspi.wifi_defense.enable_counter_deauth(enabled)
        return jsonify({"ok": True, "counter_deauth": enabled})

    @api.route("/wifi/diag", methods=["GET"])
    def wifi_diagnostics():
        """Capture diagnostics — packet counts, rx_packets, operstate, etc."""
        if not raspi.wifi_defense:
            return jsonify({"available": False})
        try:
            return jsonify(raspi.wifi_defense.get_diagnostics())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api.route("/wifi/test_deauth", methods=["POST"])
    def wifi_test_deauth():
        """Send test deauth frames to verify detection pipeline."""
        if not raspi.wifi_defense:
            return jsonify({"error": "WiFi defense not available"}), 503
        try:
            from scapy.all import RadioTap, Dot11, Dot11Deauth, sendp
            iface = raspi.wifi_defense._monitor_mode_iface or "wlan1"
            test_mac = "AA:BB:CC:DD:EE:FF"
            dot11 = Dot11(addr1="FF:FF:FF:FF:FF:FF", addr2=test_mac, addr3=test_mac)
            pkt = RadioTap() / dot11 / Dot11Deauth(reason=7)
            sendp(pkt, iface=iface, count=5, inter=0.1, verbose=False)
            return jsonify({"ok": True, "sent": 5, "test_mac": test_mac, "iface": iface})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # --- Home Assistant ---

    @api.route("/ha/status", methods=["GET"])
    def ha_status():
        if not raspi.ha_feed.available:
            return jsonify({"available": False})
        try:
            return jsonify(raspi.ha_feed.get_summary())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api.route("/ha/media", methods=["GET"])
    def ha_media():
        if not raspi.ha_feed.available:
            return jsonify({"error": "HA not available"}), 503
        return jsonify(raspi.ha_feed.get_media())

    @api.route("/ha/sensors", methods=["GET"])
    def ha_sensors():
        if not raspi.ha_feed.available:
            return jsonify({"error": "HA not available"}), 503
        return jsonify(raspi.ha_feed.get_sensors())

    @api.route("/ha/action", methods=["POST"])
    def ha_action():
        """Execute a Home Assistant service call.

        JSON body: {"domain": "light", "service": "turn_off", "entity_id": "light.lab", "data": {}}
        """
        if not raspi.ha_feed.available:
            return jsonify({"ok": False, "error": "HA not available"}), 503
        try:
            body = request.get_json(force=True)
            domain = body.get("domain", "")
            service = body.get("service", "")
            entity_id = body.get("entity_id", "")
            extra_data = body.get("data", {})
            if not domain or not service or not entity_id:
                return jsonify({"ok": False, "error": "domain, service, entity_id required"}), 400
            result = raspi.ha_feed.call_service(domain, service, entity_id, extra_data)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- Health Monitor ---

    @api.route("/health/status", methods=["GET"])
    def health_status():
        if not raspi.health.available:
            return jsonify({"available": False})
        return jsonify(raspi.health.get_summary())

    @api.route("/health/report", methods=["GET"])
    def health_report():
        """Full health report with history, averages, and assessment."""
        import time as _time
        h = raspi.health
        if not h.available:
            return jsonify({"available": False})
        d = h.data
        now = _time.time()

        hr_1h = h._compute_stats("hr", 3600)
        hr_day = h._compute_stats("hr", 86400)
        bp_day = h._compute_stats("bp_sys", 86400)
        bp_dia_day = h._compute_stats("bp_dia", 86400)
        spo2_day = h._compute_stats("spo2", 86400)

        # Multi-day history (stats per day, last 7 days)
        import datetime
        daily_stats = {}
        for days_ago in range(7):
            day_start = now - (days_ago + 1) * 86400
            day_end = now - days_ago * 86400
            day_label = datetime.datetime.fromtimestamp(day_end).strftime("%Y-%m-%d")
            day_data = {}
            for key in ("hr", "bp_sys", "bp_dia", "spo2"):
                cutoff_start = day_start
                with h._log_lock:
                    readings = [e[key] for e in h._log
                                if e.get(key, 0) > 0 and cutoff_start < e["ts"] <= day_end]
                if readings:
                    day_data[key] = {
                        "min": min(readings), "max": max(readings),
                        "avg": round(sum(readings) / len(readings)),
                        "count": len(readings),
                    }
            if day_data:
                daily_stats[day_label] = day_data

        report = {
            "connected": d.connected,
            "watch": d.watch_name,
            "battery": d.battery,
            "current": {
                "heart_rate": d.heart_rate if d.heart_rate > 0 else None,
                "hr_age_s": round(now - d.last_hr_time) if d.last_hr_time else None,
                "blood_pressure": f"{d.blood_pressure_sys}/{d.blood_pressure_dia}" if d.blood_pressure_sys > 0 else None,
                "bp_age_s": round(now - d.last_bp_time) if d.last_bp_time else None,
                "spo2": d.spo2 if d.spo2 > 0 else None,
                "spo2_age_s": round(now - d.last_spo2_time) if d.last_spo2_time else None,
                "steps": d.steps if d.steps > 0 else None,
            },
            "history_1h": {
                "hr": hr_1h,
            },
            "history_today": {
                "hr": hr_day,
                "bp_sys": bp_day,
                "bp_dia": bp_dia_day,
                "spo2": spo2_day,
            },
            "history_daily": daily_stats,
            "total_readings": len(h._log),
            "days_with_data": len(daily_stats),
        }

        # Health assessment
        issues = []
        if d.heart_rate > 120:
            issues.append("HR muy alto")
        elif d.heart_rate > 100:
            issues.append("HR elevado")
        if d.blood_pressure_sys > 140:
            issues.append("Presion alta")
        if d.spo2 > 0 and d.spo2 < 95:
            issues.append("SpO2 bajo")
        if hr_day and hr_day["avg"] > 100:
            issues.append("HR promedio alto hoy")

        report["assessment"] = "OPTIMO" if not issues else " | ".join(issues)
        report["assessment_ok"] = len(issues) == 0
        report["context"] = h.get_health_context()

        return jsonify(report)

    @api.route("/health/history", methods=["GET"])
    def health_history():
        """Raw health log entries (last N)."""
        limit = request.args.get("limit", 50, type=int)
        limit = min(limit, 500)
        with raspi.health._log_lock:
            entries = list(raspi.health._log[-limit:])
        return jsonify(entries)

    # --- Coffee Machine ---

    @api.route("/coffee/status", methods=["GET"])
    def coffee_status():
        if not raspi.coffee.available:
            return jsonify({"available": False})
        return jsonify(raspi.coffee.get_status())

    @api.route("/coffee/brew", methods=["POST"])
    def coffee_brew():
        if not raspi.coffee.available:
            return jsonify({"error": "Coffee machine not available"}), 503
        data = request.get_json(force=True, silent=True) or {}
        drink = data.get("drink", "espresso")
        result = raspi.coffee.brew(drink)
        return jsonify(result), 200 if result.get("ok") else 400

    @api.route("/coffee/menu", methods=["GET"])
    def coffee_menu():
        if not raspi.coffee.available:
            return jsonify({"available": False})
        return jsonify(raspi.coffee.get_drinks_menu())

    # --- Drone Vision (visual servoing) ---

    @api.route("/drone/vision/reset", methods=["POST"])
    def drone_vision_reset():
        raspi.drone_vision.reset()
        return jsonify({"ok": True, "status": "Vision reset — ready for fresh registration"})

    @api.route("/drone/vision/register", methods=["POST"])
    def drone_vision_register():
        raspi.drone_vision.set_mode("register")
        return jsonify({"ok": True, "status": "Show drone to camera"})

    @api.route("/drone/vision/mode", methods=["POST"])
    def drone_vision_mode():
        data = request.get_json(force=True, silent=True) or {}
        mode = data.get("mode", "track")
        valid_modes = ["idle", "register", "track", "come_to_me", "hover", "dance", "patrol"]
        if mode not in valid_modes:
            return jsonify({"error": f"Invalid mode. Valid: {valid_modes}"}), 400
        raspi.drone_vision.set_mode(mode)
        return jsonify({"ok": True, "mode": mode})

    @api.route("/drone/vision/status", methods=["GET"])
    def drone_vision_status():
        return jsonify(raspi.drone_vision.get_status())

    # --- Drone FPV (Tello camera) ---

    @api.route("/drone/fpv/status", methods=["GET"])
    def fpv_status():
        state = raspi.drone_fpv.get_state()
        return jsonify({
            "streaming": state.streaming,
            "mode": state.mode,
            "fps": round(state.fps, 1),
            "frame_count": state.frame_count,
            "persons": len(state.persons),
            "target": {
                "distance_cm": round(state.target.distance_cm, 0),
                "frame_pct": round(state.target.frame_pct, 2),
            } if state.target else None,
            "obstacle_ahead": state.obstacle_ahead,
            "closest_obstacle_cm": round(state.closest_obstacle_cm, 0),
        })

    @api.route("/drone/fpv/start", methods=["POST"])
    def fpv_start():
        raspi._start_fpv_stream()
        return jsonify({"ok": True, "status": "FPV stream started"})

    @api.route("/drone/fpv/stop", methods=["POST"])
    def fpv_stop():
        raspi._stop_fpv_stream()
        return jsonify({"ok": True, "status": "FPV stream stopped"})

    @api.route("/drone/fpv/mode", methods=["POST"])
    def fpv_mode():
        data = request.get_json(force=True, silent=True) or {}
        mode = data.get("mode", "follow")
        valid = ["follow", "orbit", "explore", "hover", "idle"]
        if mode not in valid:
            return jsonify({"error": f"Invalid mode. Valid: {valid}"}), 400
        raspi.drone_fpv.set_mode(mode)
        return jsonify({"ok": True, "mode": mode})

    @api.route("/drone/fpv/snapshot", methods=["GET"])
    def fpv_snapshot():
        import cv2 as _cv2
        frame = raspi.drone_fpv.get_frame()
        if frame is None:
            return Response("No FPV frame", status=503)
        _, jpg = _cv2.imencode(".jpg", frame, [_cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(jpg.tobytes(), mimetype="image/jpeg")

    # --- Dashboard ---

    @api.route("/dashboard", methods=["GET"])
    def dashboard():
        try:
            return api.send_static_file("dashboard.html")
        except Exception:
            return jsonify({"status": "ok", "modules": ["vision", "health", "drone", "wifi_defense", "ble_security", "mavlink"]})
    
    # ── Health Database (persistent SQLite) ──────────────

    @api.route("/health/db/report", methods=["GET"])
    def health_db_report():
        """Full health report from SQLite DB — all metrics, trends, assessments."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        report = raspi.health.health_db.full_report()
        return jsonify(report)

    @api.route("/health/db/stats", methods=["GET"])
    def health_db_stats():
        """Database statistics — total readings, metrics, size."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        return jsonify(raspi.health.health_db.db_stats())

    @api.route("/health/db/query", methods=["GET"])
    def health_db_query():
        """Query specific metric. Params: metric, hours (default 24), limit (default 100)."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        metric = request.args.get("metric", "heart_rate")
        hours = request.args.get("hours", 24, type=int)
        limit = request.args.get("limit", 100, type=int)
        readings = raspi.health.health_db.get_range(metric, hours=hours, limit=limit)
        stats = raspi.health.health_db.get_stats(metric, hours=hours)
        return jsonify({"metric": metric, "hours": hours, "stats": stats, "readings": readings})

    @api.route("/health/db/latest", methods=["GET"])
    def health_db_latest():
        """Get latest value for all metrics."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        from health_db import METRICS
        result = {}
        for m in METRICS:
            latest = raspi.health.health_db.get_latest(m)
            if latest:
                result[m] = {"value": latest["value"], "unit": latest.get("unit", ""),
                             "time": latest["datetime"]}
        return jsonify(result)

    @api.route("/health/db/daily", methods=["GET"])
    def health_db_daily():
        """Daily summaries for last N days (default 7)."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        days = request.args.get("days", 7, type=int)
        # Update today summary first
        raspi.health.health_db.update_daily_summary()
        return jsonify(raspi.health.health_db.get_daily_summaries(days=days))

    @api.route("/health/db/import", methods=["POST"])
    def health_db_import():
        """Import legacy health_log.json into SQLite DB."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        import os
        json_path = os.path.expanduser("~/.tokio_health/health_log.json")
        count = raspi.health.health_db.import_legacy_json(json_path)
        return jsonify({"ok": True, "imported": count})

    @api.route("/health/db/store", methods=["POST"])
    def health_db_store():
        """Manually store a health reading. Body: {metric, value, unit, notes}."""
        if not hasattr(raspi, 'health') or not raspi.health.health_db:
            return jsonify({"error": "Health DB not available"}), 503
        data = request.get_json(force=True, silent=True) or {}
        metric = data.get("metric")
        value = data.get("value")
        if not metric or value is None:
            return jsonify({"error": "metric and value required"}), 400
        unit = data.get("unit", "")
        notes = data.get("notes")
        source = data.get("source", "manual")
        raspi.health.health_db.store(metric, float(value), unit, source=source, notes=notes)
        return jsonify({"ok": True, "metric": metric, "value": value})

    # return api.send_static_file("dashboard.html")  # FIXED: was outside route

    # --- Demo Flight ---

    @api.route("/drone/demo", methods=["POST"])
    def drone_demo():
        data = request.get_json(force=True, silent=True) or {}
        pattern = data.get("pattern", "demo")
        speed = int(data.get("speed", 2))
        try:
            from .demo_flight import DemoFlight
            if not hasattr(raspi, '_demo_flight'):
                raspi._demo_flight = DemoFlight()
            df = raspi._demo_flight
            if df.is_running:
                return jsonify({"error": "Demo flight already running", "pattern": df.current_pattern}), 409
            df.execute(pattern, speed)
            return jsonify({"ok": True, "pattern": pattern, "speed": speed})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @api.route("/drone/demo/status", methods=["GET"])
    def drone_demo_status():
        try:
            from .demo_flight import DemoFlight
            if not hasattr(raspi, '_demo_flight'):
                return jsonify({"running": False})
            return jsonify(raspi._demo_flight.get_status())
        except Exception:
            return jsonify({"running": False})

    @api.route("/drone/demo/cancel", methods=["POST"])
    def drone_demo_cancel():
        try:
            if hasattr(raspi, '_demo_flight'):
                raspi._demo_flight.cancel()
                return jsonify({"ok": True, "status": "Demo flight cancelled"})
            return jsonify({"ok": True, "status": "No demo running"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Thought Log (persistent memory) ──────────────────────────

    @api.route("/thoughts/log", methods=["GET"])
    def thoughts_log():
        """Get recent thoughts. ?limit=20&source=ai_brain&q=search"""
        limit = request.args.get("limit", 20, type=int)
        source = request.args.get("source", "")
        query = request.args.get("q", "")

        if query:
            entries = raspi.thought_log.search(query, limit=limit)
        elif source:
            entries = raspi.thought_log.get_by_source(source, limit=limit)
        else:
            entries = raspi.thought_log.get_recent(limit=limit)
        return jsonify(entries)

    @api.route("/thoughts/summary", methods=["GET"])
    def thoughts_summary():
        return jsonify(raspi.thought_log.get_summary())

    # ── Vision Filter (Claude teaches Hailo) ─────────────────────

    @api.route("/vision/filter/status", methods=["GET"])
    def vision_filter_status():
        return jsonify(raspi.vision_filter.get_status())

    @api.route("/vision/filter/correct", methods=["POST"])
    def vision_filter_correct():
        """Manual correction: {"label": "knife", "real": "remote", "reason": "..."}"""
        data = request.json or {}
        label = data.get("label", "")
        if not label:
            return jsonify({"error": "label required"}), 400
        raspi.vision_filter.add_correction(
            label=label,
            correct_label=data.get("real", "unknown"),
            reason=data.get("reason", "manual correction"),
            confidence=float(data.get("confidence", 0.5)),
        )
        return jsonify({"ok": True, "status": f"Correction added for {label}"})

    @api.route("/vision/filter/reset", methods=["POST"])
    def vision_filter_reset():
        """Reset corrections: {"label": "knife"} for one class, or {} for all."""
        data = request.json or {}
        label = data.get("label", "")
        if label:
            raspi.vision_filter.reset_class(label)
            return jsonify({"ok": True, "status": f"Reset corrections for {label}"})
        raspi.vision_filter.reset_all()
        return jsonify({"ok": True, "status": "All corrections reset"})

    # ── Core Event (receive push from GCP brain) ──────────────────

    @api.route("/core/event", methods=["POST"])
    def core_event():
        """Receive events pushed from the GCP core.

        Body: {"type": "...", "message": "...", "emotion": "...", "color": "..."}
        Types: security, self_heal, info, alert, telegram
        """
        from .tokio_face import Emotion
        data = request.get_json(force=True, silent=True) or {}
        event_type = data.get("type", "info")
        message = data.get("message", "")
        emotion_name = data.get("emotion", "neutral").upper()
        color_name = data.get("color", "bright")

        color_map = {
            "bright": (0, 255, 255), "warn": (255, 180, 0),
            "danger": (255, 40, 60), "ok": (0, 255, 100),
            "accent": (120, 50, 255), "dim": (0, 80, 120),
        }
        color = color_map.get(color_name, (0, 255, 255))

        if message:
            raspi._say(message, color, source=f"core:{event_type}")

        try:
            emotion = Emotion[emotion_name]
            raspi.face.set_emotion(emotion, event_type)
        except KeyError:
            pass

        # Track Telegram activity for live panel
        if event_type == "telegram":
            import time as _time
            with raspi._telegram_lock:
                raspi._telegram_activity.append({
                    "user": data.get("user", "?"),
                    "message": message[:50],
                    "time": _time.time(),
                    "emotion": data.get("emotion", "neutral").lower(),
                })
                # Keep last 10 entries
                raspi._telegram_activity = raspi._telegram_activity[-10:]

        return jsonify({"ok": True, "type": event_type})

    # ── Security Dashboard (attacks blocked counter) ──────────────

    @api.route("/security/dashboard", methods=["GET"])
    def security_dashboard():
        """Live security dashboard data for Entity display."""
        result = {
            "wifi": {},
            "ble": {},
            "waf": {},
            "blocked_today": 0,
            "recent_attacks": [],
        }

        # WiFi defense stats
        if raspi.wifi_defense:
            stats = raspi.wifi_defense.get_stats()
            result["wifi"] = {
                "monitoring": stats.monitoring,
                "deauth_detected": stats.deauth_detected,
                "evil_twins": stats.evil_twins,
                "mitigations": stats.mitigations_applied,
            }
            result["blocked_today"] += stats.mitigations_applied
            result["recent_attacks"] = raspi.wifi_defense.get_attack_log(10)

        # BLE security stats
        if hasattr(raspi, 'health') and raspi.health.available:
            ble_sec = getattr(raspi.health, '_security', None)
            if ble_sec:
                sec_status = ble_sec.get_status()
                result["ble"] = {
                    "spoofing_attempts": sec_status.get("spoofing_detected", 0),
                    "replay_attempts": sec_status.get("replay_detected", 0),
                    "flooding_attempts": sec_status.get("flooding_detected", 0),
                }
                result["blocked_today"] += (
                    sec_status.get("spoofing_detected", 0) +
                    sec_status.get("replay_detected", 0) +
                    sec_status.get("flooding_detected", 0)
                )

        # WAF (SecurityFeed) stats
        if hasattr(raspi, "security") and raspi.security.connected:
            waf_stats = raspi.security.get_stats()
            waf_events = raspi.security.get_events(limit=10)
            result["waf"] = {
                "connected": True,
                "total_attacks": waf_stats.total_attacks,
                "blocked": waf_stats.blocked,
                "unique_ips": waf_stats.unique_ips,
                "critical": waf_stats.critical,
                "high": waf_stats.high,
                "medium": waf_stats.medium,
                "active_blocks": waf_stats.active_blocks,
            }
            result["blocked_today"] += waf_stats.blocked
            # Merge WAF attacks into recent_attacks
            for ev in waf_events[-5:]:
                result["recent_attacks"].append({
                    "timestamp": ev.timestamp,
                    "source": "WAF",
                    "ip": ev.ip,
                    "method": ev.method,
                    "uri": ev.uri,
                    "severity": ev.severity,
                    "blocked": ev.blocked,
                    "threat_type": ev.threat_type or "unknown",
                })

        return jsonify(result)


    # ── BLE Security Monitor endpoints ──────────────

    @api.route("/ble/security", methods=["GET"])
    def ble_security_status():
        """BLE attack detection status."""
        if hasattr(raspi, "ble_security"):
            return jsonify(raspi.ble_security.get_status())
        return jsonify({"error": "BLE security monitor not available"}), 503

    @api.route("/ble/devices", methods=["GET"])
    def ble_devices():
        """List detected BLE devices."""
        if hasattr(raspi, "ble_security"):
            return jsonify(raspi.ble_security.get_devices())
        return jsonify([])

    # ── WPA2 Monitor endpoints ──────────────

    @api.route("/wpa2/status", methods=["GET"])
    def wpa2_status():
        """WPA2 attack detection status (handshake capture, PMKID, KRACK)."""
        if hasattr(raspi, "wpa2_monitor"):
            return jsonify(raspi.wpa2_monitor.get_status())
        return jsonify({"error": "WPA2 monitor not available"}), 503

    # ── WiFi Full Defense Report ──────────────

    @api.route("/wifi/defense/full", methods=["GET"])
    def wifi_defense_full():
        """Complete WiFi + WPA2 defense report."""
        result = {"wifi": {}, "wpa2": {}, "combined_attacks": 0}
        if raspi.wifi_defense:
            stats = raspi.wifi_defense.get_stats()
            result["wifi"] = {
                "monitoring": stats.monitoring,
                "deauth_detected": stats.deauth_detected,
                "evil_twins": stats.evil_twins,
                "beacon_floods": stats.beacon_floods,
                "mitigations": stats.mitigations_applied,
                "recent": raspi.wifi_defense.get_attack_log(10),
            }
            result["combined_attacks"] += stats.deauth_detected + stats.evil_twins
        if hasattr(raspi, "wpa2_monitor"):
            wpa2 = raspi.wpa2_monitor.get_status()
            result["wpa2"] = wpa2
            result["combined_attacks"] += wpa2.get("handshake_captures_detected", 0)
            result["combined_attacks"] += wpa2.get("pmkid_attempts", 0)
        return jsonify(result)

    # ── MAVLink Drone endpoints ──────────────

    @api.route("/mavlink/status", methods=["GET"])
    def mavlink_status():
        """MAVLink drone status."""
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.get_status())
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/connect", methods=["POST"])
    def mavlink_connect():
        """Connect to MAVLink drone."""
        data = request.get_json(force=True, silent=True) or {}
        conn_str = data.get("connection", "")
        simulator = data.get("simulator", False)
        if hasattr(raspi, "mavlink_drone"):
            raspi.mavlink_drone._conn_str = conn_str
            raspi.mavlink_drone._use_sim = simulator
            ok = raspi.mavlink_drone.connect()
            return jsonify({"ok": ok})
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/takeoff", methods=["POST"])
    def mavlink_takeoff():
        data = request.get_json(force=True, silent=True) or {}
        alt = float(data.get("altitude", 5.0))
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.takeoff(alt))
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/land", methods=["POST"])
    def mavlink_land():
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.land())
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/rtl", methods=["POST"])
    def mavlink_rtl():
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.rtl())
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/move", methods=["POST"])
    def mavlink_move():
        data = request.get_json(force=True, silent=True) or {}
        direction = data.get("direction", "forward")
        distance = float(data.get("distance", 2.0))
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.move(direction, distance))
        return jsonify({"error": "MAVLink not available"}), 503

    @api.route("/mavlink/emergency", methods=["POST"])
    def mavlink_emergency():
        if hasattr(raspi, "mavlink_drone"):
            return jsonify(raspi.mavlink_drone.emergency_stop())
        return jsonify({"error": "MAVLink not available"}), 503

    # ── Stand Mode (autonomous exhibit operation) ──────────────

    @api.route("/mode", methods=["GET"])
    def get_mode():
        """Get current display mode."""
        stand_active = hasattr(raspi, '_stand_engine') and raspi._stand_engine and raspi._stand_engine.active
        return jsonify({
            "mode": "stand" if stand_active else "normal",
            "stand_active": stand_active,
        })

    @api.route("/mode/stand", methods=["POST"])
    def set_stand_mode():
        """Toggle stand mode — autonomous exhibit operation.
        
        When ON: Tokio greets visitors, shows stats, reports to Telegram.
        When OFF: Normal private mode.
        """
        data = request.get_json(force=True, silent=True) or {}
        enabled = data.get("enabled", True)
        lang = data.get("lang", "es")

        if not hasattr(raspi, '_stand_engine') or raspi._stand_engine is None:
            try:
                from .stand_mode import StandMode
                raspi._stand_engine = StandMode(raspi)
            except ImportError:
                return jsonify({"error": "StandMode module not available"}), 500

        if enabled:
            raspi._stand_engine.start(lang=lang)
            raspi._say("🎪 Stand mode ON. Autonomous operation.", (0, 255, 100))
        else:
            raspi._stand_engine.stop()
            raspi._say("Stand mode OFF. Normal operation.", (0, 255, 255))
        
        return jsonify({"ok": True, "mode": "stand" if enabled else "normal"})

    @api.route("/mode/stand/stats", methods=["GET"])
    def stand_stats():
        """Get stand mode visitor statistics."""
        if not hasattr(raspi, '_stand_engine') or not raspi._stand_engine:
            return jsonify({"active": False, "error": "Stand mode not initialized"})
        return jsonify(raspi._stand_engine.stats)

    # ── Extended Health (future sensors) ──────────────


    # ── Event Store (persistent attack/threat history) ──────────────

    @api.route("/events", methods=["GET"])
    def get_events():
        """Query stored events. Params: type, source, hours, limit."""
        if not hasattr(raspi, 'event_store') or not raspi.event_store:
            return jsonify({"error": "Event store not available"}), 503
        event_type = request.args.get("type")
        source = request.args.get("source")
        hours = float(request.args.get("hours", 24))
        limit = int(request.args.get("limit", 100))
        events = raspi.event_store.get_events(event_type, source, hours, limit)
        return jsonify(events)

    @api.route("/events/stats", methods=["GET"])
    def event_stats():
        """Get event statistics."""
        if not hasattr(raspi, 'event_store') or not raspi.event_store:
            return jsonify({"error": "Event store not available"}), 503
        hours = float(request.args.get("hours", 24))
        return jsonify(raspi.event_store.get_stats(hours))

    @api.route("/events/threats", methods=["GET"])
    def threat_history():
        """Get DEFCON threat level history."""
        if not hasattr(raspi, 'event_store') or not raspi.event_store:
            return jsonify({"error": "Event store not available"}), 503
        hours = float(request.args.get("hours", 24))
        return jsonify(raspi.event_store.get_threat_history(hours))

    @api.route("/events/cleanup", methods=["POST"])
    def cleanup_events():
        """Clean up old events. Param: days (default 30)."""
        if not hasattr(raspi, 'event_store') or not raspi.event_store:
            return jsonify({"error": "Event store not available"}), 503
        days = int(request.args.get("days", 30))
        raspi.event_store.cleanup(days)
        return jsonify({"ok": True, "cleaned_before_days": days})

    @api.route("/health/extended", methods=["GET"])
    def health_extended():
        """Extended health metrics including future sensor placeholders."""
        result = {"current": {}, "future_sensors": {}}
        if hasattr(raspi, "health") and raspi.health.available:
            data = raspi.health.data
            result["current"] = {
                "heart_rate": data.heart_rate,
                "blood_pressure_sys": data.blood_pressure_sys,
                "blood_pressure_dia": data.blood_pressure_dia,
                "spo2": data.spo2,
                "steps": data.steps,
                "battery": data.battery,
                "connected": data.connected,
            }
        result["future_sensors"] = {
            "cholesterol": {"status": "planned", "unit": "mg/dL", "value": None},
            "blood_sugar": {"status": "planned", "unit": "mg/dL", "value": None},
            "uric_acid": {"status": "planned", "unit": "mg/dL", "value": None},
            "hemoglobin": {"status": "planned", "unit": "g/dL", "value": None},
            "body_temperature": {"status": "planned", "unit": "C", "value": None},
        }
        return jsonify(result)


    # ── Threat Correlation Engine ──────────────

    @api.route("/threat/status", methods=["GET"])
    def threat_status():
        """Get unified threat assessment."""
        if hasattr(raspi, "threat_engine") and raspi.threat_engine:
            return jsonify(raspi.threat_engine.get_state())
        return jsonify({"level": 5, "level_name": "PEACE", "overall_score": 0,
                        "vectors": {}, "insights": [], "auto_actions": []})

    @api.route("/threat/push", methods=["POST"])
    def threat_push_event():
        """Push a security event into the correlation engine."""
        if not hasattr(raspi, "threat_engine") or not raspi.threat_engine:
            return jsonify({"error": "Threat engine not available"}), 503
        data = request.get_json(force=True, silent=True) or {}
        raspi.threat_engine.push_event(
            data.get("vector", "waf"), data.get("severity", "medium"),
            data.get("detail", ""), data.get("blocked", False))
        return jsonify({"ok": True})

    @api.route("/defense/status", methods=["GET"])
    def defense_status():
        """Get adaptive defense status."""
        if hasattr(raspi, "adaptive_defense") and raspi.adaptive_defense:
            return jsonify(raspi.adaptive_defense.get_status())
        return jsonify({"current_level": 5, "total_actions": 0, "recent_actions": []})

    @api.route("/defense/log", methods=["GET"])
    def defense_log():
        limit = request.args.get("limit", 50, type=int)
        if hasattr(raspi, "adaptive_defense") and raspi.adaptive_defense:
            return jsonify(raspi.adaptive_defense.get_log(limit))
        return jsonify([])

    @api.route("/security/full", methods=["GET"])
    def security_full():
        """Complete security overview — threat + WAF + WiFi + BLE + defense."""
        import time as _time
        result = {"timestamp": _time.time()}
        if hasattr(raspi, "threat_engine") and raspi.threat_engine:
            result["threat"] = raspi.threat_engine.get_state()
        else:
            result["threat"] = {"level": 5, "level_name": "PEACE"}
        if raspi.security.connected:
            result["waf"] = {"connected": True}
        else:
            result["waf"] = {"connected": False}
        if raspi.wifi_defense:
            stats = raspi.wifi_defense.get_stats()
            result["wifi"] = {"monitoring": stats.monitoring,
                              "deauth_detected": stats.deauth_detected,
                              "evil_twins": stats.evil_twins,
                              "counter_deauth": raspi.wifi_defense.counter_deauth}
        if hasattr(raspi, "adaptive_defense") and raspi.adaptive_defense:
            result["defense"] = raspi.adaptive_defense.get_status()
        result["vision"] = {"persons": raspi._person_count}
        return jsonify(result)


    return api
