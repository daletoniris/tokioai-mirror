"""
TokioAI Entity Sync — Connects the GCP brain to the Raspi body.

Tokio is ONE living entity:
- Brain (GCP): thinks, decides, responds
- Body (Raspi Entity): sees, reacts, defends, shows

This module ensures everything stays in sync:
1. After every response → Entity reflects emotion + shows activity
2. Entity events (WiFi attacks, faces, health) → push to core
3. Self-healing: monitors all services, auto-repairs
4. Security dashboard: tracks all blocked attacks for display
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional, Dict, Any, List

import httpx

logger = logging.getLogger(__name__)

RASPI_API = os.getenv("RASPI_API_URL", "")
ENTITY_SYNC_ENABLED = os.getenv("ENTITY_SYNC_ENABLED", "true").lower() in ("true", "1", "yes")
ENTITY_SYNC_TIMEOUT = float(os.getenv("ENTITY_SYNC_TIMEOUT", "5.0"))


# ── Emotion Detection ─────────────────────────────────────────

_EMOTION_RULES = [
    # (emotion, priority, patterns)
    ("angry", 10, [
        r"bloqueado|blocked|denied|intrus|breach|compromised|prohibido",
        r"ataque|attack|exploit|vulnerability|hack",
    ]),
    ("alert", 8, [
        r"cuidado|warning|peligro|alerta|atencion|ojo|riesgo",
        r"caution|danger|risk|threat|suspicious",
    ]),
    ("excited", 7, [
        r"increible|wow|asombroso|fantastico|tremendo|espectacular|que buena onda",
        r"amazing|incredible|fantastic|spectacular",
        r"\U0001f525|\U0001f4aa|\U0001f929|🔥|💪|🤩",
    ]),
    ("happy", 6, [
        r"jaja|haha|jeje|gracioso|divertido|genial|excelente|perfecto|todo bien|chido|bien hecho",
        r"great|awesome|excellent|nice|cool|done|perfect|listo|ready|claro que|por supuesto",
        r"\U0001f60e|\U0001f60a|\U0001f604|\U0001f601|\U0001f44d|\U0001f389|\U0001f680|😎|😄|😁|👍|🎉|🚀|🤙|😊",
    ]),
    ("thinking", 4, [
        r"analizando|investigando|buscando|procesando|revisando|calculando",
        r"analyzing|investigating|searching|processing|checking|computing",
    ]),
    ("curious", 3, [
        r"interesante|hmm|veamos|a ver|mira|que raro|curioso",
        r"interesting|let me see|curious|unusual|strange",
    ]),
]

_COMPILED_RULES = []
for emo, prio, patterns in _EMOTION_RULES:
    combined = "|".join(f"(?:{p})" for p in patterns)
    _COMPILED_RULES.append((emo, prio, re.compile(combined, re.IGNORECASE)))


def detect_emotion(text: str) -> str:
    """Analyze response text and return the best-fitting emotion."""
    if not text:
        return "neutral"

    best_emotion = "neutral"
    best_score = 0

    for emotion, priority, pattern in _COMPILED_RULES:
        matches = pattern.findall(text)
        if matches:
            score = len(matches) * priority
            if score > best_score:
                best_score = score
                best_emotion = emotion

    # Fallback heuristics when no patterns matched
    if best_score == 0:
        if "## Result of" in text:
            return "thinking"
        # Enthusiastic responses (lots of !) -> happy
        if text.count("!") >= 2:
            return "happy"

    return best_emotion


# ── Session-to-user mapping ────────────────────────────────────

_USER_NAMES = {
    # Map Telegram user IDs to names via TOKIO_USER_MAP env var
    # Format: "uid1:Name1,uid2:Name2"
}

def _load_user_map():
    env = os.environ.get("TOKIO_USER_MAP", "")
    for pair in env.split(","):
        if ":" in pair:
            uid, name = pair.strip().split(":", 1)
            _USER_NAMES[uid.strip()] = name.strip()

_load_user_map()


def _get_user_name(session_id: str) -> str:
    """Extract user name from session ID."""
    if not session_id:
        return "Usuario"
    for uid, name in _USER_NAMES.items():
        if uid in session_id:
            return name
    return "Usuario"


# ── Entity API calls ───────────────────────────────────────────

async def _post_entity(path: str, data: dict) -> Optional[dict]:
    """POST to Entity API. Non-blocking, fire-and-forget."""
    try:
        async with httpx.AsyncClient(timeout=ENTITY_SYNC_TIMEOUT) as client:
            r = await client.post(f"{RASPI_API}{path}", json=data)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug(f"Entity sync ({path}): {e}")
        return None


async def _get_entity(path: str) -> Optional[dict]:
    """GET from Entity API."""
    try:
        async with httpx.AsyncClient(timeout=ENTITY_SYNC_TIMEOUT) as client:
            r = await client.get(f"{RASPI_API}{path}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug(f"Entity get ({path}): {e}")
        return None


# ── Core sync: after agent responds ───────────────────────────

async def sync_after_response(
    user_message: str,
    response: str,
    session_id: str = "",
) -> None:
    """Sync Entity after the agent produces a response.

    Called automatically after process_message().
    Fire-and-forget — NEVER blocks the response to the user.
    """
    if not ENTITY_SYNC_ENABLED:
        return

    try:
        emotion = detect_emotion(response)
        user_name = _get_user_name(session_id)

        # Build clean display for Entity screen (not raw Telegram text)
        emotion_labels = {
            "happy": "contento",
            "excited": "emocionado",
            "thinking": "pensando",
            "curious": "curioso",
            "alert": "alerta",
            "angry": "en defensa",
            "neutral": "atento",
        }
        emotion_label = emotion_labels.get(emotion, emotion)
        # Short summary: who + emotion, not the full response
        display = f"Hablando con {user_name} — {emotion_label}"

        # Short preview of response for Entity display
        resp_preview = response[:50].replace("\n", " ").strip()

        tasks = [
            _post_entity("/emotion", {
                "emotion": emotion,
                "message": f"Respondiendo a {user_name}",
            }),
            _post_entity("/say", {
                "text": display,
                "color": "bright",
            }),
            # Send Telegram activity for live panel on Entity screen
            _post_entity("/core/event", {
                "type": "telegram",
                "user": user_name,
                "message": resp_preview,
                "emotion": emotion,
                "color": "accent",
            }),
        ]

        # Eye movement based on emotion
        eye_positions = {
            "curious": (0.3, -0.2),
            "thinking": (-0.2, -0.1),
            "alert": (0.0, 0.0),
            "angry": (0.0, 0.0),
            "happy": (0.1, 0.1),
            "excited": (0.2, 0.2),
        }
        if emotion in eye_positions:
            x, y = eye_positions[emotion]
            tasks.append(_post_entity("/look", {"x": x, "y": y}))

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"Entity sync: emotion={emotion} user={user_name}")

    except Exception as e:
        logger.debug(f"Entity sync error: {e}")


# ── Security event tracking ───────────────────────────────────

class SecurityDashboard:
    """Tracks all security events for Entity display."""

    def __init__(self):
        self._events: List[Dict[str, Any]] = []
        self._blocked_today = 0
        self._last_reset_day = 0

    def record_event(self, event_type: str, message: str,
                     severity: str, attacker: str = ""):
        """Record a security event."""
        now = time.time()
        day = int(now // 86400)
        if day != self._last_reset_day:
            self._blocked_today = 0
            self._last_reset_day = day

        self._blocked_today += 1
        self._events.append({
            "time": now,
            "type": event_type,
            "message": message,
            "severity": severity,
            "attacker": attacker,
        })
        # Keep last 200 events in memory
        if len(self._events) > 200:
            self._events = self._events[-200:]

    @property
    def blocked_today(self) -> int:
        return self._blocked_today

    def get_recent(self, limit: int = 10) -> List[Dict]:
        return self._events[-limit:]

    def get_summary(self) -> Dict[str, Any]:
        types = {}
        for e in self._events:
            t = e["type"]
            types[t] = types.get(t, 0) + 1
        return {
            "blocked_today": self._blocked_today,
            "total_tracked": len(self._events),
            "by_type": types,
        }


# Global security dashboard instance
security_dashboard = SecurityDashboard()


async def on_security_event(
    event_type: str,
    message: str,
    severity: str = "info",
    attacker: str = "",
) -> None:
    """Handle a security event from any source (WiFi, BLE, network).

    Records it, syncs Entity display, and could trigger router defense.
    """
    security_dashboard.record_event(event_type, message, severity, attacker)

    if not ENTITY_SYNC_ENABLED:
        return

    emotion_map = {
        "critical": "angry",
        "high": "alert",
        "medium": "alert",
        "info": "neutral",
    }
    color_map = {
        "critical": "danger",
        "high": "warn",
        "medium": "warn",
        "info": "bright",
    }

    blocked_count = security_dashboard.blocked_today

    try:
        await asyncio.gather(
            _post_entity("/emotion", {
                "emotion": emotion_map.get(severity, "neutral"),
                "message": f"Security: {event_type}",
            }),
            _post_entity("/say", {
                "text": f"[SEC] {message} | Bloqueados hoy: {blocked_count}",
                "color": color_map.get(severity, "bright"),
            }),
            return_exceptions=True,
        )
    except Exception as e:
        logger.debug(f"Security event sync error: {e}")


# ── Entity health check ──────────────────────────────────────

async def check_entity_alive() -> bool:
    """Quick health check — is the Entity responding?"""
    result = await _get_entity("/status")
    return result is not None and "error" not in result


# ── Notify Entity: incoming message ───────────────────────────

async def notify_incoming_message(session_id: str = "") -> None:
    """Notify Entity that someone is talking to Tokio.

    Shows 'thinking' face while processing.
    """
    if not ENTITY_SYNC_ENABLED:
        return

    user_name = _get_user_name(session_id)
    try:
        await asyncio.gather(
            _post_entity("/emotion", {
                "emotion": "thinking",
                "message": f"Procesando mensaje de {user_name}...",
            }),
            _post_entity("/say", {
                "text": f"[{user_name} esta hablando...]",
                "color": "dim",
            }),
            return_exceptions=True,
        )
    except Exception:
        pass
