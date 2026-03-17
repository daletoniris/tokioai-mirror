"""
TokioAI Face — Full-screen animated cyberpunk face.

The face scales to fill whatever dimensions are given.
Eyes are large rectangular displays, not small circles.
Angular/hexagonal design with circuit-board patterns and dramatic glow.
"""
from __future__ import annotations

import math
import random
import time
from enum import Enum

import pygame


class Emotion(Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    ALERT = "alert"
    SCANNING = "scanning"
    ANGRY = "angry"
    CURIOUS = "curious"
    SLEEPING = "sleeping"
    THINKING = "thinking"
    EXCITED = "excited"


EMOTION_COLORS = {
    Emotion.NEUTRAL:  {"face": (0, 200, 255),   "eye": (0, 255, 220),   "glow": (0, 80, 130),   "iris": (0, 180, 255)},
    Emotion.HAPPY:    {"face": (0, 255, 120),    "eye": (100, 255, 80),  "glow": (0, 120, 50),   "iris": (50, 255, 100)},
    Emotion.ALERT:    {"face": (255, 200, 0),    "eye": (255, 240, 0),   "glow": (130, 90, 0),   "iris": (255, 200, 0)},
    Emotion.SCANNING: {"face": (0, 150, 255),    "eye": (0, 200, 255),   "glow": (0, 60, 150),   "iris": (0, 160, 255)},
    Emotion.ANGRY:    {"face": (255, 50, 50),    "eye": (255, 20, 20),   "glow": (130, 0, 0),    "iris": (255, 0, 0)},
    Emotion.CURIOUS:  {"face": (180, 100, 255),  "eye": (200, 150, 255), "glow": (80, 40, 130),  "iris": (180, 120, 255)},
    Emotion.SLEEPING: {"face": (40, 40, 80),     "eye": (30, 30, 60),    "glow": (15, 15, 40),   "iris": (40, 40, 80)},
    Emotion.THINKING: {"face": (100, 180, 255),  "eye": (80, 160, 255),  "glow": (40, 70, 130),  "iris": (100, 180, 255)},
    Emotion.EXCITED:  {"face": (255, 100, 255),  "eye": (255, 150, 220), "glow": (130, 40, 130), "iris": (255, 100, 200)},
}

EMOTION_MESSAGES = {
    Emotion.NEUTRAL: ["Monitoring...", "All clear", "Standing by"],
    Emotion.HAPPY: ["Nice!", "Looking good!", "All systems go"],
    Emotion.ALERT: ["THREAT!", "Attention!", "Alert!"],
    Emotion.SCANNING: ["Scanning...", "Analyzing...", "Processing..."],
    Emotion.ANGRY: ["Intruder!", "Hostile!", "Blocking!"],
    Emotion.CURIOUS: ["Interesting...", "What's that?", "Hmm..."],
    Emotion.SLEEPING: ["Zzz...", "Low power", "..."],
    Emotion.THINKING: ["Processing...", "Calculating...", "Hmm..."],
    Emotion.EXCITED: ["Let's go!", "Action!", "Awesome!"],
}


class TokioFace:
    """Full-screen animated cyberpunk face that scales to any size."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.emotion = Emotion.NEUTRAL
        self._target_emotion = Emotion.NEUTRAL

        # Scale factor based on screen size
        self._scale = min(width, height) / 600.0

        # Animation state
        self._blink_timer = time.monotonic()
        self._blink_duration = 0.12
        self._blink_interval = 3.0 + random.random() * 2
        self._is_blinking = False
        self._blink_progress = 0.0

        self._eye_target_x = 0.0
        self._eye_target_y = 0.0
        self._eye_x = 0.0
        self._eye_y = 0.0

        self._mouth_open = 0.0
        self._mouth_target = 0.0

        self._scan_angle = 0.0
        self._pulse = 0.0
        self._message = ""
        self._message_timer = 0.0

        self._particles: list[dict] = []

    def set_emotion(self, emotion: Emotion, message: str = ""):
        self._target_emotion = emotion
        if message:
            self._message = message
        else:
            self._message = random.choice(EMOTION_MESSAGES.get(emotion, [""]))
        self._message_timer = time.monotonic()

    def look_at(self, x: float, y: float):
        self._eye_target_x = max(-1, min(1, x))
        self._eye_target_y = max(-1, min(1, y))

    def speak(self, duration: float = 0.5):
        self._mouth_target = 1.0

    def update(self, dt: float):
        now = time.monotonic()
        self.emotion = self._target_emotion

        # Blink
        if not self._is_blinking and now - self._blink_timer > self._blink_interval:
            self._is_blinking = True
            self._blink_timer = now
            self._blink_interval = 2.5 + random.random() * 3

        if self._is_blinking:
            elapsed = now - self._blink_timer
            if elapsed < self._blink_duration:
                self._blink_progress = elapsed / self._blink_duration
            elif elapsed < self._blink_duration * 2:
                self._blink_progress = 1.0 - (elapsed - self._blink_duration) / self._blink_duration
            else:
                self._is_blinking = False
                self._blink_progress = 0.0

        # Smooth eye movement
        lerp = min(1.0, dt * 8.0)
        self._eye_x += (self._eye_target_x - self._eye_x) * lerp
        self._eye_y += (self._eye_target_y - self._eye_y) * lerp

        # Mouth
        self._mouth_open += (self._mouth_target - self._mouth_open) * min(1, dt * 10)
        if self._mouth_target > 0:
            self._mouth_target -= dt * 3

        # Scanning
        if self.emotion == Emotion.SCANNING:
            self._scan_angle += dt * 3
            self._eye_target_x = math.sin(self._scan_angle) * 0.8
            self._eye_target_y = math.sin(self._scan_angle * 0.7) * 0.3

        # Pulse
        self._pulse = (math.sin(now * 3) + 1) / 2

        # Particles
        self._update_particles(dt)
        if self.emotion in (Emotion.ALERT, Emotion.ANGRY, Emotion.EXCITED):
            if random.random() < 0.4:
                self._spawn_particle()

    def _spawn_particle(self):
        cx, cy = self.width // 2, self.height // 2
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(40, 120) * self._scale
        colors = EMOTION_COLORS[self.emotion]
        self._particles.append({
            "x": cx + random.randint(-80, 80),
            "y": cy + random.randint(-80, 80),
            "vx": math.cos(angle) * speed,
            "vy": math.sin(angle) * speed,
            "life": 1.0,
            "size": random.randint(2, 6),
            "color": colors["glow"],
        })

    def _update_particles(self, dt: float):
        alive = []
        for p in self._particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["life"] -= dt * 1.5
            if p["life"] > 0:
                alive.append(p)
        self._particles = alive[-60:]

    def render(self, surface: pygame.Surface, x_offset: int = 0, y_offset: int = 0):
        colors = EMOTION_COLORS[self.emotion]
        s = self._scale
        cx = x_offset + self.width // 2
        cy = y_offset + self.height // 2 - int(30 * s)  # slightly above center

        # =====================================================================
        # BACKGROUND GLOW — large atmospheric glow
        # =====================================================================
        glow_r = int(220 * s + self._pulse * 30 * s)
        glow_surf = pygame.Surface((glow_r * 2, glow_r * 2), pygame.SRCALPHA)
        for r in range(glow_r, 0, -4):
            alpha = int(25 * (r / glow_r))
            color = (*colors["glow"], alpha)
            pygame.draw.circle(glow_surf, color, (glow_r, glow_r), r)
        surface.blit(glow_surf, (cx - glow_r, cy - glow_r))

        # =====================================================================
        # OUTER FRAME — hexagonal/angular face frame
        # =====================================================================
        frame_w = int(280 * s)
        frame_h = int(240 * s)

        # Hexagonal shape points
        hex_points = [
            (cx - frame_w, cy - int(frame_h * 0.3)),    # left mid-top
            (cx - int(frame_w * 0.7), cy - frame_h),    # top-left
            (cx + int(frame_w * 0.7), cy - frame_h),    # top-right
            (cx + frame_w, cy - int(frame_h * 0.3)),    # right mid-top
            (cx + frame_w, cy + int(frame_h * 0.6)),    # right mid-bottom
            (cx + int(frame_w * 0.6), cy + frame_h),    # bottom-right
            (cx - int(frame_w * 0.6), cy + frame_h),    # bottom-left
            (cx - frame_w, cy + int(frame_h * 0.6)),    # left mid-bottom
        ]
        pygame.draw.polygon(surface, colors["glow"], hex_points, 2)

        # Inner frame
        inner_s = 0.88
        inner_points = [(int(cx + (px - cx) * inner_s), int(cy + (py - cy) * inner_s))
                        for px, py in hex_points]
        pygame.draw.polygon(surface, colors["face"], inner_points, 1)

        # Circuit lines from frame
        for i, (px, py) in enumerate(hex_points):
            ex = px + int(math.cos(i * math.pi / 4) * 25 * s)
            ey = py + int(math.sin(i * math.pi / 4) * 25 * s)
            pygame.draw.line(surface, colors["glow"], (px, py), (ex, ey), 1)

        # =====================================================================
        # EYES — large rectangular displays
        # =====================================================================
        eye_spacing = int(90 * s)
        eye_y = cy - int(20 * s)
        eye_w = int(80 * s)   # wide rectangular eyes
        eye_h = int(50 * s)

        blink = 1.0 - self._blink_progress
        if self.emotion == Emotion.SLEEPING:
            blink = 0.08
        elif self.emotion == Emotion.ANGRY:
            blink = 0.45

        actual_h = max(int(3 * s), int(eye_h * blink))

        for side in (-1, 1):
            ex = cx + side * eye_spacing + int(self._eye_x * 15 * s)
            ey = eye_y + int(self._eye_y * 10 * s)

            eye_rect = pygame.Rect(
                ex - eye_w // 2, ey - actual_h // 2,
                eye_w, actual_h
            )

            # Eye background (dark)
            pygame.draw.rect(surface, (0, 5, 15), eye_rect, border_radius=int(6 * s))

            # Eye glow fill
            glow_rect = eye_rect.inflate(-int(4 * s), -int(4 * s))
            if glow_rect.height > 2:
                pygame.draw.rect(surface, colors["eye"], glow_rect, border_radius=int(4 * s))

            # Eye border
            pygame.draw.rect(surface, colors["face"], eye_rect, int(2 * s), border_radius=int(6 * s))

            # Pupil / iris
            if actual_h > int(10 * s):
                pupil_x = ex + int(self._eye_x * 12 * s)
                pupil_y = ey + int(self._eye_y * 8 * s)

                # Iris (larger)
                iris_w = int(28 * s)
                iris_h = min(int(22 * s), actual_h - int(8 * s))
                if iris_h > 4:
                    iris_rect = pygame.Rect(
                        pupil_x - iris_w // 2, pupil_y - iris_h // 2,
                        iris_w, iris_h
                    )
                    pygame.draw.ellipse(surface, (0, 8, 20), iris_rect)
                    pygame.draw.ellipse(surface, colors["iris"], iris_rect, int(2 * s))

                    # Pupil (center dot)
                    pupil_r = int(6 * s)
                    pygame.draw.circle(surface, (0, 0, 0), (pupil_x, pupil_y), pupil_r)

                    # Highlight
                    hl_x = pupil_x - int(4 * s)
                    hl_y = pupil_y - int(4 * s)
                    pygame.draw.circle(surface, (255, 255, 255), (hl_x, hl_y), int(3 * s))

            # Eyebrow for angry
            if self.emotion == Emotion.ANGRY:
                brow_y = ey - actual_h // 2 - int(12 * s)
                pygame.draw.line(surface, colors["face"],
                                 (ex - eye_w // 2 - int(5*s), brow_y - side * int(8*s)),
                                 (ex + eye_w // 2 + int(5*s), brow_y + side * int(8*s)),
                                 int(4 * s))
            elif self.emotion == Emotion.CURIOUS and side == 1:
                brow_y = ey - actual_h // 2 - int(14 * s)
                pygame.draw.line(surface, colors["face"],
                                 (ex - eye_w // 2, brow_y + int(5*s)),
                                 (ex + eye_w // 2, brow_y - int(5*s)),
                                 int(3 * s))

            # Eye corner accents
            corner_len = int(8 * s)
            for (ccx, ccy) in [(eye_rect.left, eye_rect.top), (eye_rect.right, eye_rect.top),
                                (eye_rect.left, eye_rect.bottom), (eye_rect.right, eye_rect.bottom)]:
                dx = 1 if ccx == eye_rect.left else -1
                dy = 1 if ccy == eye_rect.top else -1
                pygame.draw.line(surface, colors["face"],
                                 (ccx, ccy), (ccx + dx * corner_len, ccy), int(2 * s))
                pygame.draw.line(surface, colors["face"],
                                 (ccx, ccy), (ccx, ccy + dy * corner_len), int(2 * s))

        # =====================================================================
        # MOUTH
        # =====================================================================
        mouth_y = cy + int(60 * s)
        mouth_w = int(50 * s)

        if self.emotion in (Emotion.HAPPY, Emotion.EXCITED):
            # Big smile arc
            mouth_rect = pygame.Rect(cx - mouth_w, mouth_y - int(15*s),
                                     mouth_w * 2, int(35*s))
            pygame.draw.arc(surface, colors["face"], mouth_rect,
                            math.pi + 0.2, 2 * math.pi - 0.2, int(3 * s))
            if self.emotion == Emotion.EXCITED:
                # Extra wide
                pygame.draw.arc(surface, colors["eye"], mouth_rect.inflate(int(10*s), int(5*s)),
                                math.pi + 0.3, 2 * math.pi - 0.3, int(2 * s))
        elif self.emotion == Emotion.ANGRY:
            mouth_rect = pygame.Rect(cx - mouth_w, mouth_y + int(5*s),
                                     mouth_w * 2, int(25*s))
            pygame.draw.arc(surface, colors["face"], mouth_rect,
                            0.3, math.pi - 0.3, int(3 * s))
        elif self.emotion == Emotion.SLEEPING:
            points = [(cx - int(20*s), mouth_y), (cx - int(7*s), mouth_y - int(4*s)),
                      (cx + int(7*s), mouth_y + int(4*s)), (cx + int(20*s), mouth_y)]
            pygame.draw.lines(surface, colors["face"], False, points, int(2 * s))
        else:
            # Neutral / talking
            open_h = int(self._mouth_open * 18 * s)
            if open_h > int(3 * s):
                pygame.draw.ellipse(surface, colors["face"],
                                    (cx - int(16*s), mouth_y - open_h // 2,
                                     int(32*s), open_h), int(2 * s))
            else:
                pygame.draw.line(surface, colors["face"],
                                 (cx - mouth_w, mouth_y),
                                 (cx + mouth_w, mouth_y), int(2 * s))

        # =====================================================================
        # SCAN LINE (scanning mode)
        # =====================================================================
        if self.emotion == Emotion.SCANNING:
            scan_progress = (self._scan_angle % math.pi) / math.pi
            scan_y = int(cy - frame_h + scan_progress * frame_h * 2)
            scan_y = max(cy - frame_h, min(cy + frame_h, scan_y))
            line_surf = pygame.Surface((frame_w * 2, int(3 * s)), pygame.SRCALPHA)
            line_surf.fill((*colors["eye"], 120))
            surface.blit(line_surf, (cx - frame_w, scan_y))

        # =====================================================================
        # DECORATIVE CIRCUIT LINES
        # =====================================================================
        for angle_deg in range(0, 360, 30):
            angle_rad = math.radians(angle_deg + self._pulse * 10)
            r1 = int((frame_h + 10) * s * 0.95)
            r2 = int((frame_h + 10) * s * 0.95 + (10 + self._pulse * 8) * s)
            x1 = cx + int(math.cos(angle_rad) * r1)
            y1 = cy + int(math.sin(angle_rad) * r1)
            x2 = cx + int(math.cos(angle_rad) * r2)
            y2 = cy + int(math.sin(angle_rad) * r2)
            pygame.draw.line(surface, colors["glow"], (x1, y1), (x2, y2), 1)

        # =====================================================================
        # PARTICLES
        # =====================================================================
        for p in self._particles:
            px, py = int(p["x"]), int(p["y"])
            r, g, b = p["color"]
            sz = max(1, int(p["size"] * p["life"]))
            pygame.draw.circle(surface, (min(255, r+60), min(255, g+60), min(255, b+60)),
                               (px, py), sz)

        # =====================================================================
        # STATUS INDICATOR (top center)
        # =====================================================================
        now = time.monotonic()
        ind_color = colors["eye"] if int(now * 2) % 2 == 0 else colors["glow"]
        ind_y = cy - frame_h - int(15 * s)
        pygame.draw.circle(surface, ind_color, (cx, ind_y), int(5 * s))
        # Small text above
        if self._message and now - self._message_timer < 5:
            font = pygame.font.SysFont("monospace", max(12, int(14 * s)), bold=True)
            text_surf = font.render(self._message, True, colors["face"])
            text_rect = text_surf.get_rect(centerx=cx, bottom=ind_y - int(8 * s))
            surface.blit(text_surf, text_rect)
