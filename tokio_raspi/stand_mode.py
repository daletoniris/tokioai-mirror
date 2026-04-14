"""
TokioAI Stand Mode — Autonomous operation for conferences/exhibits.

When activated, Tokio operates autonomously:
- Greets visitors who approach (face detection)
- Shows system stats on screen periodically
- Cycles through demo displays (WAF stats, WiFi defense, health, drone)
- Engages with recognized people by name
- Tracks visitor count and dwell time
- Reports activity to Telegram periodically
- Auto-recovers from any service failure

Usage:
    POST /mode/stand {"enabled": true}
    GET  /mode/stand/stats
"""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .main import TokioRaspiApp

logger = logging.getLogger("stand_mode")


# Greetings for unknown visitors
GREETINGS_ES = [
    "¡Hola! Soy Tokio, un agente de IA autónomo.",
    "¡Bienvenido! Soy TokioAI — seguridad inteligente.",
    "Hey! Acercate, te muestro lo que puedo hacer.",
    "¡Hola! Estoy monitoreando todo en tiempo real.",
]

GREETINGS_EN = [
    "Hi! I'm Tokio, an autonomous AI agent.",
    "Welcome! I'm TokioAI — intelligent security.",
    "Hey! Come closer, let me show you what I can do.",
    "Hello! I'm monitoring everything in real-time.",
]

# Demo cycle messages
DEMO_MESSAGES = [
    ("🔥 WAF Defense: {attacks} ataques detectados, {blocked} bloqueados", "waf"),
    ("📡 WiFi: Monitoreando {channels} canales. {deauths} ataques mitigados", "wifi"),
    ("❤️ Health Monitor: HR {hr} bpm | SpO2 {spo2}%", "health"),
    ("🚁 Drone: Safety proxy activo. Geofence {geo}cm", "drone"),
    ("👁️ Vision: {fps} FPS con Hailo-8L AI. {persons} personas detectadas", "entity"),
    ("🧠 AI Brain: Analizo contexto cada 12s con Claude", "brain"),
    ("🛡️ DEFCON {defcon}: {level_name}", "threat"),
]


@dataclass
class VisitorStats:
    total_visitors: int = 0
    current_visitors: int = 0
    greetings_given: int = 0
    recognized_returns: int = 0
    peak_visitors: int = 0
    session_start: float = field(default_factory=time.time)
    visitors_by_hour: dict = field(default_factory=lambda: defaultdict(int))
    dwell_times: list = field(default_factory=list)


class StandMode:
    """Autonomous stand operation for TokioAI."""

    def __init__(self, app: TokioRaspiApp):
        self._app = app
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._stats = VisitorStats()
        self._last_greeting_time = 0
        self._greeting_cooldown = 15  # seconds between greetings
        self._demo_cycle_interval = 20  # seconds between demo messages
        self._last_demo_time = 0
        self._demo_index = 0
        self._last_person_count = 0
        self._person_enter_time: dict = {}  # track when people appear
        self._telegram_report_interval = 300  # 5 min reports
        self._last_telegram_report = 0
        self._lang = "es"  # default language

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> dict:
        elapsed = time.time() - self._stats.session_start
        return {
            "active": self._active,
            "session_minutes": round(elapsed / 60, 1),
            "total_visitors": self._stats.total_visitors,
            "current_visitors": self._stats.current_visitors,
            "peak_visitors": self._stats.peak_visitors,
            "greetings_given": self._stats.greetings_given,
            "recognized_returns": self._stats.recognized_returns,
        }

    def start(self, lang: str = "es"):
        if self._active:
            return
        self._active = True
        self._lang = lang
        self._stats = VisitorStats()
        self._app._stand_mode = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="stand-mode")
        self._thread.start()
        logger.info("Stand mode ACTIVATED")

    def stop(self):
        self._active = False
        self._app._stand_mode = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Stand mode DEACTIVATED")

    def _loop(self):
        """Main stand mode loop — runs continuously."""
        while self._active:
            try:
                now = time.time()

                # Track visitors
                self._track_visitors()

                # Greet new visitors
                if now - self._last_greeting_time > self._greeting_cooldown:
                    self._maybe_greet()

                # Cycle demo messages on screen
                if now - self._last_demo_time > self._demo_cycle_interval:
                    self._show_demo_message()
                    self._last_demo_time = now

                # Periodic Telegram report
                if now - self._last_telegram_report > self._telegram_report_interval:
                    self._send_telegram_report()
                    self._last_telegram_report = now

                time.sleep(2)

            except Exception as e:
                logger.error(f"Stand mode error: {e}")
                time.sleep(5)

    def _track_visitors(self):
        """Track visitor count changes."""
        current = self._app._person_count
        prev = self._last_person_count

        if current > prev:
            # New person appeared
            new_count = current - prev
            self._stats.total_visitors += new_count
            self._stats.current_visitors = current
            if current > self._stats.peak_visitors:
                self._stats.peak_visitors = current

            # Track by hour
            import datetime
            hour = datetime.datetime.now().hour
            self._stats.visitors_by_hour[hour] += new_count

        elif current < prev:
            # Person left
            self._stats.current_visitors = current

        self._last_person_count = current

    def _maybe_greet(self):
        """Greet visitors if new people are detected."""
        current = self._app._person_count
        if current == 0:
            return

        # Check if we have a recognized face
        known_names = []
        if hasattr(self._app, '_last_recognized'):
            known_names = self._app._last_recognized or []

        if known_names:
            # Greet by name
            name = known_names[0]
            self._stats.recognized_returns += 1
            greeting = f"¡Hola {name}! Que bueno verte de nuevo." if self._lang == "es" \
                else f"Hey {name}! Great to see you again."
        else:
            # Generic greeting
            greets = GREETINGS_ES if self._lang == "es" else GREETINGS_EN
            greeting = random.choice(greets)

        self._app._say(greeting, (0, 255, 200))
        self._stats.greetings_given += 1
        self._last_greeting_time = time.time()

    def _show_demo_message(self):
        """Show rotating demo information on Tokio's face."""
        if self._app._person_count == 0:
            # No audience — show minimal
            return

        msg_template, msg_type = DEMO_MESSAGES[self._demo_index % len(DEMO_MESSAGES)]
        self._demo_index += 1

        try:
            data = {}
            if msg_type == "waf" and hasattr(self._app, 'security') and self._app.security.connected:
                waf_data = self._app.security._last_data or {}
                data = {
                    "attacks": waf_data.get("total_attacks", "?"),
                    "blocked": waf_data.get("blocked", "?"),
                }
            elif msg_type == "wifi" and self._app.wifi_defense:
                stats = self._app.wifi_defense.get_stats()
                data = {
                    "channels": "14",
                    "deauths": stats.deauth_detected,
                }
            elif msg_type == "health" and hasattr(self._app, 'health') and self._app.health.available:
                h = self._app.health.data
                data = {"hr": h.heart_rate, "spo2": h.spo2}
            elif msg_type == "drone":
                data = {"geo": "200"}
            elif msg_type == "entity" and self._app.vision:
                vs = self._app.vision.get_status()
                data = {"fps": vs.get("fps", "?"), "persons": self._app._person_count}
            elif msg_type == "brain":
                data = {}
            elif msg_type == "threat":
                if hasattr(self._app, 'threat_engine'):
                    ts = self._app.threat_engine.get_status()
                    data = {"defcon": ts.get("defcon", "5"), "level_name": ts.get("level_name", "PEACE")}
                else:
                    data = {"defcon": "5", "level_name": "PEACE"}

            if data:
                msg = msg_template.format(**data)
                self._app._say(msg, (100, 200, 255))
        except Exception as e:
            logger.debug(f"Demo message error: {e}")

    def _send_telegram_report(self):
        """Send periodic activity report to Telegram."""
        if not self._active:
            return
        try:
            elapsed = time.time() - self._stats.session_start
            mins = int(elapsed / 60)
            msg = (
                f"📊 Stand Mode Report ({mins} min)\n"
                f"👥 Visitors: {self._stats.total_visitors} total, "
                f"{self._stats.current_visitors} now, "
                f"peak {self._stats.peak_visitors}\n"
                f"👋 Greetings: {self._stats.greetings_given}\n"
                f"🔁 Recognized returns: {self._stats.recognized_returns}"
            )
            # Push to core API for Telegram
            from . import main as _m
            if hasattr(_m, '_push_to_core'):
                _m._push_to_core("/api/internal/event", {
                    "type": "stand_report",
                    "message": msg,
                })
            logger.info(f"Stand report: {self._stats.total_visitors} visitors, {self._stats.current_visitors} current")
        except Exception as e:
            logger.debug(f"Telegram report error: {e}")
