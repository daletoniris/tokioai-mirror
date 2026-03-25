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
            "total_readings": len(h._log),
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
        return api.send_static_file("dashboard.html")

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

        return jsonify(result)

    return api
