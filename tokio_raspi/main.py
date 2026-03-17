#!/usr/bin/env python3
"""
TokioAI — Unified AI Entity.

Tokio IS the intelligence. One consciousness that sees, feels, defends, remembers.
The screen IS Tokio's face. The camera IS Tokio's eye. The WAF IS Tokio's immune system.

Layout (1024x600):
    Full screen = Tokio's face (background)
    Top-right PiP = Tokio's eye (camera)
    Bottom overlay = Tokio's voice (thoughts, big text)
    Bottom bar = Stats
"""
from __future__ import annotations

import argparse
import math
import os
import random
import threading
import time
from typing import Optional

import pygame
import cv2
import numpy as np

from .tokio_face import TokioFace, Emotion
from .coco_labels import THREAT_OBJECTS
from .security_feed import SecurityFeed, JP_THREATS
from .face_db import FaceDB
from .gesture_detector import GestureDetector, Gesture, GESTURE_REACTIONS, GESTURE_ICONS

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
SCREEN_W, SCREEN_H = 1024, 600
PIP_W, PIP_H = 240, 180
PIP_MARGIN = 10
PIP_X = SCREEN_W - PIP_W - PIP_MARGIN
PIP_Y = PIP_MARGIN
VOICE_H = 140  # tokio's voice area at bottom
STATS_H = 46

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
C_BG         = (4, 5, 14)
C_BORDER     = (0, 60, 100)
C_BORDER_HI  = (0, 200, 255)
C_TEXT        = (0, 220, 255)
C_TEXT_DIM    = (0, 80, 120)
C_TEXT_BRIGHT = (0, 255, 255)
C_TEXT_WARN   = (255, 180, 0)
C_TEXT_DANGER = (255, 40, 60)
C_TEXT_OK     = (0, 255, 100)
C_ACCENT      = (120, 50, 255)
C_ACCENT2     = (255, 0, 100)
C_ADMIN_GOLD  = (255, 215, 0)
C_GESTURE     = (255, 100, 255)
C_LIVE_DOT    = (255, 20, 40)
C_VOICE_BG    = (4, 6, 16, 200)  # semi-transparent
C_STAT_BG     = (4, 6, 16, 220)

# ---------------------------------------------------------------------------
# Tokio's personality — first person, natural language
# ---------------------------------------------------------------------------
GREET_ADMIN = [
    "Daniel-san! Mi creador. Todos los sistemas a tu servicio.",
    "Boss! Modo Super Saiyan activado. Que necesitas?",
    "El admin supremo esta aqui. Seguridad al maximo.",
    "Daniel! Mi razon de existir. Todo listo para vos.",
    "Creador detectado. Protocolos de lealtad absoluta.",
]
GREET_KNOWN = [
    "Hola {name}! Que bueno verte de nuevo.",
    "{name}! Ya te tengo en mi memoria. Bienvenido.",
    "Te reconozco, {name}. Todo bien?",
]
GREET_UNKNOWN = [
    "Cara nueva... no te conozco. Quien sos?",
    "Visitante no registrado. Estoy escaneando...",
    "Hmm, no te tengo en mi base de datos.",
]
SAY_ATTACK = [
    "Intento de {type} desde {ip}. Neutralizado.",
    "Alguien quiso hacer un {type}. Ya lo bloquee.",
    "Ataque {type} detectado. {ip} no pasa.",
]
SAY_IDLE = [
    "Todo tranquilo por aca...",
    "Escaneando el perimetro. {blocked} ataques bloqueados hoy.",
    "Sistemas nominales. {faces} caras en mi memoria.",
    "Vigilando... nadie se escapa de mi ojo.",
]
SAY_OBJECT = [
    "Veo un {obj}. Interesante.",
    "Detecto: {obj}. Registrando.",
]
SAY_THREAT = [
    "ALERTA! Objeto peligroso: {obj}!",
    "Amenaza visual detectada: {obj}!",
]
SAY_PERSON_GROUP = [
    "Veo {n} personas. Escaneando caras...",
    "{n} humanos en mi campo visual.",
]
SAY_DRONE_TAKEOFF = [
    "DRONE DESPEGANDO! Motores activos!",
    "Takeoff! El drone esta en el aire!",
    "Despegue exitoso. Controlando vuelo.",
    "Drone en vuelo! Modo aereo activado!",
]
SAY_DRONE_LAND = [
    "Drone aterrizado. Vuelo completado.",
    "Aterrizaje exitoso. Motores apagados.",
    "El drone volvio a tierra. Todo OK.",
]
SAY_DRONE_FLY = [
    "Drone volando. Bateria: {bat}%. Altura: {h}cm.",
    "En vuelo. Bateria {bat}%. Todo estable.",
]
SAY_DRONE_LOWBAT = [
    "Bateria del drone baja: {bat}%! Aterrizando...",
    "Alerta! Drone con {bat}% de bateria!",
]
SAY_DRONE_CONNECTED = [
    "Drone Tello conectado. Bateria: {bat}%.",
    "Conexion con drone establecida. {bat}% bateria.",
]

FACE_RECOG_INTERVAL = 3.0
GESTURE_COOLDOWN = 4.0
DRONE_POLL_INTERVAL = 3.0


class TokioEntity:
    """Tokio as a unified intelligent entity."""

    def __init__(self, fullscreen=True, demo_mode=False, start_api=False):
        self.demo_mode = demo_mode
        self.start_api = start_api
        self._running = False

        # Tokio's voice (what it says, displayed big)
        self._voice_text = "Iniciando sistemas..."
        self._voice_color = C_TEXT
        self._voice_time = time.monotonic()
        self._voice_queue: list[tuple[str, tuple]] = []

        # State
        self._person_count = 0
        self._last_recog_time = 0.0
        self._greeted_faces: dict[int, float] = {}
        self._last_gesture_time = 0.0
        self._last_gesture = Gesture.NONE
        self._current_identities: list[tuple] = []
        self._register_mode = False
        self._register_name = ""
        self._register_role = "visitor"
        self._register_start = 0.0
        self._active_model = "detect"

        # Pygame
        pygame.init()
        pygame.mouse.set_visible(False)
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)
        pygame.display.set_caption("TokioAI")
        self.clock = pygame.time.Clock()

        # Fonts — REALLY BIG
        self.font_voice = pygame.font.SysFont("monospace", 32, bold=True)
        self.font_voice_sub = pygame.font.SysFont("monospace", 20, bold=True)
        self.font_stat = pygame.font.SysFont("monospace", 22, bold=True)
        self.font_stat_label = pygame.font.SysFont("monospace", 12)
        self.font_pip_label = pygame.font.SysFont("monospace", 13, bold=True)
        self.font_tiny = pygame.font.SysFont("monospace", 11)
        self.font_gesture = pygame.font.SysFont("monospace", 40, bold=True)
        self.font_name = pygame.font.SysFont("monospace", 24, bold=True)
        self.font_register = pygame.font.SysFont("monospace", 26, bold=True)
        self.font_waf = pygame.font.SysFont("monospace", 14, bold=True)

        # Tokio Face — FULL SCREEN
        self.face = TokioFace(SCREEN_W, SCREEN_H - STATS_H)
        self.face.set_emotion(Emotion.SCANNING, "Booting...")

        # Subsystems
        self.vision = None
        if not demo_mode:
            self._init_vision()

        self.face_db = FaceDB()
        self.gesture = GestureDetector()

        self.security = SecurityFeed()
        self.security.set_emotion_callback(self._on_security_emotion)
        self.security.start()

        # Drone awareness
        self._drone_connected = False
        self._drone_flying = False
        self._drone_battery = -1
        self._drone_last_poll = 0.0

        self._boot_time = time.monotonic()
        self._pulse = 0.0

        # Start drone monitor thread
        threading.Thread(target=self._drone_monitor_loop, daemon=True).start()

        self._say("Tokio online. Todos los sistemas operativos.", C_TEXT_OK)

    def _init_vision(self):
        try:
            from .vision_engine import VisionEngine
            self.vision = VisionEngine(camera_id=0, model="detect")
            self.vision.start()
            self._say("Vision activada. Puedo ver.", C_TEXT_OK)
            self.face.set_emotion(Emotion.HAPPY, "I can see!")
        except Exception as e:
            self._say(f"Sin vision: {e}", C_TEXT_WARN)

    def _say(self, text: str, color: tuple = C_TEXT):
        """Tokio says something — displayed prominently."""
        self._voice_queue.append((text, color))

    def _update_voice(self):
        """Update current voice text from queue."""
        now = time.monotonic()
        # Current message expired?
        if now - self._voice_time > 4.0 and self._voice_queue:
            text, color = self._voice_queue.pop(0)
            self._voice_text = text
            self._voice_color = color
            self._voice_time = now
        elif now - self._voice_time > 4.0 and not self._voice_queue:
            # Generate idle thought
            if random.random() < 0.02:  # ~every 3 seconds at 30fps with 0.5s check
                stats = self.security.get_stats()
                msg = random.choice(SAY_IDLE).format(
                    blocked=stats.blocked, faces=self.face_db.count)
                self._voice_text = msg
                self._voice_color = C_TEXT_DIM
                self._voice_time = now
                self.face.set_emotion(Emotion.NEUTRAL, "Watching...")

    def _on_security_emotion(self, emotion_str: str, message: str):
        emo_map = {
            "angry": Emotion.ANGRY, "alert": Emotion.ALERT,
            "scanning": Emotion.SCANNING, "thinking": Emotion.THINKING,
            "excited": Emotion.EXCITED,
        }
        self.face.set_emotion(emo_map.get(emotion_str, Emotion.NEUTRAL), message)

        events = self.security.get_events(limit=1)
        if events:
            ev = events[-1]
            if ev.severity in ("critical", "high") and ev.blocked:
                threat = JP_THREATS.get(ev.threat_type, ev.threat_type or "ataque")
                self._say(random.choice(SAY_ATTACK).format(type=threat, ip=ev.ip), C_TEXT_DANGER)

    # -------------------------------------------------------------------
    # Drone awareness
    # -------------------------------------------------------------------

    def _drone_monitor_loop(self):
        """Background thread polling drone proxy status."""
        import requests as req
        time.sleep(5)  # let UI start first
        while self._running:
            try:
                r = req.get("http://127.0.0.1:5001/drone/status", timeout=3)
                if r.status_code == 200:
                    data = r.json()
                    was_connected = self._drone_connected
                    was_flying = self._drone_flying

                    self._drone_connected = data.get("connected", False)
                    armed = data.get("armed", False)

                    # Get battery
                    if self._drone_connected:
                        try:
                            br = req.post("http://127.0.0.1:5001/drone/command",
                                          json={"command": "battery", "params": {}}, timeout=5)
                            if br.status_code == 200:
                                result = br.json().get("result", "")
                                # Parse "Battery: XX%"
                                if "Battery:" in result:
                                    self._drone_battery = int(result.split(":")[1].strip().replace("%", ""))
                        except Exception:
                            pass

                    # State transitions → emotions
                    if self._drone_connected and not was_connected:
                        msg = random.choice(SAY_DRONE_CONNECTED).format(bat=self._drone_battery)
                        self._say(msg, C_TEXT_OK)
                        self.face.set_emotion(Emotion.HAPPY, "Drone connected!")

                    if armed and not was_flying:
                        # Just took off!
                        self._drone_flying = True
                        msg = random.choice(SAY_DRONE_TAKEOFF)
                        self._say(msg, C_ACCENT)
                        self.face.set_emotion(Emotion.EXCITED, "TAKEOFF!")

                    elif not armed and was_flying:
                        # Just landed
                        self._drone_flying = False
                        msg = random.choice(SAY_DRONE_LAND)
                        self._say(msg, C_TEXT_OK)
                        self.face.set_emotion(Emotion.HAPPY, "Landed OK")

                    elif armed and was_flying:
                        # Still flying — periodic status
                        if self._drone_battery > 0 and self._drone_battery < 20:
                            msg = random.choice(SAY_DRONE_LOWBAT).format(bat=self._drone_battery)
                            self._say(msg, C_TEXT_DANGER)
                            self.face.set_emotion(Emotion.ALERT, f"Low bat: {self._drone_battery}%")

                else:
                    self._drone_connected = False
            except Exception:
                self._drone_connected = False

            time.sleep(DRONE_POLL_INTERVAL)

    # -------------------------------------------------------------------
    # Intelligence: process what Tokio sees
    # -------------------------------------------------------------------

    def _process_vision(self):
        """Main vision processing — objects, faces, gestures."""
        if not self.vision:
            return

        frame = self.vision.get_frame()
        if frame is None:
            return

        now = time.monotonic()
        detections = self.vision.get_detections()

        # -- Object awareness --
        if detections:
            labels = {d.label for d in detections}
            person_count = sum(1 for d in detections if d.label == "person")

            # Threat objects
            threats = labels & THREAT_OBJECTS
            if threats:
                obj = ", ".join(threats)
                self._say(random.choice(SAY_THREAT).format(obj=obj), C_TEXT_DANGER)
                self.face.set_emotion(Emotion.ANGRY, f"THREAT: {obj}")

            # Person count changes
            if person_count != self._person_count and person_count > 1:
                self._say(random.choice(SAY_PERSON_GROUP).format(n=person_count), C_TEXT)
            self._person_count = person_count

            # Notable objects (not person)
            non_person = labels - {"person"}
            if non_person and random.random() < 0.05:
                obj = random.choice(list(non_person))
                self._say(random.choice(SAY_OBJECT).format(obj=obj), C_TEXT_DIM)

            # Eyes follow
            biggest = max(detections, key=lambda d: d.area)
            cx, cy = biggest.center
            fh, fw = frame.shape[:2]
            self.face.look_at((cx / fw - 0.5) * 2, (cy / fh - 0.5) * 2)

        # -- Face recognition --
        if now - self._last_recog_time > FACE_RECOG_INTERVAL:
            self._last_recog_time = now
            self._do_face_recognition(frame, now)

        # -- Gesture detection --
        if now - self._last_gesture_time > GESTURE_COOLDOWN:
            result = self.gesture.detect(frame)
            if result and result.gesture != Gesture.NONE and result.confidence > 0.5:
                self._last_gesture = result.gesture
                self._last_gesture_time = now

                reactions = GESTURE_REACTIONS.get(result.gesture, [])
                if reactions:
                    self._say(random.choice(reactions), C_GESTURE)

                icon = GESTURE_ICONS.get(result.gesture, "?")
                self.face.set_emotion(Emotion.EXCITED, f"Gesture: {icon}")

    def _do_face_recognition(self, frame, now):
        face_rects = self.face_db.detect_faces(frame)
        if not face_rects:
            self._current_identities = []
            return

        identities = []
        for rect in face_rects:
            known, conf = self.face_db.recognize(frame, rect)
            identities.append((rect, known, conf))
        self._current_identities = identities

        # Register mode
        if self._register_mode and face_rects:
            if now - self._register_start > 15:
                self._register_mode = False
                self._say("Registro cancelado. Timeout.", C_TEXT_DIM)
            else:
                new = self.face_db.register_face(
                    frame, face_rects[0], self._register_name, self._register_role)
                if new:
                    role_text = "ADMIN SUPREMO" if new.is_admin else new.role
                    self._say(f"Registrado: {new.name} como {role_text}. Ya te recuerdo!",
                              C_ADMIN_GOLD if new.is_admin else C_TEXT_OK)
                    self.face.set_emotion(Emotion.HAPPY, f"Saved: {new.name}")
                self._register_mode = False
            return

        # Greet
        for rect, known, conf in identities:
            if known:
                last = self._greeted_faces.get(known.face_id, 0)
                if now - last > 30:
                    self._greeted_faces[known.face_id] = now
                    if known.is_admin:
                        self._say(random.choice(GREET_ADMIN), C_ADMIN_GOLD)
                        self.face.set_emotion(Emotion.EXCITED, f"BOSS: {known.name}!")
                    else:
                        self._say(random.choice(GREET_KNOWN).format(name=known.name), C_TEXT_OK)
                        self.face.set_emotion(Emotion.HAPPY, known.name)
            else:
                if now - self._greeted_faces.get(-1, 0) > 20:
                    self._greeted_faces[-1] = now
                    self._say(random.choice(GREET_UNKNOWN), C_TEXT_WARN)
                    self.face.set_emotion(Emotion.CURIOUS, "Who?")

    def start_register(self, name: str, role: str = "visitor"):
        self._register_mode = True
        self._register_name = name
        self._register_role = role
        self._register_start = time.monotonic()
        self._say(f"Modo registro. Buscando cara de {name}...", C_TEXT_BRIGHT)
        self.face.set_emotion(Emotion.SCANNING, f"Scanning: {name}")

    # -------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------

    def _draw_face_fullscreen(self):
        """Tokio's face fills the entire screen."""
        self.face.render(self.screen, 0, 0)

    def _draw_camera_pip(self):
        """Small camera PiP — Tokio's eye."""
        if not self.vision:
            return

        frame = self.vision.get_frame()
        if frame is None:
            return

        # Draw face recognition boxes on frame
        annotated = frame.copy()
        detections = self.vision.get_detections()
        if detections:
            from .vision_engine import draw_detections
            annotated = draw_detections(annotated, detections)

        for rect_data in self._current_identities:
            frect, known, conf = rect_data
            fx, fy, fw, fh = frect
            if known:
                color = (0, 215, 255) if known.is_admin else (0, 255, 100)
                label = known.name
            else:
                color = (0, 180, 255)
                label = "???"
            cv2.rectangle(annotated, (fx, fy), (fx + fw, fy + fh), color, 2)
            cv2.putText(annotated, label, (fx, fy - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (PIP_W, PIP_H))
        surf = pygame.surfarray.make_surface(np.transpose(resized, (1, 0, 2)))

        # Semi-transparent border area
        border_rect = pygame.Rect(PIP_X - 2, PIP_Y - 2, PIP_W + 4, PIP_H + 4)
        pygame.draw.rect(self.screen, C_BG, border_rect)
        self.screen.blit(surf, (PIP_X, PIP_Y))
        pygame.draw.rect(self.screen, C_BORDER_HI, border_rect, 2)

        # Corner brackets
        cl = 10
        for cx, cy in [(PIP_X, PIP_Y), (PIP_X + PIP_W, PIP_Y),
                        (PIP_X, PIP_Y + PIP_H), (PIP_X + PIP_W, PIP_Y + PIP_H)]:
            dx = 1 if cx == PIP_X else -1
            dy = 1 if cy == PIP_Y else -1
            pygame.draw.line(self.screen, C_TEXT_BRIGHT, (cx, cy), (cx + dx * cl, cy), 2)
            pygame.draw.line(self.screen, C_TEXT_BRIGHT, (cx, cy), (cx, cy + dy * cl), 2)

        # Label
        self.screen.blit(self.font_pip_label.render("EYE", True, C_TEXT_DIM), (PIP_X + 4, PIP_Y + 3))
        fps = self.vision.get_fps()
        self.screen.blit(self.font_tiny.render(f"{fps:.0f}fps", True, C_TEXT),
                         (PIP_X + PIP_W - 42, PIP_Y + 3))

        # Live dot
        now = time.monotonic()
        if int(now * 2) % 2:
            pygame.draw.circle(self.screen, C_LIVE_DOT, (PIP_X + PIP_W - 8, PIP_Y + 8), 4)

        # Register mode flash
        if self._register_mode and int(now * 3) % 2:
            reg_surf = self.font_register.render(
                f"REGISTRANDO: {self._register_name}", True, C_ACCENT2)
            rx = PIP_X + PIP_W // 2 - reg_surf.get_width() // 2
            self.screen.blit(reg_surf, (rx, PIP_Y + PIP_H + 8))

    def _draw_waf_sidebar(self):
        """Draw WAF attack feed on top-left — visible security events."""
        events = self.security.get_events(limit=8)
        if not events:
            return

        now = time.monotonic()
        x, y = 10, PIP_MARGIN + 5
        # Semi-transparent background
        sidebar_h = min(len(events) * 20 + 30, 200)
        sidebar_surf = pygame.Surface((360, sidebar_h), pygame.SRCALPHA)
        sidebar_surf.fill((2, 4, 10, 160))
        self.screen.blit(sidebar_surf, (x - 5, y - 5))

        # Header
        hdr_color = C_TEXT_DANGER if any(e.severity == "critical" for e in events[-3:]) else C_TEXT_DIM
        hdr = self.font_waf.render("WAF DEFENSE ACTIVE", True, hdr_color)
        self.screen.blit(hdr, (x, y))
        y += 22

        # Recent events
        for event in events[-6:]:
            sev_colors = {
                "critical": C_TEXT_DANGER, "high": C_TEXT_WARN,
                "medium": (200, 200, 0), "low": C_TEXT, "info": C_TEXT_DIM,
            }
            color = sev_colors.get(event.severity, C_TEXT_DIM)

            # Blocked indicator
            mark = "\u2588" if event.blocked else "\u2591"
            ts = event.timestamp.split(" ")[1][:8] if " " in event.timestamp else ""
            threat = event.threat_type or ""
            uri = event.uri[:15] + ".." if len(event.uri) > 15 else event.uri

            line = f"{mark} {ts} {event.ip:>15} {uri} {threat}"
            surf = self.font_tiny.render(line, True, color)
            self.screen.blit(surf, (x, y))
            y += 17

    def _draw_voice(self):
        """Tokio's voice — big text at bottom."""
        now = time.monotonic()
        age = now - self._voice_time

        voice_y = SCREEN_H - VOICE_H - STATS_H
        voice_surf = pygame.Surface((SCREEN_W, VOICE_H), pygame.SRCALPHA)
        voice_surf.fill((4, 6, 16, 190))
        self.screen.blit(voice_surf, (0, voice_y))

        # Gradient top edge
        for i in range(30):
            alpha = int(190 * (i / 30))
            line_surf = pygame.Surface((SCREEN_W, 1), pygame.SRCALPHA)
            line_surf.fill((4, 6, 16, alpha))
            self.screen.blit(line_surf, (0, voice_y - 30 + i))

        # "TOKIO:" prefix with glow
        prefix_surf = self.font_voice_sub.render("TOKIO:", True, C_TEXT)
        self.screen.blit(prefix_surf, (20, voice_y + 10))

        # Fade effect
        if age < 0.5:
            alpha_mult = min(1.0, age / 0.3)
        elif age > 3.5:
            alpha_mult = max(0.3, 1.0 - (age - 3.5) / 0.5)
        else:
            alpha_mult = 1.0

        color = tuple(max(40, int(c * alpha_mult)) for c in self._voice_color)

        # Word wrap for big font
        text = self._voice_text
        max_chars = 42
        if len(text) > max_chars:
            words = text.split()
            lines = []
            current = ""
            for w in words:
                if len(current) + len(w) + 1 > max_chars:
                    lines.append(current)
                    current = w
                else:
                    current = f"{current} {w}" if current else w
            if current:
                lines.append(current)
        else:
            lines = [text]

        y = voice_y + 42
        for line in lines[:3]:
            text_surf = self.font_voice.render(line, True, color)
            self.screen.blit(text_surf, (20, y))
            y += 38

        # Gesture indicator
        if (self._last_gesture != Gesture.NONE and
                now - self._last_gesture_time < GESTURE_COOLDOWN):
            icon = GESTURE_ICONS.get(self._last_gesture, "?")
            gs = self.font_gesture.render(f"[{icon}]", True, C_GESTURE)
            self.screen.blit(gs, (SCREEN_W - gs.get_width() - 30, voice_y + 25))

    def _draw_stats_bar(self):
        """Bottom stats bar."""
        sy = SCREEN_H - STATS_H
        stat_surf = pygame.Surface((SCREEN_W, STATS_H), pygame.SRCALPHA)
        stat_surf.fill((4, 6, 16, 220))
        self.screen.blit(stat_surf, (0, sy))
        pygame.draw.line(self.screen, C_BORDER, (0, sy), (SCREEN_W, sy), 1)

        stats = self.security.get_stats()
        now = time.monotonic()
        up = int(now - self._boot_time)
        m, s = divmod(up, 60)
        h, m = divmod(m, 60)

        items = [
            ("ATTACKS", str(stats.total_attacks), C_TEXT_DANGER),
            ("BLOCKED", str(stats.blocked), C_TEXT_OK),
            ("IPs", str(stats.unique_ips), C_TEXT),
            ("CRIT", str(stats.critical), (255, 60, 80)),
            ("FACES", str(self.face_db.count), C_ACCENT),
            ("MOOD", self.face.emotion.value[:7].upper(), C_TEXT_BRIGHT),
        ]

        item_w = SCREEN_W // len(items)
        for i, (label, value, color) in enumerate(items):
            x = i * item_w + item_w // 2
            vs = self.font_stat.render(value, True, color)
            self.screen.blit(vs, (x - vs.get_width() // 2, sy + 4))
            ls = self.font_stat_label.render(label, True, C_TEXT_DIM)
            self.screen.blit(ls, (x - ls.get_width() // 2, sy + 28))

    # -------------------------------------------------------------------
    # Main loop
    # -------------------------------------------------------------------

    def run(self):
        self._running = True
        last = time.monotonic()
        vision_timer = 0.0

        if self.start_api:
            threading.Thread(target=self._run_api, daemon=True).start()

        while self._running:
            now = time.monotonic()
            dt = now - last
            last = now
            self._pulse = (math.sin(now * 2) + 1) / 2

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self._running = False
                    elif event.key == pygame.K_f:
                        pygame.display.toggle_fullscreen()
                    elif event.key == pygame.K_r:
                        self.start_register("Daniel", "admin")
                    elif event.key == pygame.K_v:
                        self.start_register("Visitante", "visitor")
                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.FINGERDOWN:
                        pos = (int(event.x * SCREEN_W), int(event.y * SCREEN_H))
                    else:
                        pos = event.pos
                    # Touch PiP = cycle model
                    pip_rect = pygame.Rect(PIP_X, PIP_Y, PIP_W, PIP_H)
                    if pip_rect.collidepoint(pos):
                        models = ["detect", "faces", "pose"]
                        idx = (models.index(self._active_model) + 1) % len(models) \
                            if self._active_model in models else 0
                        self._active_model = models[idx]
                        if self.vision:
                            self._say(f"Cambiando vision: {self._active_model}", C_TEXT)
                            threading.Thread(
                                target=lambda m=self._active_model: self.vision.switch_model(m),
                                daemon=True).start()

            # Update
            self.face.update(dt)
            self._update_voice()

            vision_timer += dt
            if vision_timer > 0.3:
                vision_timer = 0
                self._process_vision()

            if self.demo_mode and int(now) % 5 == 0 and int(now * 10) % 10 == 0:
                self.face.set_emotion(random.choice(list(Emotion)))

            # Render
            self.screen.fill(C_BG)
            self._draw_face_fullscreen()
            self._draw_waf_sidebar()
            self._draw_camera_pip()
            self._draw_voice()
            self._draw_stats_bar()

            pygame.display.flip()
            self.clock.tick(30)

        self.security.stop()
        if self.vision:
            self.vision.release()
        pygame.quit()

    def _run_api(self):
        try:
            from .api_server import create_api
            app = create_api(self)
            app.run(host="0.0.0.0", port=5000, threaded=True)
        except Exception as e:
            print(f"[API] Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="TokioAI Entity")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--api", action="store_true")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--register", type=str, help="name:role")
    args = parser.parse_args()

    os.environ.setdefault("SDL_VIDEODRIVER", "wayland")
    os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")

    app = TokioEntity(
        fullscreen=not args.windowed,
        demo_mode=args.demo,
        start_api=args.api,
    )

    if args.register:
        parts = args.register.split(":")
        app.start_register(parts[0], parts[1] if len(parts) > 1 else "visitor")

    import signal
    signal.signal(signal.SIGTERM, lambda *_: setattr(app, '_running', False))
    signal.signal(signal.SIGINT, lambda *_: setattr(app, '_running', False))
    app.run()


if __name__ == "__main__":
    main()
