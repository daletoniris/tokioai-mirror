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
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Flask, Response, jsonify, request

if TYPE_CHECKING:
    from .main import TokioRaspiApp


def create_api(app_instance: TokioRaspiApp) -> Flask:
    """Create Flask API bound to the running TokioRaspi app."""

    api = Flask(__name__)
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

    # --- Thoughts ---

    @api.route("/thoughts", methods=["GET"])
    def thoughts():
        import time
        now = time.monotonic()
        return jsonify([
            {"text": t, "age_s": round(now - ts, 1)}
            for t, ts, _ in raspi._thoughts[-20:]
        ])

    return api
