"""
TokioAI Raspi Vision & Health Tools — Control the Raspberry Pi entity.

Vision, AI brain, health monitor, Home Assistant, WiFi defense.
All requests go to the Raspi Entity API via Tailscale.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

RASPI_API = os.getenv("RASPI_API_URL", "")
TIMEOUT = 10.0


def _enrich_error(e: Exception, path: str) -> str:
    """Provide actionable error messages instead of raw exceptions."""
    err = str(e)
    if "Connection refused" in err or "connect" in err.lower():
        return (f"Raspberry Pi Entity no responde en {path}. "
                "La Raspi puede estar apagada o el servicio Entity caído. "
                "Intentar: self_heal(action='check')")
    if "timeout" in err.lower():
        return f"Timeout conectando a Entity ({path}). Red puede estar lenta."
    return err


async def _get(path: str):
    if not RASPI_API:
        return {"error": "RASPI_API_URL no configurada. Verificar variables de entorno del agente."}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{RASPI_API}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": _enrich_error(e, path)}


async def _post(path: str, data: dict = None):
    if not RASPI_API:
        return {"error": "RASPI_API_URL no configurada. Verificar variables de entorno del agente."}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{RASPI_API}{path}", json=data or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": _enrich_error(e, path)}


async def raspi_vision(action: str = "status", params: dict = None, **kwargs) -> str:
    """Unified handler for Raspi vision + health actions."""
    if params is None:
        params = {}

    if action == "status":
        s = await _get("/status")
        if "error" in s:
            return f"Error: {s['error']}"
        v = s.get("vision", {})
        lines = [
            f"System: {s.get('system')}",
            f"Emotion: {s.get('emotion')}",
            f"Model: {s.get('active_model')}",
            f"Camera: {'open' if v.get('camera_open') else 'CLOSED'}",
            f"Hailo AI: {'online' if v.get('hailo_available') else 'OFFLINE'}",
            f"FPS: {v.get('fps', 0):.1f}",
            f"Detections: {v.get('detections_count', 0)}",
            f"Persons: {s.get('persons_detected', 0)}",
            f"Known faces: {s.get('faces_known', 0)}",
            f"Security feed: {'connected' if s.get('security_connected') else 'disconnected'}",
        ]
        for d in v.get("detections", []):
            lines.append(f"  - {d['label']}: {d['confidence']:.0%}")
        return "\n".join(lines)

    elif action == "see":
        summary = await _get("/vision/summary")
        if isinstance(summary, dict) and "error" in summary:
            return f"Error: {summary['error']}"

        lines = [f"FPS: {summary.get('fps', 0):.1f}"]

        dets = summary.get("detections", [])
        if dets:
            labels = {}
            for d in dets:
                labels[d["label"]] = labels.get(d["label"], 0) + 1
            det_parts = [f"{lbl} x{cnt}" if cnt > 1 else lbl for lbl, cnt in labels.items()]
            lines.append(f"Detected: {', '.join(det_parts)}")
        else:
            lines.append("No objects detected")

        persons = summary.get("persons", 0)
        if persons:
            lines.append(f"Persons: {persons}")
        face = summary.get("face_recognized")
        if face:
            lines.append(f"Face recognized: {face}")
        for ident in summary.get("identities", []):
            name = ident.get("name", "?")
            role = ident.get("role", "?")
            lines.append(f"  Identity: {name} ({role}) conf={ident.get('confidence', 0):.0%}")

        gesture = summary.get("last_gesture")
        if gesture:
            lines.append(f"Gesture: {gesture}")

        thought = summary.get("ai_thought")
        if thought:
            lines.append(f"\nTokio's AI thought: \"{thought}\"")

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

    elif action == "snapshot":
        data = await _get("/snapshot/base64")
        if isinstance(data, dict) and "error" in data:
            return f"Error: {data['error']}"
        if isinstance(data, dict) and "image" in data:
            return f"data:image/jpeg;base64,{data['image']}"
        return "Error: unexpected response"

    elif action == "thoughts":
        limit = params.get("limit", 15)
        source = params.get("source", "")
        query_str = f"?limit={limit}"
        if source:
            query_str += f"&source={source}"
        thoughts = await _get(f"/thoughts/log{query_str}")
        if isinstance(thoughts, dict) and "error" in thoughts:
            return f"Error: {thoughts['error']}"
        if not thoughts:
            return "No thoughts yet — AI brain may still be warming up."
        lines = [f"Tokio's thoughts (last {len(thoughts)}):"]
        for t in thoughts:
            ts = t.get("time_str", "?")
            src = t.get("source", "?")
            emotion = t.get("emotion", "")
            text = t.get("text", "")
            lines.append(f"  [{ts}] ({src}/{emotion}) {text}")
        return "\n".join(lines)

    elif action == "thought_search":
        query = params.get("query", params.get("q", ""))
        if not query:
            return "Error: 'query' parameter required"
        limit = params.get("limit", 15)
        results = await _get(f"/thoughts/log?q={query}&limit={limit}")
        if isinstance(results, dict) and "error" in results:
            return f"Error: {results['error']}"
        if not results:
            return f"No thoughts matching '{query}'"
        lines = [f"Thoughts matching '{query}' ({len(results)} results):"]
        for t in results:
            ts = t.get("time_str", "?")
            src = t.get("source", "?")
            text = t.get("text", "")
            lines.append(f"  [{ts}] ({src}) {text}")
        return "\n".join(lines)

    elif action == "thought_summary":
        summary = await _get("/thoughts/summary")
        if isinstance(summary, dict) and "error" in summary:
            return f"Error: {summary['error']}"
        lines = [
            f"Total pensamientos: {summary.get('total', 0)}",
            f"Periodo: {summary.get('first_entry', '?')} — {summary.get('last_entry', '?')}",
            f"Horas: {summary.get('hours_span', 0)}h",
        ]
        sources = summary.get("sources", {})
        if sources:
            lines.append("\nPor fuente:")
            for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
                lines.append(f"  - {src}: {cnt}")
        emotions = summary.get("emotions", {})
        if emotions:
            lines.append("\nPor emocion:")
            for emo, cnt in sorted(emotions.items(), key=lambda x: -x[1]):
                lines.append(f"  - {emo}: {cnt}")
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

    elif action == "say":
        text = params.get("text", "")
        if not text:
            return "Error: 'text' parameter required"
        color = params.get("color", "bright")
        r = await _post("/say", {"text": text, "color": color})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Message displayed on screen: \"{text}\""

    elif action == "teach":
        name = params.get("name", "")
        role = params.get("role", "friend")
        if not name:
            return "Error: 'name' parameter required"
        r = await _post("/face/register", {"name": name, "role": role})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Face registration started for {name} ({role}). Person should look at camera."

    elif action == "faces":
        faces = await _get("/face/list")
        if isinstance(faces, dict) and "error" in faces:
            return f"Error: {faces['error']}"
        if not faces:
            return "No faces registered yet."
        lines = ["Known faces:"]
        for f in faces:
            lines.append(f"  - {f['name']} ({f['role']}) — seen {f['times_seen']}x")
        return "\n".join(lines)

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

    # ── Health Monitor ────────────────────────────────────────

    elif action == "health":
        r = await _get("/health/report")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        lines = []
        lines.append(f"Reloj: {r.get('watch', '?')} — {'conectado' if r.get('connected') else 'desconectado'}")
        if r.get("battery", -1) >= 0:
            lines.append(f"Bateria: {r['battery']}%")
        lines.append(f"Estado: {r.get('assessment', '?')}")
        lines.append("")

        cur = r.get("current", {})
        if cur.get("heart_rate"):
            age = cur.get("hr_age_s", 0)
            lines.append(f"HR actual: {cur['heart_rate']} bpm (hace {age}s)")
        if cur.get("blood_pressure"):
            age = cur.get("bp_age_s", 0)
            ago = f" (hace {age // 60}min)" if age and age > 60 else ""
            lines.append(f"Presion: {cur['blood_pressure']} mmHg{ago}")
        if cur.get("spo2"):
            age = cur.get("spo2_age_s", 0)
            ago = f" (hace {age // 60}min)" if age and age > 60 else ""
            lines.append(f"SpO2: {cur['spo2']}%{ago}")
        if cur.get("steps"):
            lines.append(f"Pasos: {cur['steps']}")

        lines.append("")
        h1 = r.get("history_1h", {})
        hr1 = h1.get("hr")
        if hr1:
            lines.append(f"HR ultima hora: avg={hr1['avg']} min={hr1['min']} max={hr1['max']} ({hr1['count']} lecturas)")

        hd = r.get("history_today", {})
        hrd = hd.get("hr")
        if hrd:
            lines.append(f"HR hoy: avg={hrd['avg']} min={hrd['min']} max={hrd['max']} ({hrd['count']} lecturas)")
        bps = hd.get("bp_sys")
        bpd = hd.get("bp_dia")
        if bps and bpd:
            lines.append(f"Presion hoy: avg={bps['avg']}/{bpd['avg']} min={bps['min']}/{bpd['min']} max={bps['max']}/{bpd['max']} ({bps['count']} med)")
        sp = hd.get("spo2")
        if sp:
            lines.append(f"SpO2 hoy: avg={sp['avg']}% min={sp['min']}% max={sp['max']}% ({sp['count']} med)")

        lines.append(f"\nTotal lecturas almacenadas: {r.get('total_readings', 0)}")
        return "\n".join(lines)

    elif action == "health_status":
        r = await _get("/health/status")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return "\n".join(f"{k}: {v}" for k, v in r.items())

    elif action == "health_store":
        # Store a manual health reading (e.g., from Accu-Answer iSaw device)
        # params: metric (glucose/cholesterol/hemoglobin/uric_acid), value, unit, notes
        metric = params.get("metric", "")
        value = params.get("value")
        unit = params.get("unit", "")
        notes = params.get("notes", "manual entry")

        # Map common names to DB metric names
        metric_map = {
            "glucose": ("blood_sugar", "mg/dL"),
            "blood_sugar": ("blood_sugar", "mg/dL"),
            "glucosa": ("blood_sugar", "mg/dL"),
            "cholesterol": ("cholesterol_total", "mg/dL"),
            "colesterol": ("cholesterol_total", "mg/dL"),
            "cholesterol_total": ("cholesterol_total", "mg/dL"),
            "hemoglobin": ("hemoglobin", "g/dL"),
            "hemoglobina": ("hemoglobin", "g/dL"),
            "uric_acid": ("uric_acid", "mg/dL"),
            "acido_urico": ("uric_acid", "mg/dL"),
        }

        if metric.lower() in metric_map:
            db_metric, default_unit = metric_map[metric.lower()]
        else:
            db_metric = metric
            default_unit = unit

        if not db_metric or value is None:
            return "Error: Se requieren 'metric' y 'value'. Métricas válidas: glucose, cholesterol, hemoglobin, uric_acid"

        r = await _post("/health/db/store", {
            "metric": db_metric,
            "value": float(value),
            "unit": unit or default_unit,
            "source": "manual_isaw",
            "notes": notes,
        })
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"

        # Normal ranges for assessment
        ranges = {
            "blood_sugar": (70, 100, "mg/dL", "Glucosa"),
            "cholesterol_total": (0, 200, "mg/dL", "Colesterol"),
            "hemoglobin": (13.5, 17.5, "g/dL", "Hemoglobina"),
            "uric_acid": (3.4, 7.0, "mg/dL", "Ácido Úrico"),
        }

        if db_metric in ranges:
            low, high, u, name = ranges[db_metric]
            val = float(value)
            if val < low:
                status = f"⚠️ BAJO (normal: {low}-{high} {u})"
            elif val > high:
                status = f"🔴 ALTO (normal: {low}-{high} {u})"
            else:
                status = f"✅ Normal ({low}-{high} {u})"
            return f"📋 {name}: {val} {unit or default_unit} — {status}\nRegistrado en historial de salud."
        return f"📋 {db_metric}: {value} {unit or default_unit} registrado."

    elif action == "health_full":
        # Full health report combining BLE watch + iSaw manual readings
        r = await _get("/health/db/report")
        if isinstance(r, dict) and "error" in r:
            # Fallback to basic report
            return await raspi_vision(action="health", params=params)

        lines = ["═══ INFORME COMPLETO DE SALUD ═══\n"]

        # Current vitals from watch
        watch = await _get("/health/report")
        if isinstance(watch, dict) and "error" not in watch:
            lines.append("── Signos Vitales (Smartwatch) ──")
            cur = watch.get("current", {})
            if cur.get("heart_rate"):
                lines.append(f"  ❤️  HR: {cur['heart_rate']} bpm")
            if cur.get("blood_pressure"):
                lines.append(f"  🩸 Presión: {cur['blood_pressure']} mmHg")
            if cur.get("spo2"):
                lines.append(f"  🫁 SpO2: {cur['spo2']}%")
            if cur.get("steps"):
                lines.append(f"  🚶 Pasos: {cur['steps']}")
            lines.append(f"  📊 Estado: {watch.get('assessment', '?')}")
            lines.append("")

        # Lab values from iSaw
        isaw_metrics = ["blood_sugar", "cholesterol_total", "hemoglobin", "uric_acid"]
        ranges = {
            "blood_sugar": (70, 100, "mg/dL", "Glucosa"),
            "cholesterol_total": (0, 200, "mg/dL", "Colesterol Total"),
            "hemoglobin": (13.5, 17.5, "g/dL", "Hemoglobina"),
            "uric_acid": (3.4, 7.0, "mg/dL", "Ácido Úrico"),
        }

        has_lab = False
        for metric in isaw_metrics:
            latest = await _get(f"/health/db/latest?metrics={metric}")
            if isinstance(latest, dict) and metric in latest:
                entry = latest[metric]
                if entry:
                    if not has_lab:
                        lines.append("── Laboratorio (Accu-Answer iSaw) ──")
                        has_lab = True
                    low, high, unit, name = ranges[metric]
                    val = entry.get("value", 0)
                    dt = entry.get("datetime", "?")
                    if val < low:
                        indicator = "⚠️ BAJO"
                    elif val > high:
                        indicator = "🔴 ALTO"
                    else:
                        indicator = "✅"
                    lines.append(f"  {indicator} {name}: {val} {unit} ({dt})")

        if not has_lab:
            lines.append("── Laboratorio (Accu-Answer iSaw) ──")
            lines.append("  Sin datos. Usá health_store para registrar mediciones.")

        # Daily stats from DB
        daily = r.get("daily_7d", []) if isinstance(r, dict) else []
        if daily:
            lines.append("\n── Promedios 7 días ──")
            for day in daily[-3:]:
                date = day.get("date", "?")
                metrics_str = []
                for m in day.get("metrics", {}):
                    val = day["metrics"][m]
                    if isinstance(val, dict):
                        metrics_str.append(f"{m}={val.get('avg', '?')}")
                if metrics_str:
                    lines.append(f"  {date}: {', '.join(metrics_str)}")

        lines.append(f"\n── Total lecturas: {r.get('total_readings', '?')} ──")
        return "\n".join(lines)

    # ── Home Assistant ────────────────────────────────────────

    elif action == "ha_status":
        r = await _get("/ha/status")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        if not r.get("available", True):
            return "Home Assistant no disponible"
        lines = ["Home Assistant:"]
        for k, v in r.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    # ── WiFi Defense ──────────────────────────────────────────

    elif action == "wifi":
        r = await _get("/wifi/status")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        if not r.get("available"):
            return "WiFi defense no disponible"
        lines = [
            f"Monitoring: {r.get('monitoring')}",
            f"Interface: {r.get('interface')}",
            f"Deauth detected: {r.get('deauth_detected', 0)}",
            f"Evil twins: {r.get('evil_twins', 0)}",
            f"Mitigations: {r.get('mitigations', 0)}",
            f"Counter-deauth: {'ON' if r.get('counter_deauth') else 'OFF'}",
        ]
        attacks = r.get("recent_attacks", [])
        if attacks:
            lines.append(f"\nUltimos ataques:")
            for a in attacks[-5:]:
                lines.append(f"  - {a}")
        return "\n".join(lines)

    # ── AI Brain ──────────────────────────────────────────────

    elif action == "ai_status":
        r = await _get("/ai/status")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return "\n".join(f"{k}: {v}" for k, v in r.items())

    elif action == "ai_memory":
        r = await _get("/ai/memory")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        lines = ["AI Memory:"]
        for k, v in r.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    elif action == "ai_correct":
        correction = params.get("correction", "")
        if not correction:
            return "Error: 'correction' parameter required (e.g. 'El cartel dice NIPERIA, no Nigeria')"
        r = await _post("/ai/memory/correct", {"correction": correction})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Correction added to AI Brain: {correction}"

    elif action == "ai_teach":
        key = params.get("key", "")
        value = params.get("value", "")
        if not key or not value:
            return "Error: 'key' and 'value' parameters required (e.g. key='cartel', value='Niperia Lab')"
        r = await _post("/ai/memory/teach", {"key": key, "value": value})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Taught AI Brain: {key} = {value}"

    elif action == "ai_forget":
        key = params.get("key", "")
        if not key:
            return "Error: 'key' parameter required (observation key to remove)"
        r = await _post("/ai/memory/forget", {"key": key})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"AI Brain forgot observation: {key}"

    elif action == "ai_person":
        name = params.get("name", "")
        if not name:
            return "Error: 'name' parameter required"
        r = await _post("/ai/memory/person", {
            "name": name,
            "role": params.get("role", "friend"),
            "notes": params.get("notes", ""),
        })
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Person updated in AI Brain: {name}"

    elif action == "ai_environment":
        fact = params.get("fact", "")
        if not fact:
            return "Error: 'fact' parameter required"
        r = await _post("/ai/memory/environment", {"fact": fact})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Environment fact added to AI Brain: {fact}"

    elif action == "vision_filter":
        r = await _get("/vision/filter/status")
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        lines = [
            f"Correcciones totales: {r.get('total_corrections', 0)}",
            f"Detecciones filtradas: {r.get('total_filtered', 0)}",
            f"Correcciones activas: {r.get('active_corrections', 0)}",
        ]
        thresholds = r.get("class_thresholds", {})
        if thresholds:
            lines.append("\nUmbrales aprendidos:")
            for cls, thresh in thresholds.items():
                fp_count = r.get("class_fp_counts", {}).get(cls, 0)
                lines.append(f"  - {cls}: {thresh:.0%} ({fp_count} FPs)")
        recent = r.get("recent_corrections", [])
        if recent:
            lines.append("\nCorrecciones recientes:")
            for c in recent[-5:]:
                lines.append(f"  - {c['label']} -> {c['correct_label']} "
                             f"({c['reason']}) x{c['times_seen']}")
        return "\n".join(lines)

    elif action == "vision_correct":
        label = params.get("label", "")
        if not label:
            return "Error: 'label' required (what Hailo detected wrong)"
        r = await _post("/vision/filter/correct", {
            "label": label,
            "real": params.get("real", "unknown"),
            "reason": params.get("reason", "manual correction"),
        })
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return f"Correccion agregada: {label} -> {params.get('real', 'unknown')}"

    elif action == "vision_reset":
        label = params.get("label", "")
        r = await _post("/vision/filter/reset", {"label": label} if label else {})
        if isinstance(r, dict) and "error" in r:
            return f"Error: {r['error']}"
        return r.get("status", "Reset OK")

    else:
        return (
            "Unknown action. Available:\n"
            "  Vision: status, see, look, snapshot, emotion, say, teach, faces, model, look_at\n"
            "  Thoughts: thoughts (recientes), thought_search (buscar), thought_summary (resumen)\n"
            "  Health: health (reporte completo), health_status (estado actual), "
            "health_store (registrar glucosa/colesterol/hemoglobina/acido_urico), "
            "health_full (informe completo combinando watch + iSaw)\n"
            "  Home: ha_status\n"
            "  WiFi: wifi (estado defensa WiFi)\n"
            "  AI: ai_status, ai_memory, ai_correct, ai_teach, ai_forget, ai_person, ai_environment\n"
            "  Filter: vision_filter (estado), vision_correct (corregir), vision_reset (resetear)"
        )
