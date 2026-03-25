"""
TokioAI Face — Cosmic Spiral Entity.

A living digital face built from cosmic geometry: Fibonacci spirals,
rotating concentric rings (hexagon, circles, square), orbiting particles,
nebula glow, and portal-like eyes with spinning data rings.

Inspired by the TokioAI spiral logo — galaxies, sacred geometry, cosmos.
Every element breathes, pulses, and reacts. The face feels ALIVE.
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
    Emotion.NEUTRAL:  {"primary": (0, 255, 200),   "secondary": (0, 212, 255), "glow": (0, 60, 120),  "accent": (123, 94, 167),  "bg": (10, 10, 15)},
    Emotion.HAPPY:    {"primary": (0, 255, 160),    "secondary": (0, 220, 130), "glow": (0, 80, 50),   "accent": (100, 255, 180), "bg": (0, 12, 8)},
    Emotion.ALERT:    {"primary": (255, 200, 0),    "secondary": (255, 150, 0), "glow": (100, 70, 0),  "accent": (255, 230, 80),  "bg": (15, 10, 0)},
    Emotion.SCANNING: {"primary": (0, 212, 255),    "secondary": (0, 160, 220), "glow": (0, 40, 120),  "accent": (60, 180, 255),  "bg": (0, 5, 18)},
    Emotion.ANGRY:    {"primary": (255, 30, 30),    "secondary": (200, 0, 0),   "glow": (100, 0, 0),   "accent": (255, 80, 80),   "bg": (18, 0, 0)},
    Emotion.CURIOUS:  {"primary": (200, 120, 255),  "secondary": (150, 80, 220),"glow": (60, 30, 100), "accent": (220, 160, 255), "bg": (8, 0, 15)},
    Emotion.SLEEPING: {"primary": (30, 30, 60),     "secondary": (20, 20, 40),  "glow": (10, 10, 25),  "accent": (40, 40, 80),    "bg": (2, 2, 5)},
    Emotion.THINKING: {"primary": (80, 180, 255),   "secondary": (40, 120, 200),"glow": (20, 50, 100), "accent": (120, 200, 255), "bg": (0, 6, 16)},
    Emotion.EXCITED:  {"primary": (255, 107, 157),  "secondary": (200, 60, 180),"glow": (80, 20, 60),  "accent": (255, 120, 230), "bg": (12, 0, 10)},
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


def _lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


class TokioFace:
    """Cosmic spiral entity face — alive, breathing, reactive."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.emotion = Emotion.NEUTRAL
        self._target_emotion = Emotion.NEUTRAL
        self._emotion_blend = 1.0
        self._prev_emotion = Emotion.NEUTRAL

        self._scale = min(width, height) / 600.0

        # Timing
        self._time = 0.0
        self._dt = 0.0

        # Blink
        self._blink_timer = time.monotonic()
        self._blink_interval = 3.0 + random.random() * 2
        self._is_blinking = False
        self._blink_progress = 0.0

        # Eye tracking (spring physics)
        self._eye_target_x = 0.0
        self._eye_target_y = 0.0
        self._eye_x = 0.0
        self._eye_y = 0.0
        self._eye_vx = 0.0
        self._eye_vy = 0.0

        # Mouth
        self._mouth_open = 0.0
        self._mouth_target = 0.0

        # Spiral geometry points
        self._spiral_points: list[dict] = []
        self._ring_points: list[dict] = []
        self._orbit_dots: list[dict] = []
        self._init_cosmic_geometry()

        # Floating ambient particles (stardust)
        self._ambient: list[dict] = []

        # Energy arcs (cosmic lightning)
        self._arcs: list[list[tuple]] = []
        self._arc_timer = 0.0

        # Scanline
        self._scan_y = 0.0

        # Glitch
        self._glitch_time = 0.0
        self._glitch_intensity = 0.0

        # Pulse / breathing
        self._pulse = 0.0
        self._breath = 0.0

        # Ring rotations (different speeds like website)
        self._ring_rot1 = 0.0   # hexagon, 8s CW
        self._ring_rot2 = 0.0   # circle, 12s CCW
        self._ring_rot3 = 0.0   # square, 6s CW
        self._ring_rot4 = 0.0   # inner circle, 16s CCW
        self._ring_rot5 = 0.0   # heptagon, 10s CW
        self._spiral_rot = 0.0  # spiral path, 5s CW

        # Iris rings rotation (for eyes)
        self._iris_rot = 0.0
        self._iris_rot2 = 0.0

        # HUD
        self.hud_detections = 0
        self.hud_fps = 0.0
        self.hud_threats = 0
        self.hud_persons = 0

        self._message = ""
        self._message_timer = 0.0

        # Fonts
        self._fonts_ready = False
        self._font_hud = None
        self._font_emo = None
        self._font_msg = None
        self._font_data = None

    def _ensure_fonts(self):
        if self._fonts_ready:
            return
        s = self._scale
        self._font_hud = pygame.font.SysFont("monospace", max(11, int(12 * s)), bold=True)
        self._font_emo = pygame.font.SysFont("monospace", max(14, int(15 * s)), bold=True)
        self._font_msg = pygame.font.SysFont("monospace", max(13, int(14 * s)), bold=True)
        self._font_data = pygame.font.SysFont("monospace", max(9, int(10 * s)))
        self._fonts_ready = True

    def _init_cosmic_geometry(self):
        """Generate points for spiral, rings, and orbital dots."""
        s = self._scale
        cx, cy = self.width // 2 + int(100 * s), self.height // 2 + int(330 * s)

        # --- Fibonacci spiral ---
        # Logarithmic spiral: r = a * e^(b*theta)
        spiral_pts = []
        a = 8.0 * s
        b = 0.12
        for i in range(200):
            theta = i * 0.15
            r = a * math.exp(b * theta)
            if r > 230 * s:
                break
            px = cx + math.cos(theta) * r
            py = cy + math.sin(theta) * r
            spiral_pts.append({
                "base_x": px, "base_y": py,
                "x": px, "y": py,
                "theta": theta, "r": r,
                "phase": random.random() * math.pi * 2,
                "speed": random.uniform(0.5, 1.5),
                "drift": random.uniform(1.0, 3.0) * s,
                "size": max(1.0, (1.0 + theta * 0.02) * s),
            })
        self._spiral_points = spiral_pts

        # --- Concentric geometric rings ---
        ring_pts = []

        # Ring 1: Hexagon (6 sides) — outermost
        r_hex = 210 * s
        for i in range(6):
            angle = i * (math.pi * 2 / 6)
            px = cx + math.cos(angle) * r_hex
            py = cy + math.sin(angle) * r_hex
            ring_pts.append({
                "base_x": px, "base_y": py, "x": px, "y": py,
                "ring": 1, "index": i, "total": 6, "radius": r_hex,
                "phase": random.random() * math.pi * 2,
                "size": 2.5 * s,
            })

        # Ring 2: Circle — dashed, r=155
        r_circ = 155 * s
        for i in range(24):
            angle = i * (math.pi * 2 / 24)
            px = cx + math.cos(angle) * r_circ
            py = cy + math.sin(angle) * r_circ
            ring_pts.append({
                "base_x": px, "base_y": py, "x": px, "y": py,
                "ring": 2, "index": i, "total": 24, "radius": r_circ,
                "phase": random.random() * math.pi * 2,
                "size": 1.5 * s,
            })

        # Ring 3: Square (4 sides, rotated 45deg) — r=130
        r_sq = 130 * s
        for i in range(4):
            angle = i * (math.pi / 2) + math.pi / 4
            px = cx + math.cos(angle) * r_sq
            py = cy + math.sin(angle) * r_sq
            ring_pts.append({
                "base_x": px, "base_y": py, "x": px, "y": py,
                "ring": 3, "index": i, "total": 4, "radius": r_sq,
                "phase": random.random() * math.pi * 2,
                "size": 2.0 * s,
            })

        # Ring 4: Inner circle — r=118
        r_inner = 118 * s
        for i in range(16):
            angle = i * (math.pi * 2 / 16)
            px = cx + math.cos(angle) * r_inner
            py = cy + math.sin(angle) * r_inner
            ring_pts.append({
                "base_x": px, "base_y": py, "x": px, "y": py,
                "ring": 4, "index": i, "total": 16, "radius": r_inner,
                "phase": random.random() * math.pi * 2,
                "size": 1.2 * s,
            })

        # Ring 5: Heptagon (7 sides) — r=100
        r_hept = 100 * s
        for i in range(7):
            angle = i * (math.pi * 2 / 7)
            px = cx + math.cos(angle) * r_hept
            py = cy + math.sin(angle) * r_hept
            ring_pts.append({
                "base_x": px, "base_y": py, "x": px, "y": py,
                "ring": 5, "index": i, "total": 7, "radius": r_hept,
                "phase": random.random() * math.pi * 2,
                "size": 1.8 * s,
            })

        self._ring_points = ring_pts

        # --- Orbiting dots ---
        self._orbit_dots = [
            {"radius": 210 * s, "speed": 1.57, "size": 3.5 * s, "angle": 0.0,
             "color_key": "primary"},      # cyan dot on hex ring
            {"radius": 155 * s, "speed": -1.05, "size": 2.5 * s, "angle": math.pi / 2,
             "color_key": "accent"},       # purple dot on circle
            {"radius": 100 * s, "speed": 0.7, "size": 2.0 * s, "angle": math.pi,
             "color_key": "secondary"},    # blue dot on inner
        ]

    def set_emotion(self, emotion: Emotion, message: str = ""):
        if emotion != self._target_emotion:
            self._prev_emotion = self.emotion
            self._target_emotion = emotion
            self._emotion_blend = 0.0
            self._glitch_time = time.monotonic()
            self._glitch_intensity = 1.0
            # Scatter spiral particles on emotion change
            for p in self._spiral_points:
                p["drift"] *= 4.0
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
        self._time = now
        self._dt = dt

        # Emotion blend
        if self._emotion_blend < 1.0:
            self._emotion_blend = min(1.0, self._emotion_blend + dt * 3.0)
            if self._emotion_blend >= 1.0:
                self.emotion = self._target_emotion
        else:
            self.emotion = self._target_emotion

        # Breathing / pulse
        self._pulse = (math.sin(now * 2.5) + 1) / 2
        self._breath = (math.sin(now * 1.2) + 1) / 2

        # Blink
        if not self._is_blinking and now - self._blink_timer > self._blink_interval:
            self._is_blinking = True
            self._blink_timer = now
            self._blink_interval = 2.5 + random.random() * 3
        if self._is_blinking:
            elapsed = now - self._blink_timer
            dur = 0.12
            if elapsed < dur:
                self._blink_progress = elapsed / dur
            elif elapsed < dur * 2:
                self._blink_progress = 1.0 - (elapsed - dur) / dur
            else:
                self._is_blinking = False
                self._blink_progress = 0.0

        # Spring eye tracking — snappy, near real-time
        spring, damping = 60.0, 14.0
        self._eye_vx += (spring * (self._eye_target_x - self._eye_x) - damping * self._eye_vx) * dt
        self._eye_vy += (spring * (self._eye_target_y - self._eye_y) - damping * self._eye_vy) * dt
        self._eye_x += self._eye_vx * dt
        self._eye_y += self._eye_vy * dt

        # Mouth
        self._mouth_open += (self._mouth_target - self._mouth_open) * min(1, dt * 10)
        if self._mouth_target > 0:
            self._mouth_target -= dt * 3

        # Scanning behavior
        if self.emotion == Emotion.SCANNING:
            self._eye_target_x = math.sin(now * 3) * 0.8
            self._eye_target_y = math.sin(now * 2.1) * 0.3

        # Ring rotations — matching website speeds (converted to radians/sec)
        # Website: ring-1 8s CW, ring-2 12s CCW, ring-3 6s CW, ring-4 16s CCW, ring-5 10s CW
        self._ring_rot1 += dt * (2 * math.pi / 8)     # 8s full rotation CW
        self._ring_rot2 -= dt * (2 * math.pi / 12)    # 12s CCW
        self._ring_rot3 += dt * (2 * math.pi / 6)     # 6s CW
        self._ring_rot4 -= dt * (2 * math.pi / 16)    # 16s CCW
        self._ring_rot5 += dt * (2 * math.pi / 10)    # 10s CW
        self._spiral_rot += dt * (2 * math.pi / 5)    # 5s CW

        # Iris rotation (for eyes)
        self._iris_rot += dt * 1.2
        self._iris_rot2 -= dt * 0.8

        # Update orbital dots
        for dot in self._orbit_dots:
            dot["angle"] += dot["speed"] * dt

        # Spiral particles — drift and reform
        for p in self._spiral_points:
            p["drift"] = _lerp(p["drift"], random.uniform(1.0, 3.0) * self._scale, dt * 2)
            offset_x = math.sin(now * p["speed"] + p["phase"]) * p["drift"]
            offset_y = math.cos(now * p["speed"] * 0.7 + p["phase"]) * p["drift"]
            # Rotate spiral with spiral_rot
            theta = p["theta"] + self._spiral_rot
            r = p["r"]
            breath_expand = 1.0 + self._breath * 0.02
            scx = self.width / 2
            scy = self.height / 2 - 30 * self._scale
            p["x"] = scx + math.cos(theta) * r * breath_expand + offset_x
            p["y"] = scy + math.sin(theta) * r * breath_expand + offset_y

        # Ambient particles
        if len(self._ambient) < 50 and random.random() < 0.35:
            self._spawn_ambient()
        self._update_ambient(dt)

        # Energy arcs
        self._arc_timer += dt
        if self._arc_timer > 0.18:
            self._arc_timer = 0
            self._arcs = []
            if self.emotion != Emotion.SLEEPING:
                self._generate_arcs()

        # Scanline
        self._scan_y = (self._scan_y + dt * 180) % (self.height + 100)

        # Glitch decay
        if self._glitch_intensity > 0:
            self._glitch_intensity = max(0, self._glitch_intensity - dt * 2.5)

    def _spawn_ambient(self):
        """Spawn stardust particles."""
        s = self._scale
        cx, cy = self.width // 2, self.height // 2
        angle = random.uniform(0, math.pi * 2)
        dist = random.uniform(80, 280) * s
        # Cosmic colors — cyan, purple, blue, pink
        color_choices = [
            (0, 255, 200),    # cyan
            (123, 94, 167),   # purple
            (0, 212, 255),    # blue
            (255, 107, 157),  # pink
        ]
        chosen = random.choice(color_choices)
        self._ambient.append({
            "x": cx + math.cos(angle) * dist,
            "y": cy + math.sin(angle) * dist,
            "vx": random.uniform(-15, 15) * s,
            "vy": random.uniform(-25, -3) * s,
            "life": 1.0,
            "size": random.uniform(0.8, 2.5) * s,
            "color": chosen,
        })

    def _update_ambient(self, dt):
        alive = []
        for p in self._ambient:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["life"] -= dt * 0.35
            if p["life"] > 0:
                alive.append(p)
        self._ambient = alive

    def _generate_arcs(self):
        """Generate cosmic energy arcs between rings."""
        s = self._scale
        cx = self.width // 2
        cy = self.height // 2 - int(30 * s)

        # Arc along the spiral
        if random.random() < 0.5 and len(self._spiral_points) > 20:
            start_idx = random.randint(0, len(self._spiral_points) - 15)
            arc = []
            for i in range(start_idx, min(start_idx + 8, len(self._spiral_points))):
                p = self._spiral_points[i]
                arc.append((int(p["x"] + random.uniform(-4, 4) * s),
                            int(p["y"] + random.uniform(-4, 4) * s)))
            if len(arc) > 1:
                self._arcs.append(arc)

        # Random arcs from ring points to center
        if random.random() < 0.3:
            pts = [p for p in self._ring_points if p["ring"] in (1, 2)]
            if pts:
                p = random.choice(pts)
                arc = [(int(p["x"]), int(p["y"]))]
                dx = (cx - p["x"]) / 5
                dy = (cy - p["y"]) / 5
                for j in range(4):
                    last = arc[-1]
                    arc.append((int(last[0] + dx + random.randint(-12, 12)),
                                int(last[1] + dy + random.randint(-12, 12))))
                self._arcs.append(arc)

    def _get_colors(self):
        if self._emotion_blend >= 1.0:
            return EMOTION_COLORS[self.emotion]
        c_prev = EMOTION_COLORS[self._prev_emotion]
        c_next = EMOTION_COLORS[self._target_emotion]
        t = self._emotion_blend
        return {k: _lerp_color(c_prev[k], c_next[k], t) for k in c_prev}

    def render(self, surface: pygame.Surface, x_offset: int = 0, y_offset: int = 0):
        self._ensure_fonts()
        colors = self._get_colors()
        s = self._scale
        if not (0.01 < s < 100.0) or not math.isfinite(s):
            s = 1.0
        cx = x_offset + self.width // 2 + int(100 * s)
        cy = y_offset + self.height // 2 + int(200 * s)

        # Layer 1: Nebula background glow
        try:
            self._render_nebula_glow(surface, cx, cy, colors, s)
        except (TypeError, ValueError, OverflowError):
            pass
        # Layer 2: Stardust particles
        self._render_ambient(surface, colors)
        # Layer 3: Rotating geometric rings
        self._render_rings(surface, cx, cy, colors, s)
        # Layer 4: Fibonacci spiral
        self._render_spiral(surface, cx, cy, colors, s)
        # Layer 5: Orbiting dots
        self._render_orbit_dots(surface, cx, cy, colors, s)
        # Layer 6: Core triangle + center
        self._render_core(surface, cx, cy, colors, s)
        # Layer 7: Energy arcs
        self._render_arcs(surface, colors, s)
        # Layer 8: Eyes — the main attraction
        try:
            self._render_eyes(surface, cx, cy, colors, s)
        except (TypeError, ValueError, OverflowError):
            pass
        # Layer 9: Mouth
        try:
            self._render_mouth(surface, cx, cy, colors, s)
        except (TypeError, ValueError, OverflowError):
            pass
        # Layer 10: Scanline
        self._render_scanline(surface, colors, s)
        # Layer 11: Glitch
        self._render_glitch(surface, cx, cy, colors, s)
        # Layer 12: HUD data
        self._render_hud(surface, cx, cy, colors, s)
        # Layer 13: Status
        self._render_status(surface, cx, cy, colors, s)

    def _render_nebula_glow(self, surface, cx, cy, colors, s):
        """Deep cosmic nebula glow — concentric colored rings."""
        # Outer nebula — purple/blue
        for i in range(6):
            r = int((220 - i * 30) * s)
            if r > 0:
                bright = max(3, int(10 * (1 - i / 6) * (0.5 + self._pulse * 0.5)))
                # Alternate between cosmic colors
                if i % 3 == 0:
                    glow_c = tuple(max(0, min(255, c * bright // 80)) for c in (123, 94, 167))
                elif i % 3 == 1:
                    glow_c = tuple(max(0, min(255, c * bright // 80)) for c in (0, 212, 255))
                else:
                    glow_c = tuple(max(0, min(255, c * bright // 80)) for c in colors["glow"])
                pygame.draw.circle(surface, glow_c, (int(cx), int(cy)), r, max(1, int(8 * s)))

        # Eye glow — bright primary color circles behind eyes
        eye_spacing = int(100 * s)
        eye_y = int(cy - int(25 * s))
        for side in (-1, 1):
            ex = int(cx + side * eye_spacing)
            for i in range(3):
                r = int((60 - i * 18) * s)
                if r > 0:
                    bright = max(5, int(12 * (1 - i / 3)))
                    ec = tuple(max(0, min(255, c * bright // 100)) for c in colors["primary"])
                    pygame.draw.circle(surface, ec, (int(ex), int(eye_y)), r, max(1, int(5 * s)))

    def _render_ambient(self, surface, colors):
        """Floating stardust particles."""
        for p in self._ambient:
            sz = max(1, int(p["size"] * p["life"]))
            bright_f = p["life"]
            color = tuple(min(255, int(c * bright_f)) for c in p["color"])
            pygame.draw.circle(surface, color, (int(p["x"]), int(p["y"])), sz)

    def _render_rings(self, surface, cx, cy, colors, s):
        """Draw rotating concentric geometric rings — like the website logo."""
        now = self._time
        ring_rotations = {
            1: self._ring_rot1,  # hexagon
            2: self._ring_rot2,  # outer circle
            3: self._ring_rot3,  # square
            4: self._ring_rot4,  # inner circle
            5: self._ring_rot5,  # heptagon
        }

        # Ring visual styles
        ring_colors = {
            1: colors["primary"],    # cyan hexagon
            2: colors["accent"],     # purple circle
            3: colors["secondary"],  # blue square
            4: (255, 107, 157),      # pink inner circle
            5: colors["primary"],    # cyan heptagon
        }

        ring_dash = {
            1: (8, 4),    # hexagon dashes
            2: (3, 9),    # circle sparse dashes
            3: (5, 6),    # square dashes
            4: (2, 6),    # inner sparse
            5: (4, 5),    # heptagon dashes
        }

        ring_opacity = {1: 0.4, 2: 0.6, 3: 0.35, 4: 0.4, 5: 0.3}

        # Group points by ring
        rings: dict[int, list] = {}
        for p in self._ring_points:
            rings.setdefault(p["ring"], []).append(p)

        for ring_id, points in rings.items():
            rot = ring_rotations.get(ring_id, 0)
            base_color = ring_colors.get(ring_id, colors["primary"])
            opacity = ring_opacity.get(ring_id, 0.4)

            # Calculate rotated positions
            rotated = []
            for p in points:
                # Rotate around center
                dx = p["base_x"] - cx
                dy = p["base_y"] - cy
                cos_r = math.cos(rot)
                sin_r = math.sin(rot)
                rx = cx + dx * cos_r - dy * sin_r
                ry = cy + dx * sin_r + dy * cos_r
                p["x"] = rx
                p["y"] = ry
                rotated.append((int(rx), int(ry)))

            # Draw the ring shape (connect vertices)
            dim = max(1, opacity)
            line_color = tuple(max(0, min(255, int(c * opacity))) for c in base_color)
            line_width = max(1, int(1.2 * s))

            if len(rotated) >= 2:
                # Draw dashed lines between consecutive points
                for i in range(len(rotated)):
                    p1 = rotated[i]
                    p2 = rotated[(i + 1) % len(rotated)]
                    self._draw_dashed_line(surface, line_color, p1, p2,
                                          line_width, ring_dash.get(ring_id, (4, 4)))

            # Draw small dots at vertices
            dot_color = tuple(min(255, int(c * 0.7)) for c in base_color)
            for pt in rotated:
                sz = max(1, int(points[0]["size"]))
                if 0 <= pt[0] < self.width and 0 <= pt[1] < self.height:
                    pygame.draw.circle(surface, dot_color, pt, sz)

    def _draw_dashed_line(self, surface, color, start, end, width, pattern):
        """Draw a dashed line between two points."""
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.sqrt(dx * dx + dy * dy)
        if length < 2:
            return

        dash_len, gap_len = pattern
        total = dash_len + gap_len
        nx, ny = dx / length, dy / length
        pos = 0
        drawing = True

        while pos < length:
            seg = dash_len if drawing else gap_len
            seg = min(seg, length - pos)
            if drawing and seg > 0:
                sx = int(start[0] + nx * pos)
                sy = int(start[1] + ny * pos)
                ex = int(start[0] + nx * (pos + seg))
                ey = int(start[1] + ny * (pos + seg))
                pygame.draw.line(surface, color, (sx, sy), (ex, ey), width)
            pos += seg
            drawing = not drawing

    def _render_spiral(self, surface, cx, cy, colors, s):
        """Draw the Fibonacci spiral — the signature element."""
        if len(self._spiral_points) < 2:
            return

        now = self._time
        pts = self._spiral_points

        # Draw spiral path as connected segments with gradient
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]

            x1, y1 = int(p1["x"]), int(p1["y"])
            x2, y2 = int(p2["x"]), int(p2["y"])

            # Gradient along spiral: cyan -> blue -> purple
            t = i / max(1, len(pts) - 1)
            if t < 0.5:
                # Cyan to blue
                t2 = t * 2
                r = int(0 * (1 - t2) + 0 * t2)
                g = int(255 * (1 - t2) + 212 * t2)
                b = int(200 * (1 - t2) + 255 * t2)
            else:
                # Blue to purple
                t2 = (t - 0.5) * 2
                r = int(0 * (1 - t2) + 123 * t2)
                g = int(212 * (1 - t2) + 94 * t2)
                b = int(255 * (1 - t2) + 167 * t2)

            # Pulse brightness
            bright = 0.6 + self._pulse * 0.4
            r = min(255, int(r * bright))
            g = min(255, int(g * bright))
            b = min(255, int(b * bright))

            # Bounds check
            if (0 <= x1 < self.width and 0 <= y1 < self.height and
                    0 <= x2 < self.width and 0 <= y2 < self.height):
                pygame.draw.line(surface, (r, g, b), (x1, y1), (x2, y2),
                                max(1, int(2 * s)))

        # Draw dots at some spiral points for particle effect
        for i, p in enumerate(pts):
            if i % 3 != 0:
                continue
            x, y = int(p["x"]), int(p["y"])
            if not (0 <= x < self.width and 0 <= y < self.height):
                continue
            sz = max(1, int(p["size"]))
            # Holographic color shift
            hue_shift = math.sin(now * 0.8 + p["phase"]) * 0.3
            r = max(0, min(255, int(colors["primary"][0] * (1 + hue_shift))))
            g = max(0, min(255, int(colors["primary"][1] * (1 + hue_shift * 0.5))))
            b = max(0, min(255, int(colors["primary"][2] * (1 - hue_shift * 0.3))))
            pygame.draw.circle(surface, (r, g, b), (x, y), sz)

    def _render_orbit_dots(self, surface, cx, cy, colors, s):
        """Draw orbiting dots — like the website's orbit-dot elements."""
        for dot in self._orbit_dots:
            angle = dot["angle"]
            r = dot["radius"]
            x = int(cx + math.cos(angle) * r)
            y = int(cy + math.sin(angle) * r)
            sz = max(2, int(dot["size"]))

            color = colors.get(dot["color_key"], colors["primary"])

            if 0 <= x < self.width and 0 <= y < self.height:
                # Glow around dot
                glow_sz = sz + int(4 * s)
                glow_c = tuple(max(0, c // 3) for c in color)
                pygame.draw.circle(surface, glow_c, (x, y), glow_sz)
                # Bright dot
                pygame.draw.circle(surface, color, (x, y), sz)
                # White center
                if sz > 2:
                    pygame.draw.circle(surface, (255, 255, 255), (x, y), max(1, sz // 2))

    def _render_core(self, surface, cx, cy, colors, s):
        """Draw the central core — triangle + pulsing center dot."""
        # Core triangle (like website's core element)
        tri_size = int(14 * s)
        tri_pts = [
            (cx, cy - tri_size),                                    # top
            (cx + int(tri_size * 0.87), cy + tri_size // 2),       # bottom right
            (cx - int(tri_size * 0.87), cy + tri_size // 2),       # bottom left
        ]

        # Only draw core if not obscured by eyes (draw it above the eyes area)
        core_y = cy  # center of face
        # The triangle is small enough to sit between/above the eyes

        tri_color = tuple(min(255, int(c * 0.8)) for c in colors["primary"])
        pygame.draw.polygon(surface, tri_color, tri_pts, max(1, int(1.5 * s)))

        # Pulsing center dot
        core_r = max(2, int(5 * s * (0.8 + self._pulse * 0.5)))
        pygame.draw.circle(surface, colors["primary"], (cx, cy), core_r)
        # White inner
        inner_r = max(1, int(2 * s))
        pygame.draw.circle(surface, (255, 255, 255), (cx, cy), inner_r)

    def _render_arcs(self, surface, colors, s):
        """Cosmic energy arcs."""
        for arc in self._arcs:
            if len(arc) < 2:
                continue
            try:
                # Ensure all points are valid int pairs
                safe_arc = [(int(x), int(y)) for x, y in arc]
                arc_color = colors.get("secondary", colors["primary"])
                pygame.draw.lines(surface, arc_color, False, safe_arc, max(1, int(1.5 * s)))
                for p in safe_arc[::2]:
                    pygame.draw.circle(surface, colors["primary"], p, max(1, int(2 * s)))
            except (TypeError, ValueError, OverflowError):
                pass

    def _render_eyes(self, surface, cx, cy, colors, s):
        """Portal-like eyes — deep, layered, with spinning data rings."""
        try:
            cx, cy = int(cx), int(cy)
            s = float(s) if s else 1.0
        except (ValueError, TypeError):
            return
        eye_spacing = int(100 * s)
        eye_y = int(cy - int(25 * s))
        eye_r = int(55 * s)

        blink = 1.0 - self._blink_progress
        if self.emotion == Emotion.SLEEPING:
            blink = 0.05
        elif self.emotion == Emotion.ANGRY:
            blink = 0.5

        for side in (-1, 1):
            ex = int(cx + side * eye_spacing + self._eye_x * 30 * s)
            ey = int(eye_y + self._eye_y * 20 * s)

            actual_r = max(int(3 * s), int(eye_r * blink))

            # Eye socket — dark void
            pygame.draw.circle(surface, (0, 0, 5), (int(ex), int(ey)), actual_r + int(4 * s))

            if actual_r < int(10 * s):
                # Closed eye — just a slit
                pygame.draw.line(surface, colors["primary"],
                                 (ex - eye_r, ey), (ex + eye_r, ey), max(2, int(3 * s)))
                continue

            # Layer 1: Outer glow ring
            pygame.draw.circle(surface, colors["primary"], (int(ex), int(ey)),
                               actual_r + int(2 * s), max(2, int(3 * s)))

            # Layer 2: Outer data ring — spinning segments
            for i in range(16):
                angle = self._iris_rot + i * (math.pi * 2 / 16)
                seg_len = math.pi / 20
                if i % 4 == int(self._time * 4) % 4:
                    color = colors["accent"]
                    width = max(2, int(2.5 * s))
                else:
                    color = colors["secondary"]
                    width = max(1, int(1.5 * s))
                r = actual_r - int(4 * s)
                rect = pygame.Rect(ex - r, ey - r, r * 2, r * 2)
                if r > 5:
                    pygame.draw.arc(surface, color, rect,
                                    angle, angle + seg_len * 3, width)

            # Layer 3: Middle ring — counter-rotating
            mid_r = int(actual_r * 0.7)
            if mid_r > 5:
                for i in range(8):
                    angle = self._iris_rot2 + i * (math.pi / 4)
                    seg_len = math.pi / 12
                    color = colors["primary"] if i % 2 == 0 else colors["glow"]
                    rect = pygame.Rect(ex - mid_r, ey - mid_r, mid_r * 2, mid_r * 2)
                    pygame.draw.arc(surface, color, rect,
                                    angle, angle + seg_len * 2, max(1, int(2 * s)))

                # Connecting spokes
                for i in range(4):
                    angle = self._iris_rot * 0.5 + i * (math.pi / 2)
                    inner = int(mid_r * 0.4)
                    x1 = ex + int(math.cos(angle) * inner)
                    y1 = ey + int(math.sin(angle) * inner)
                    x2 = ex + int(math.cos(angle) * mid_r)
                    y2 = ey + int(math.sin(angle) * mid_r)
                    pygame.draw.line(surface, colors["glow"], (x1, y1), (x2, y2), 1)

            # Layer 4: Inner ring with dots
            inner_r = int(actual_r * 0.4)
            if inner_r > 4:
                pygame.draw.circle(surface, colors["secondary"], (int(ex), int(ey)), inner_r, 1)
                for i in range(6):
                    angle = -self._iris_rot * 2 + i * (math.pi / 3)
                    dx = ex + int(math.cos(angle) * inner_r)
                    dy = ey + int(math.sin(angle) * inner_r)
                    pygame.draw.circle(surface, colors["accent"], (dx, dy),
                                       max(1, int(2.5 * s)))

            # Layer 5: Pupil — the void
            pupil_r = int(actual_r * 0.22)
            pygame.draw.circle(surface, (0, 0, 0), (int(ex), int(ey)), pupil_r)
            pygame.draw.circle(surface, colors["primary"], (int(ex), int(ey)), pupil_r, 1)

            # Highlights — life in the eyes
            hl_x = ex - int(6 * s * (1 + self._eye_x * 0.3))
            hl_y = ey - int(6 * s * (1 + self._eye_y * 0.3))
            pygame.draw.circle(surface, (255, 255, 255), (hl_x, hl_y), max(2, int(4 * s)))
            pygame.draw.circle(surface, (200, 220, 255),
                               (ex + int(4 * s), ey + int(3 * s)), max(1, int(2 * s)))

            # Cross-hair inside eye
            ch = int(actual_r * 0.85)
            ch_color = (*colors["glow"][:3],)
            pygame.draw.line(surface, ch_color, (int(ex - ch), int(ey)), (int(ex + ch), int(ey)), 1)
            pygame.draw.line(surface, ch_color, (int(ex), int(ey - ch)), (int(ex), int(ey + ch)), 1)

            # Eye emotion modifiers
            if self.emotion == Emotion.ANGRY:
                brow_y = ey - actual_r - int(10 * s)
                pygame.draw.line(surface, colors["primary"],
                                 (ex - eye_r - int(5 * s), brow_y - side * int(15 * s)),
                                 (ex + eye_r + int(5 * s), brow_y + side * int(15 * s)),
                                 max(2, int(4 * s)))
            elif self.emotion == Emotion.CURIOUS and side == 1:
                brow_y = ey - actual_r - int(14 * s)
                pygame.draw.line(surface, colors["primary"],
                                 (ex - eye_r, brow_y + int(8 * s)),
                                 (ex + eye_r, brow_y - int(8 * s)),
                                 max(2, int(3 * s)))
            elif self.emotion in (Emotion.HAPPY, Emotion.EXCITED):
                brow_y = ey - actual_r - int(8 * s)
                rect = pygame.Rect(ex - eye_r, brow_y - int(10 * s), eye_r * 2, int(20 * s))
                pygame.draw.arc(surface, colors["primary"], rect,
                                0.3, math.pi - 0.3, max(2, int(3 * s)))

    def _render_mouth(self, surface, cx, cy, colors, s):
        """Dynamic mouth — waveform when speaking, expression when idle."""
        mouth_y = cy + int(80 * s)
        mouth_w = int(70 * s)

        if self.emotion in (Emotion.HAPPY, Emotion.EXCITED):
            rect = pygame.Rect(cx - mouth_w, mouth_y - int(20 * s), mouth_w * 2, int(50 * s))
            pygame.draw.arc(surface, colors["primary"], rect,
                            math.pi + 0.2, 2 * math.pi - 0.2, max(2, int(3 * s)))
            for side in (-1, 1):
                pygame.draw.circle(surface, colors["accent"],
                                   (cx + side * mouth_w, mouth_y), max(2, int(3 * s)))
        elif self.emotion == Emotion.ANGRY:
            rect = pygame.Rect(cx - mouth_w, mouth_y + int(5 * s), mouth_w * 2, int(35 * s))
            pygame.draw.arc(surface, colors["primary"], rect,
                            0.3, math.pi - 0.3, max(2, int(3 * s)))
        elif self.emotion == Emotion.SLEEPING:
            pts = [(cx - int(30 * s) + int(i * 3 * s),
                    mouth_y + int(math.sin(i * 0.7 + self._time * 2) * 4 * s))
                   for i in range(20)]
            if len(pts) > 1:
                pygame.draw.lines(surface, colors["primary"], False, pts, max(1, int(2 * s)))
        else:
            open_h = self._mouth_open
            if open_h > 0.1:
                wave_pts = []
                for i in range(30):
                    t = i / 30
                    wx = cx - mouth_w + int(t * mouth_w * 2)
                    amp = open_h * 15 * s * math.sin(t * math.pi)
                    wy = mouth_y + math.sin(i * 1.5 + self._time * 18) * amp
                    wave_pts.append((int(wx), int(wy)))
                if len(wave_pts) > 1:
                    pygame.draw.lines(surface, colors["accent"], False, wave_pts, max(2, int(2.5 * s)))
                    for pt in wave_pts[::3]:
                        gs = pygame.Surface((8, 8), pygame.SRCALPHA)
                        pygame.draw.circle(gs, (*colors["primary"], 50), (4, 4), 4)
                        surface.blit(gs, (pt[0] - 4, pt[1] - 4))
            else:
                y_offset = math.sin(self._time * 1.5) * 2 * s
                pygame.draw.line(surface, colors["primary"],
                                 (cx - mouth_w, int(mouth_y + y_offset)),
                                 (cx + mouth_w, int(mouth_y + y_offset)),
                                 max(1, int(2.5 * s)))
                for side in (-1, 1):
                    pygame.draw.line(surface, colors["glow"],
                                     (cx + side * mouth_w, int(mouth_y + y_offset - 4 * s)),
                                     (cx + side * mouth_w, int(mouth_y + y_offset + 4 * s)), 1)

    def _render_scanline(self, surface, colors, s):
        """Holographic scanline sweeping down."""
        if self.emotion == Emotion.SLEEPING:
            return
        y = int(self._scan_y) % (self.height + 50) - 25
        if 0 <= y < self.height:
            scan_color = tuple(max(0, c // 5) for c in colors["primary"])
            pygame.draw.line(surface, scan_color, (0, y), (self.width, y), max(1, int(2 * s)))
            cx = self.width // 2
            bright_w = int(200 * s)
            bright_color = tuple(max(0, c // 3) for c in colors["accent"])
            pygame.draw.line(surface, bright_color,
                             (cx - bright_w // 2, y), (cx + bright_w // 2, y),
                             max(1, int(2 * s)))

    def _render_glitch(self, surface, cx, cy, colors, s):
        """Glitch effect on emotion transitions."""
        if self._glitch_intensity <= 0.05:
            return
        num = int(self._glitch_intensity * 8)
        for _ in range(num):
            gy = cy + random.randint(int(-200 * s), int(200 * s))
            gw = random.randint(int(50 * s), int(250 * s))
            gx = cx - gw // 2 + random.randint(int(-80 * s), int(80 * s))
            gh = random.randint(1, max(2, int(3 * s)))
            ch = random.choice([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
            bright = max(30, int(ch[0] * self._glitch_intensity * 0.4))
            glitch_c = (bright if ch[0] else 0, bright if ch[1] else 0, bright if ch[2] else 0)
            pygame.draw.rect(surface, glitch_c, (gx, gy, gw, gh))

    def _render_hud(self, surface, cx, cy, colors, s):
        """HUD data overlay — positioned to avoid left sidebar panels."""
        now = self._time

        sw = surface.get_width()
        rx = sw - int(120 * s)
        ry = cy + int(100 * s)
        items = [
            (f"DET {self.hud_detections:03d}", colors["primary"] if self.hud_detections > 0 else colors["glow"]),
            (f"FPS {self.hud_fps:4.0f}", colors["glow"]),
            (f"THR {self.hud_threats:03d}",
             (255, 50, 50) if self.hud_threats > 0 and int(now * 3) % 2 else colors["glow"]),
            (f"PRS {self.hud_persons:03d}",
             colors["accent"] if self.hud_persons > 0 else colors["glow"]),
        ]
        for i, (text, color) in enumerate(items):
            surf = self._font_hud.render(text, True, color)
            surface.blit(surf, (rx, ry + i * int(16 * s)))

        ry2 = ry + len(items) * int(16 * s) + int(8 * s)
        right = [
            (f"EMO {self.emotion.value[:6].upper()}", colors["primary"]),
            (f"TRK {self._eye_x:+.1f} {self._eye_y:+.1f}", colors["glow"]),
        ]
        for i, (text, color) in enumerate(right):
            surf = self._font_hud.render(text, True, color)
            surface.blit(surf, (rx, ry2 + i * int(16 * s)))

    def _render_status(self, surface, cx, cy, colors, s):
        """Status indicator — disabled (not visible on screen)."""
        pass
