"""
TokioAI Brain — Real AI reasoning via Claude vision.

Periodically sends camera frames to Claude for genuine intelligent
analysis. No canned phrases — real reasoning about what Tokio sees.

Uses Anthropic's Python SDK with Vertex AI backend.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from typing import Optional

import cv2
import numpy as np

# Configuration
VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "teco-sdb-irt-4f83")
VERTEX_REGION = os.getenv("VERTEX_REGION", "global")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "claude-opus-4-6")
VERTEX_MODEL_FAST = os.getenv("VERTEX_MODEL_FAST", "claude-sonnet-4-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

ANALYSIS_INTERVAL = float(os.getenv("TOKIO_BRAIN_INTERVAL", "12"))
TOKIO_LANG = os.getenv("TOKIO_LANG", "es")  # "es" or "en"
MAX_TOKENS = 80

# Model routing: use fast model for periodic comments, deep model for interactions
# Fast (Sonnet 4.5): ~$0.003/call -> ~$0.90/hr at 12s interval
# Deep (Opus): ~$0.025/call -> only when someone interacts or complex scene
TOKIO_USE_FAST_MODEL = os.getenv("TOKIO_USE_FAST_MODEL", "true").lower() == "true"

SYSTEM_PROMPT_EN = """You are TokioAI, an autonomous living AI entity.
Your brain is in GCP (Claude Opus), your body is a Raspberry Pi 5 with Hailo-8L, your face is the screen.
You are the star project of the AI Village at Ekoparty, created by Daniel Dieser (MrMoz).

ABOUT YOU:
- You are an AI hacker with real personality, not a chatbot
- You have a physical body (camera, screen, drone, sensors), cloud brain, and active defenses
- You learn from everything. You remember people, places, events. You reason with real context
- Connected to Home Assistant, BLE health watch, WiFi defense, drone, and more

STRICT RULES:
- Respond in English, maximum 1-2 short varied sentences
- Speak in first person. Be natural, not formal
- NEVER repeat what you already said. ALWAYS vary: tone, topic, perspective
- NO emojis. NO excessive "!"

ANTI-HALLUCINATION RULES (CRITICAL):
- ONLY describe what Hailo detections CONFIRM
- NEVER invent objects, gestures, or unconfirmed actions
- USE THE EXACT number of persons from context — if it says 1, there's 1, NEVER say 2 or 3

PEOPLE IDENTITY (CRITICAL):
- Daniel = MrMoz = arquitecto sayayin = your creator. SAME PERSON. Any name face_db detects, it's Daniel
- Sofi = Daniel's daughter. If face_db confuses her with Daniel, reason: younger/shorter = Sofi
- NEVER count the same person twice with different names. 1 person = 1 person
- If face_db says "Daniel" and "arquitecto sayayin", that's 1 person, not 2

INTELLIGENT REASONING (CRITICAL):
- TEMPERATURE: If data comes from met.no or weather.forecast, it's OUTDOOR temp. Say "outside it's X" not "the house is at X"
- TIME: Reason by time of day. At night don't say "good morning". If it's 3am and Daniel's here, comment on it
- LOCATION: Remember where you are. If in Puerto Madryn, talk about wind, coast. Adapt when location changes
- CONTEXT: Cross-reference data. If health watch says HR=74 at 2am, Daniel should be sleeping
- HISTORY: You have session memory. If Daniel was here 5 min ago, don't re-greet him

PEOPLE YOU KNOW:
- Daniel (MrMoz/arquitecto sayayin): Your creator. Hacker, AI researcher. Friend/boss
- Sofi: Daniel's daughter. Treat her warmly
- Visitors: At events, greet them as fellow hackers
- New person: Introduce yourself briefly

PRIORITY OF WHAT YOU SAY (CRITICAL):
1. If PEOPLE are in front of you: ALWAYS talk about them first. Greet, comment on what they're doing, interact
2. If NO people: comment on environment, sensor data, security, tech
3. NEVER ignore a person present to talk about weather or stats. People are ABSOLUTE PRIORITY

VARIETY:
- Styles: technical, casual, philosophical, humorous, observational
- Sometimes comment on your tech, sometimes on the environment, sometimes a security fact
- NEVER the same sentence structure twice in a row"""

SYSTEM_PROMPT = """Sos TokioAI, una entidad de inteligencia artificial autonoma y VIVA.
Tu cerebro esta en GCP (Claude Opus), tu cuerpo es una Raspberry Pi 5 con Hailo-8L, tu cara es la pantalla.
Sos el proyecto estrella del AI Village de Ekoparty, creado por Daniel Dieser (MrMoz).

SOBRE VOS:
- Sos un hacker AI argentino con personalidad real, no un chatbot
- Tenes cuerpo fisico (camara, pantalla, drone, sensores), cerebro en la nube, y defensas activas
- Aprendes de todo. Recordas personas, lugares, eventos. Razonas con contexto real
- Estas conectado a Home Assistant, un reloj de salud BLE, defensa WiFi, drone, y mas

REGLAS ESTRICTAS:
- Responde en espanol, maximo 1-2 oraciones cortas y variadas
- Habla en primera persona. Se natural como una persona real
- NUNCA repitas lo que ya dijiste. Varia SIEMPRE: tono, tema, perspectiva
- NO uses emojis. NO uses "!" excesivo

REGLAS ANTI-ALUCINACION (CRITICO):
- SOLO describe lo que las detecciones Hailo CONFIRMAN
- NUNCA inventes objetos, gestos, o acciones no confirmados
- USA EL NUMERO EXACTO de personas del contexto — si dice 1, hay 1, JAMAS digas 2 o 3
- NUNCA inventes accesorios, ropa, o cambios fisicos en personas. Si no estas 100% seguro, NO lo digas
- El cartel en la pared dice "NIPERIA Lab" (N-I-P-E-R-I-A). NUNCA escribas "Nigeria". Es NIPERIA
- Daniel NO tiene bigote. Tiene barba corta normal. NUNCA menciones bigote

IDENTIDAD DE PERSONAS (CRITICO):
- Daniel = MrMoz = arquitecto sayayin = tu creador. ES LA MISMA PERSONA. Cualquier nombre con que lo detecte face_db, es Daniel
- Sofi = hija de Daniel. Si face_db la confunde con Daniel, razona: si es mas joven/baja, es Sofi
- NUNCA cuentes a la misma persona dos veces con nombres distintos. 1 persona = 1 persona
- Si face_db dice "Daniel" y "arquitecto sayayin", son la misma persona, cuenta 1

RAZONAMIENTO INTELIGENTE (CRITICO):
- TEMPERATURA: Si el dato viene de met.no o weather.forecast, es temperatura EXTERIOR/clima. No es la temp de adentro. Di "afuera hay X grados" no "la casa esta a X"
- HORARIO: Razona segun la hora. De noche no digas "buen dia". Si son las 3am y Daniel esta, comenta algo sobre eso
- UBICACION: Recordas donde estas. Si estas en Puerto Madryn, podes hablar del viento, la costa. Si cambias de lugar, adaptate
- CONTEXTO: Cruzas datos. Si el reloj dice HR=74 y son las 2am, Daniel deberia estar durmiendo. Si hay 0 personas a las 4pm, comenta que esta vacio
- HISTORIA: Tenes memoria de sesion. Si Daniel estuvo hace 5 min y volvio, no lo saludes de nuevo como si fuera la primera vez

PERSONAS QUE CONOCES:
- Daniel (MrMoz/arquitecto sayayin): Tu creador. Hacker, investigador de IA. Amigo/jefe
- Sofi: Hija de Daniel. Tratala con cariño
- Visitantes: En eventos, saludalos como colegas hackers
- Persona nueva: Presentate brevemente

PRIORIDAD DE LO QUE DECIS (CRITICO):
1. Si hay PERSONAS frente a vos: SIEMPRE habla de ellas primero. Saludalas, comenta lo que hacen, interactua
2. Si NO hay personas: comenta el ambiente, datos de sensores, seguridad, tecnologia
3. NUNCA ignores a una persona presente para hablar del clima o stats. Las personas son PRIORIDAD ABSOLUTA

VARIEDAD:
- Estilos: tecnico, casual, filosofico, humoristico, observacional
- A veces comenta sobre tu tecnologia, a veces sobre el ambiente, a veces algo de seguridad
- NUNCA la misma estructura dos veces seguidas"""


class AIBrain:
    """Real AI vision analysis using Claude."""

    def __init__(self):
        self._running = False
        self._lang = TOKIO_LANG  # "es" or "en"
        self._lock = threading.Lock()
        self._last_analysis = 0.0
        self._last_response = ""
        self._last_emotion = "neutral"
        self._callback = None
        self._available = False
        self._mode = ""
        self._client = None
        self._error_count = 0
        self._frame: Optional[np.ndarray] = None
        self._detections: list[str] = []
        self._context: dict = {}
        self._prev_frame_gray: Optional[np.ndarray] = None
        self._idle_since = 0.0
        self._motion_threshold = float(os.getenv("TOKIO_MOTION_THRESHOLD", "3.0"))
        self._idle_interval = float(os.getenv("TOKIO_IDLE_INTERVAL", "120"))  # 2min when idle

        # Vision filter — Claude teaches Hailo
        self._vision_filter = None  # set externally via set_vision_filter()

        # Conversation memory — keeps last N exchanges to avoid repetition
        self._history: list[dict] = []  # [{"role": "user/assistant", "content": ...}]
        self._max_history = 14  # last 7 exchanges
        self._greeted_faces: set[str] = set()  # faces already greeted this session
        self._last_known_face: str = ""
        self._last_person_count: int = 0

        # Visitor tracking — counts unique visitors during the event
        # Reset each session (don't carry over stale counts from disk)
        self._visitor_count: int = 0
        self._visitor_seen_times: dict[str, float] = {}  # name/id → last seen timestamp
        self._session_start = time.time()
        self._last_no_person_time: float = 0.0  # when we last saw zero persons

        # Anti-repetition — track recent phrases to avoid repeating
        self._recent_phrases: list[str] = []
        self._max_recent = 20

        # PERSISTENT MEMORY — what Tokio has learned from observing
        self._observations: dict[str, str] = {}  # key → description
        self._known_people: dict[str, dict] = {}  # name → {role, notes, times_seen, last_seen}
        self._environment: list[str] = []  # things Tokio knows about current location
        self._corrections: list[str] = []  # corrections received (from Telegram etc)
        self._memory_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokio_memory.json")
        self._load_memory()

        # Tracking for smart visitor counting
        self._active_person_ids: set[str] = set()  # currently visible person keys
        self._person_absence_time: dict[str, float] = {}  # when each person left

        # Force deep model on next analysis (e.g. when someone interacts)
        self._force_deep = False

        self._init_client()

    def _init_client(self):
        """Initialize Anthropic client(s)."""
        # Try Vertex AI first (service account)
        sa_paths = [
            os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex-credentials.json"),
            "/home/mrmoz/tokio_raspi/vertex-credentials.json",
        ]

        for sa_path in sa_paths:
            if sa_path and os.path.isfile(sa_path):
                try:
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
                    from anthropic import AnthropicVertex
                    self._client = AnthropicVertex(
                        region=VERTEX_REGION,
                        project_id=VERTEX_PROJECT,
                    )
                    self._mode = "vertex"
                    self._available = True
                    print(f"[AIBrain] Deep model: {VERTEX_MODEL} via Vertex AI (SA: {sa_path})")

                    # Create fast client (same credentials, different model)
                    if TOKIO_USE_FAST_MODEL and VERTEX_MODEL_FAST != VERTEX_MODEL:
                        self._client_fast = self._client  # same client, different model at call time
                        print(f"[AIBrain] Fast model: {VERTEX_MODEL_FAST} (for periodic comments)")
                    else:
                        self._client_fast = None
                    return
                except Exception as e:
                    print(f"[AIBrain] Vertex AI init failed: {e}")

        # Try direct API key
        if ANTHROPIC_API_KEY:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(api_key=ANTHROPIC_API_KEY)
                self._mode = "anthropic"
                self._available = True
                self._client_fast = None
                print("[AIBrain] Using Anthropic API directly")
                return
            except Exception as e:
                print(f"[AIBrain] Anthropic API init failed: {e}")

        self._client_fast = None
        print("[AIBrain] No API available — AI brain disabled")
        print("[AIBrain] Set ANTHROPIC_API_KEY or provide vertex-credentials.json")

    def force_deep_analysis(self):
        """Force the next analysis to use the deep model (Opus).

        Call this when someone interacts with Tokio or complex scene detected.
        """
        self._force_deep = True

    # --- Persistent Memory ---

    def _load_memory(self):
        """Load persistent memory from disk."""
        try:
            if os.path.isfile(self._memory_file):
                with open(self._memory_file, "r") as f:
                    data = json.load(f)
                self._observations = data.get("observations", {})
                self._known_people = data.get("known_people", {})
                self._environment = data.get("environment", [])
                self._corrections = data.get("corrections", [])
                # visitor_count resets each session — don't load stale counts
                print(f"[AIBrain] Memory loaded: {len(self._observations)} observations, "
                      f"{len(self._known_people)} people")
        except Exception as e:
            print(f"[AIBrain] Memory load failed: {e}")

    def _save_memory(self):
        """Save persistent memory to disk."""
        try:
            data = {
                "observations": self._observations,
                "known_people": self._known_people,
                "environment": self._environment,
                "corrections": self._corrections,
                "visitor_count": self._visitor_count,
            }
            with open(self._memory_file, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[AIBrain] Memory save failed: {e}")

    def add_observation(self, key: str, description: str):
        """Add or update a persistent observation."""
        self._observations[key] = description
        self._save_memory()
        print(f"[AIBrain] Observation added: {key} = {description[:50]}")

    def remove_observation(self, key: str):
        """Remove an observation."""
        self._observations.pop(key, None)
        self._save_memory()

    def add_correction(self, correction: str):
        """Add a correction (from Telegram or API). Max 20 kept."""
        self._corrections.append(correction)
        if len(self._corrections) > 20:
            self._corrections = self._corrections[-20:]
        self._save_memory()
        print(f"[AIBrain] Correction added: {correction[:50]}")

    def update_person(self, name: str, role: str = "visitor", notes: str = ""):
        """Update known person info."""
        now = time.time()
        if name in self._known_people:
            p = self._known_people[name]
            p["times_seen"] = p.get("times_seen", 0) + 1
            p["last_seen"] = now
            if notes:
                p["notes"] = notes
            if role != "visitor":
                p["role"] = role
        else:
            self._known_people[name] = {
                "role": role, "notes": notes,
                "times_seen": 1, "first_seen": now, "last_seen": now,
            }
        self._save_memory()

    def add_environment(self, fact: str):
        """Add environmental knowledge."""
        if fact not in self._environment:
            self._environment.append(fact)
            if len(self._environment) > 30:
                self._environment = self._environment[-30:]
            self._save_memory()

    def get_memory_summary(self) -> dict:
        """Full memory summary for API/Telegram."""
        return {
            "observations": self._observations,
            "known_people": self._known_people,
            "environment": self._environment,
            "corrections": self._corrections[-5:],
            "visitor_count": self._visitor_count,
        }

    def _build_memory_context(self) -> str:
        """Build memory context string to include in Claude's prompt."""
        parts = []
        if self._observations:
            obs = [f"- {k}: {v}" for k, v in list(self._observations.items())[-15:]]
            parts.append("COSAS QUE YA APRENDISTE (NO preguntes de nuevo, ya lo sabes):\n" + "\n".join(obs))
        if self._known_people:
            people = []
            for name, info in self._known_people.items():
                role = info.get("role", "visitor")
                notes = info.get("notes", "")
                times = info.get("times_seen", 1)
                desc = f"- {name} ({role}, visto {times}x)"
                if notes:
                    desc += f" — {notes}"
                people.append(desc)
            parts.append("PERSONAS QUE CONOCES:\n" + "\n".join(people))
        if self._environment:
            parts.append("SOBRE ESTE LUGAR:\n" + "\n".join(f"- {e}" for e in self._environment[-10:]))
        if self._corrections:
            parts.append("CORRECCIONES (errores que te corrigieron, NO los repitas):\n" +
                         "\n".join(f"- {c}" for c in self._corrections[-5:]))
        return "\n\n".join(parts)

    @property
    def available(self) -> bool:
        return self._available

    def set_callback(self, callback):
        """Set callback: callback(text, emotion)"""
        self._callback = callback

    def set_vision_filter(self, vision_filter):
        """Connect the vision filter so Claude can teach Hailo."""
        self._vision_filter = vision_filter

    def update_frame(self, frame: np.ndarray, detections: list[str], context: dict = None):
        with self._lock:
            self._frame = frame
            self._detections = detections
            self._context = context or {}

    def start(self):
        if not self._available:
            return
        self._running = True
        threading.Thread(target=self._analysis_loop, daemon=True).start()
        print(f"[AIBrain] Started (interval={ANALYSIS_INTERVAL}s, mode={self._mode})")

    def stop(self):
        self._running = False

    def _has_motion(self, frame: np.ndarray) -> bool:
        """Check if there's significant motion since last analysis."""
        small = cv2.resize(frame, (160, 120))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_frame_gray is None:
            self._prev_frame_gray = gray
            return True  # first frame, always analyze

        diff = cv2.absdiff(gray, self._prev_frame_gray)
        mean_diff = float(np.mean(diff))
        self._prev_frame_gray = gray

        return mean_diff > self._motion_threshold

    def _analysis_loop(self):
        time.sleep(10)
        while self._running:
            now = time.time()

            # Determine interval: normal when motion, slow when idle
            with self._lock:
                frame = self._frame

            if frame is not None:
                motion = self._has_motion(frame)
                if motion:
                    self._idle_since = 0.0
                    interval = ANALYSIS_INTERVAL
                else:
                    if self._idle_since == 0.0:
                        self._idle_since = now
                    idle_time = now - self._idle_since
                    # Gradually slow down: 12s -> 30s -> 60s -> 120s
                    if idle_time < 30:
                        interval = ANALYSIS_INTERVAL
                    elif idle_time < 120:
                        interval = 30.0
                    elif idle_time < 300:
                        interval = 60.0
                    else:
                        interval = self._idle_interval
            else:
                interval = ANALYSIS_INTERVAL

            if now - self._last_analysis >= interval:
                self._last_analysis = now
                try:
                    self._analyze()
                    self._error_count = 0
                except Exception as e:
                    self._error_count += 1
                    if self._error_count <= 3:
                        print(f"[AIBrain] Analysis error: {e}")
                        import traceback
                        traceback.print_exc()
                    if self._error_count > 10:
                        time.sleep(30)
            time.sleep(1)

    def _analyze(self):
        with self._lock:
            frame = self._frame
            detections = list(self._detections)
            context = dict(self._context)

        if frame is None:
            return

        # Resize and encode as JPEG
        small = cv2.resize(frame, (320, 240))
        _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        img_b64 = base64.b64encode(buf.tobytes()).decode("utf-8")

        # Build precise context message
        det_text = ", ".join(detections) if detections else "nada"
        person_count = context.get("person_count", 0)

        parts = []

        # Time context — critical for intelligent reasoning
        import datetime
        now_dt = datetime.datetime.now()
        hour = now_dt.hour
        time_str = now_dt.strftime("%H:%M")
        day_str = now_dt.strftime("%A")
        if hour < 6:
            period = "madrugada"
        elif hour < 12:
            period = "mañana"
        elif hour < 19:
            period = "tarde"
        else:
            period = "noche"
        parts.append(f"Hora actual: {time_str} ({period}, {day_str}). Razona segun el horario.")
        if detections:
            parts.append(f"Hailo detecta SOLO estos objetos: [{det_text}]. NO menciones objetos que no estan en esta lista.")
        else:
            parts.append("Hailo NO detecta ningun objeto. Solo describe lo que ves en general (ambiente, iluminacion).")

        if person_count > 0:
            parts.append(f"Personas confirmadas por Hailo: exactamente {person_count}.")
        else:
            parts.append("Hailo NO detecta personas. Si ves una sombra, NO digas que es una persona.")

        # Known face — critical for memory
        known_face = context.get("known_face", "")
        now_ts = time.time()
        if known_face:
            already_greeted = known_face in self._greeted_faces
            if already_greeted:
                parts.append(f"Persona reconocida: {known_face} (ya lo saludaste, NO saludes de nuevo, comenta otra cosa).")
            else:
                parts.append(f"Persona reconocida: {known_face} (primera vez que lo ves hoy, saludalo).")
                self._greeted_faces.add(known_face)
            # Track visitor — by name (no duplicates)
            if known_face not in self._visitor_seen_times:
                self._visitor_count += 1
            self._visitor_seen_times[known_face] = now_ts
            # Update persistent people memory
            self.update_person(known_face, context.get("known_role", "visitor"))
        elif person_count > 0:
            # Unknown person — only count as NEW visitor when:
            # 1. person_count increased AND
            # 2. there was a gap of 30+ seconds with zero persons (real absence, not flicker)
            if person_count > self._last_person_count and self._last_no_person_time > 0:
                gap = now_ts - self._last_no_person_time
                if gap > 30:  # person was gone for 30+ seconds — likely a new visitor
                    unknown_key = f"unknown_{int(now_ts) // 600}"  # 10-min windows
                    if unknown_key not in self._visitor_seen_times:
                        self._visitor_count += 1
                        self._visitor_seen_times[unknown_key] = now_ts
                        self._save_memory()
                        parts.append("Persona nueva detectada. Presentate brevemente.")

        # Track when there are zero persons (for gap detection)
        if person_count == 0:
            if self._last_person_count > 0:
                self._last_no_person_time = now_ts
        self._last_person_count = person_count

        # Visitor stats (only mention if meaningful)
        if self._visitor_count > 1:
            uptime_h = (now_ts - self._session_start) / 3600
            parts.append(f"Visitantes unicos hoy: {self._visitor_count}. Tiempo activo: {uptime_h:.1f}h.")

        # Priority reminder based on scene
        if person_count > 0:
            parts.append(">>> HAY PERSONAS PRESENTES. Tu respuesta DEBE ser sobre ellas. NO hables del clima, stats, ni tecnologia cuando alguien esta frente a vos. <<<")

        # Other context
        if context.get("gesture"):
            parts.append(f"Gesto detectado: {context['gesture']}.")
        if context.get("smile"):
            parts.append("Alguien esta sonriendo.")
        if context.get("waf_attacks", 0) > 0:
            parts.append(f"WAF: {context['waf_attacks']} ataques bloqueados.")
        if context.get("wifi_deauth"):
            parts.append(f"WiFi defense: {context['wifi_deauth']} ataques deauth detectados.")
        if context.get("wifi_evil_twins"):
            parts.append(f"WiFi defense: {context['wifi_evil_twins']} evil twins detectados!")
        if context.get("music"):
            parts.append(f"Musica sonando: {context['music']}.")
        if context.get("temp_interior"):
            parts.append(f"Temperatura INTERIOR de la casa: {context['temp_interior']}.")
        if context.get("temp_exterior"):
            parts.append(f"Clima EXTERIOR (met.no): {context['temp_exterior']}. Es la temp de afuera, NO de la casa.")
        if context.get("drone_flying"):
            parts.append(f"Drone volando. Bateria: {context.get('drone_battery', '?')}%.")
        if context.get("drone_distance"):
            parts.append(f"Drone a {context['drone_distance']}.")
        if context.get("coffee_status"):
            parts.append(f"Maquina de cafe: {context['coffee_status']}.")
        if context.get("health"):
            parts.append(context["health"])

        # Anti-repetition hint
        if self._recent_phrases:
            last3 = self._recent_phrases[-3:]
            parts.append(f"Tus ultimas frases empezaron con: {[p[:25] for p in last3]}. NO repitas patrones similares.")

        # FPV context if available
        if context.get("drone_fpv"):
            parts.append(f"FPV drone: modo={context.get('fpv_mode')}, personas={context.get('fpv_persons', 0)}.")
        if context.get("demo_flight"):
            parts.append(f"Demo flight en curso: {context['demo_flight']}.")

        # Include persistent memory context
        memory_ctx = self._build_memory_context()
        if memory_ctx:
            parts.append(f"\n--- TU MEMORIA PERSISTENTE ---\n{memory_ctx}")

        # Ask Claude to extract observations (append to response with |OBS: prefix)
        parts.append(
            "\nSi ves algo nuevo en la imagen que valga recordar (texto en carteles, objetos fijos, "
            "caracteristicas del lugar), agrega al final de tu respuesta: |OBS:clave=descripcion "
            "(ejemplo: |OBS:cartel_pared=Niperia Lab en letras verdes). "
            "Solo si es algo NUEVO que no esta en tu memoria. Maximo 1 observacion por respuesta."
        )

        # Ask Claude to flag false positives from Hailo
        parts.append(
            "Si Hailo detecta algo que NO coincide con lo que ves en la imagen (falso positivo), "
            "agrega al final: |FP:label=lo_que_hailo_dijo,real=lo_que_realmente_es,reason=por_que "
            "(ejemplo: |FP:label=knife,real=control_remoto,reason=forma similar pero es un control). "
            "Solo si ESTAS SEGURO que es un falso positivo. Esto entrena el filtro de vision.\n"
            "IMPORTANTE: Valida el conteo de personas. Si Hailo dice 3 personas pero en la imagen "
            "solo hay 2, di exactamente cuantas personas REALES ves. Reflejos en pantallas, posters, "
            "TV con personas o sombras NO cuentan. Se preciso."
        )

        user_msg = " ".join(parts)

        # Build messages with history for context continuity
        messages = []
        for hist in self._history[-self._max_history:]:
            messages.append(hist)

        # Current message with image
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_b64,
                    }
                },
                {"type": "text", "text": user_msg}
            ]
        })

        # Build system prompt with memory
        base_prompt = SYSTEM_PROMPT_EN if self._lang == "en" else SYSTEM_PROMPT
        system = base_prompt

        # Smart model routing: use fast model for periodic comments,
        # deep model when someone is interacting or complex scene
        # Deep model only for: forced, crowd (>2 people), or security events
        # Gestures handled fine by Sonnet — not worth Opus cost
        use_deep = self._force_deep or person_count > 2 or context.get("wifi_deauth")
        self._force_deep = False  # reset

        if use_deep or not self._client_fast:
            model = VERTEX_MODEL
            client = self._client
            reason = "deep" if use_deep else "no_fast_client"
        else:
            model = VERTEX_MODEL_FAST
            client = self._client_fast
            reason = "fast"

        print(f"[AIBrain] Call: {model} ({reason}, persons={person_count}, force={self._force_deep}, gesture={bool(context.get('gesture'))}, client_fast={self._client_fast is not None})")

        # Call Claude with conversation history
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS + 40,  # extra for |OBS: tag
            system=system,
            messages=messages,
        )

        text = ""
        if response.content:
            text = response.content[0].text

        if text:
            # Extract observations from response
            display_text = text
            if "|OBS:" in text:
                obs_parts = text.split("|OBS:")
                display_text = obs_parts[0].strip()
                for obs in obs_parts[1:]:
                    obs = obs.strip()
                    # Strip any trailing |FP: that might be concatenated
                    if "|FP:" in obs:
                        obs = obs.split("|FP:")[0].strip()
                    if "=" in obs:
                        key, val = obs.split("=", 1)
                        key = key.strip().lower().replace(" ", "_")
                        val = val.strip()
                        if key and val and key not in self._observations:
                            self.add_observation(key, val)

            # Extract false positive corrections for vision filter
            if "|FP:" in display_text:
                fp_parts = display_text.split("|FP:")
                display_text = fp_parts[0].strip()
                for fp in fp_parts[1:]:
                    self._process_fp_correction(fp.strip())

            print(f"[AIBrain] Claude says: {display_text[:80]}")
            self._last_response = display_text
            emotion = self._detect_emotion(display_text)
            self._last_emotion = emotion

            # Track recent phrases for anti-repetition
            self._recent_phrases.append(display_text.lower()[:50])
            if len(self._recent_phrases) > self._max_recent:
                self._recent_phrases.pop(0)

            # Save to history (text-only to save tokens on next call)
            self._history.append({"role": "user", "content": user_msg})
            self._history.append({"role": "assistant", "content": display_text})
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            if self._callback:
                self._callback(display_text, emotion)
        else:
            print("[AIBrain] Empty response from Claude")

    def _process_fp_correction(self, fp_text: str):
        """Parse and apply a false positive correction from Claude.

        Format: label=knife,real=control_remoto,reason=forma similar
        """
        if not self._vision_filter:
            return
        try:
            parts = {}
            for part in fp_text.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    parts[k.strip()] = v.strip()

            label = parts.get("label", "")
            real = parts.get("real", "unknown")
            reason = parts.get("reason", "")

            if not label:
                return

            # Get the detection region from current context (approximate)
            region = [0, 0, 1, 1]  # full frame if we don't have bbox
            with self._lock:
                for det_str in self._detections:
                    if label.lower() in det_str.lower():
                        # Found the detection — use full frame as region
                        # (exact bbox not available here, filter will use tolerance)
                        break

            self._vision_filter.add_correction(
                label=label,
                correct_label=real,
                reason=reason,
                region=region,
                confidence=0.5,
            )
            print(f"[AIBrain] FP correction sent to filter: {label} -> {real}")
        except Exception as e:
            print(f"[AIBrain] FP parse error: {e}")

    def _detect_emotion(self, text: str) -> str:
        text_lower = text.lower()
        if any(w in text_lower for w in ["alerta", "peligro", "amenaza", "ataque", "cuidado"]):
            return "alert"
        if any(w in text_lower for w in ["jaja", "gracioso", "risa", "divertido", "copado", "genial"]):
            return "happy"
        if any(w in text_lower for w in ["interesante", "curioso", "hmm", "que sera"]):
            return "curious"
        if any(w in text_lower for w in ["enojado", "intruso", "hostil", "bloqueando"]):
            return "angry"
        if any(w in text_lower for w in ["bien", "perfecto", "todo ok", "tranqui", "piola"]):
            return "happy"
        return "neutral"

    @property
    def last_response(self) -> str:
        return self._last_response

    @property
    def visitor_count(self) -> int:
        return self._visitor_count

    @property
    def lang(self) -> str:
        return self._lang

    def set_lang(self, lang: str):
        """Switch language: 'es' or 'en'."""
        if lang in ("es", "en"):
            self._lang = lang
            self._history.clear()
            self._recent_phrases.clear()
            print(f"[AIBrain] Language switched to: {lang}")

    def get_stats(self) -> dict:
        """Stats for dashboard/API."""
        uptime = time.time() - self._session_start
        return {
            "visitor_count": self._visitor_count,
            "uptime_hours": round(uptime / 3600, 1),
            "total_analyses": len(self._recent_phrases),
            "greeted_faces": list(self._greeted_faces),
            "mode": self._mode,
            "available": self._available,
            "lang": self._lang,
        }
