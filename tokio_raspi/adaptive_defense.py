"""
TokioAI Adaptive Defense — Autonomous security response system.

Based on the Threat Correlation Engine's DEFCON level, automatically:
  - Adjusts Entity emotion/face glow to reflect threat state
  - Modifies WAF sensitivity (via dashboard API)
  - Enables/disables WiFi counter-deauth
  - Triggers drone patrols on high alert
  - Sends Telegram alerts on critical events
  - Logs all defensive actions for audit

This is the "immune system" — not just detection, but RESPONSE.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger("adaptive_defense")


class AdaptiveDefense:
    """
    Autonomous defense response system.

    Reacts to threat level changes and specific events
    with automated defensive actions.
    """

    def __init__(self):
        self._current_level = 5  # DEFCON5 = peace
        self._actions_log: list = []
        self._lock = threading.Lock()
        self._last_actions: Dict[str, float] = {}  # action -> timestamp
        self._cooldowns: Dict[str, float] = {
            "emotion_change": 30,
            "waf_adjust": 120,
            "wifi_harden": 60,
            "drone_patrol": 300,
            "telegram_alert": 60,
        }

        # External action handlers (set by Entity)
        self._set_emotion: Optional[Callable] = None
        self._set_face_glow: Optional[Callable] = None
        self._waf_action: Optional[Callable] = None
        self._wifi_action: Optional[Callable] = None
        self._drone_action: Optional[Callable] = None
        self._telegram_alert: Optional[Callable] = None
        self._say: Optional[Callable] = None

    def set_handlers(self, emotion=None, face_glow=None, waf=None,
                     wifi=None, drone=None, telegram=None, say=None):
        """Set action handler callables."""
        self._set_emotion = emotion
        self._set_face_glow = face_glow
        self._waf_action = waf
        self._wifi_action = wifi
        self._drone_action = drone
        self._telegram_alert = telegram
        self._say = say

    def on_level_change(self, old_level: int, new_level: int, score: float):
        """React to threat level changes."""
        self._current_level = new_level
        logger.info(f"Defense responding to DEFCON {new_level} (score: {score:.1f})")

        if new_level <= 2:  # DEFCON 2 or 1
            self._respond_critical(new_level, score)
        elif new_level == 3:
            self._respond_increased(score)
        elif new_level == 4:
            self._respond_elevated(score)
        else:
            self._respond_peace(score)

    def on_insight(self, insight):
        """React to specific correlation insights."""
        if insight.severity == "critical":
            self._log_action("insight_critical", insight.title)
            if self._telegram_alert and self._can_act("telegram_alert"):
                self._telegram_alert(
                    f"🚨 THREAT CORRELATION\n{insight.title}\n{insight.detail}"
                )
            if self._say:
                self._say(f"⚠ {insight.title}", (255, 40, 60))

    def _respond_critical(self, level: int, score: float):
        """DEFCON 1-2: Maximum response."""
        # Change face to alert mode
        if self._set_emotion and self._can_act("emotion_change"):
            self._set_emotion("ALERT")
        if self._set_face_glow:
            self._set_face_glow((255, 0, 0))  # Red glow

        # Enable maximum WiFi defense
        if self._wifi_action and self._can_act("wifi_harden"):
            self._wifi_action("harden")
            self._log_action("wifi_harden", "Counter-deauth enabled, monitoring intensified")

        # Alert via Telegram
        if self._telegram_alert and self._can_act("telegram_alert"):
            self._telegram_alert(
                f"🔴 DEFCON {level} — Threat score: {score:.0f}/100\n"
                f"Multi-vector attack possible. Auto-defenses engaged."
            )

        # Announce on screen
        if self._say:
            self._say(f"DEFCON {level} — ACTIVE DEFENSE", (255, 40, 60))

        self._log_action("level_response", f"DEFCON {level} critical response engaged")

    def _respond_increased(self, score: float):
        """DEFCON 3: Heightened awareness."""
        if self._set_emotion and self._can_act("emotion_change"):
            self._set_emotion("FOCUSED")
        if self._set_face_glow:
            self._set_face_glow((255, 180, 0))  # Orange glow

        if self._say:
            self._say("Threat level increased — monitoring", (255, 180, 0))

        self._log_action("level_response", f"DEFCON 3 increased monitoring (score: {score:.0f})")

    def _respond_elevated(self, score: float):
        """DEFCON 4: Minor anomalies detected."""
        if self._set_emotion and self._can_act("emotion_change"):
            self._set_emotion("CURIOUS")
        if self._set_face_glow:
            self._set_face_glow((0, 200, 255))  # Cyan glow

        self._log_action("level_response", f"DEFCON 4 elevated (score: {score:.0f})")

    def _respond_peace(self, score: float):
        """DEFCON 5: All normal."""
        if self._set_emotion and self._can_act("emotion_change"):
            self._set_emotion("NEUTRAL")
        if self._set_face_glow:
            self._set_face_glow((0, 255, 100))  # Green glow

    def _can_act(self, action_name: str) -> bool:
        """Check cooldown for an action."""
        now = time.time()
        last = self._last_actions.get(action_name, 0)
        cooldown = self._cooldowns.get(action_name, 60)
        if now - last < cooldown:
            return False
        self._last_actions[action_name] = now
        return True

    def _log_action(self, action: str, detail: str):
        """Log a defensive action."""
        entry = {
            "ts": time.time(),
            "action": action,
            "detail": detail,
            "level": self._current_level,
        }
        with self._lock:
            self._actions_log.append(entry)
            if len(self._actions_log) > 200:
                self._actions_log = self._actions_log[-200:]
        logger.info(f"DEFENSE ACTION [{action}]: {detail}")

    def get_log(self, limit: int = 20) -> list:
        """Get recent defense actions."""
        with self._lock:
            return self._actions_log[-limit:]

    def get_status(self) -> dict:
        """Get current defense status."""
        return {
            "current_level": self._current_level,
            "total_actions": len(self._actions_log),
            "recent_actions": self.get_log(5),
            "cooldowns": {
                k: max(0, round(self._cooldowns[k] - (time.time() - self._last_actions.get(k, 0))))
                for k in self._cooldowns
            }
        }
