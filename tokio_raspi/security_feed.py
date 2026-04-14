"""
TokioAI Security Feed — Real-time WAF/SOC event consumer.

Polls the WAF Dashboard API on GCP for attack events,
maps them to Tokio emotions, and provides a stream of
security events for the UI terminal.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import requests

logger = logging.getLogger("tokio.security")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WAF_API_BASE = os.getenv("TOKIO_WAF_API", "http://127.0.0.1:18000")
WAF_USER = os.getenv("TOKIO_WAF_USER", "admin")
WAF_PASS = os.getenv("TOKIO_WAF_PASS", os.getenv("WAF_ADMIN_PASS", ""))
POLL_INTERVAL = 5  # seconds

# Japanese flavor text for different event types
JP_SEVERITY = {
    "critical": "\u81e8\u754c",      # rinkai - critical
    "high":     "\u5371\u967a",      # kiken - danger
    "medium":   "\u8b66\u544a",      # keikoku - warning
    "low":      "\u76e3\u8996",      # kanshi - monitoring
    "info":     "\u60c5\u5831",      # jouhou - info
}

JP_ACTIONS = {
    "block_ip":    "\u906e\u65ad",   # shadan - blocked
    "log_only":    "\u8a18\u9332",   # kiroku - logged
    "rate_limit":  "\u5236\u9650",   # seigen - limited
    "challenge":   "\u691c\u8a3c",   # kenshou - verified
}

JP_THREATS = {
    "SQLi":        "SQL\u6ce8\u5165",      # SQL injection
    "XSS":         "\u30af\u30ed\u30b9\u30b5\u30a4\u30c8",  # cross-site
    "RCE":         "\u9060\u9694\u5b9f\u884c",    # remote execution
    "LFI":         "\u30d5\u30a1\u30a4\u30eb\u4fb5\u5165",  # file intrusion
    "SCANNER":     "\u30b9\u30ad\u30e3\u30ca",    # scanner
    "HONEYPOT":    "\u7f60",               # wana - trap
    "BRUTE_FORCE": "\u7dcf\u5f53\u305f\u308a",    # brute force
    "BOT":         "\u30dc\u30c3\u30c8",          # bot
    "DDoS":        "\u5206\u6563\u653b\u6483",    # distributed attack
    "ZERO_DAY":    "\u30bc\u30ed\u30c7\u30a4",    # zero day
}

JP_GREETINGS = [
    "\u3053\u3093\u306b\u3061\u306f",     # konnichiwa
    "\u3088\u3046\u3053\u305d",           # youkoso - welcome
    "\u3044\u3089\u3063\u3057\u3083\u3044",  # irasshai
    "\u521d\u3081\u307e\u3057\u3066",      # hajimemashite
]

JP_STATUS = {
    "scanning":  "\u30b9\u30ad\u30e3\u30f3\u4e2d...",    # scanning...
    "defending": "\u9632\u5fa1\u4e2d",          # defending
    "alert":     "\u8b66\u6212\u30e2\u30fc\u30c9",      # alert mode
    "secure":    "\u5b89\u5168",              # safe
    "hunting":   "\u8106\u5f31\u6027\u63a2\u7d22",      # vulnerability hunting
}


class SeverityLevel(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class SecurityEvent:
    timestamp: str
    ip: str
    method: str
    uri: str
    severity: str
    blocked: bool
    threat_type: Optional[str]
    owasp: Optional[str]
    action: Optional[str]
    sig_id: Optional[str]
    host: Optional[str]

    @property
    def display_line(self) -> str:
        """Format as terminal line."""
        ts = self.timestamp.split(" ")[1][:8] if " " in self.timestamp else self.timestamp[:8]
        sev_jp = JP_SEVERITY.get(self.severity, self.severity)
        action_jp = JP_ACTIONS.get(self.action, self.action or "")
        threat = self.threat_type or ""
        threat_jp = JP_THREATS.get(threat, threat)

        blocked_mark = "\u2588 BLOCK" if self.blocked else "\u2591 LOG"
        uri_short = self.uri[:30] + "..." if len(self.uri) > 30 else self.uri

        return f"[{ts}] {sev_jp} {self.ip:>15} {self.method:4} {uri_short:<33} {blocked_mark} {threat_jp}"

    @property
    def severity_color(self) -> tuple:
        colors = {
            "critical": (255, 20, 60),
            "high":     (255, 140, 0),
            "medium":   (255, 220, 0),
            "low":      (0, 200, 255),
            "info":     (100, 130, 160),
        }
        return colors.get(self.severity, (100, 130, 160))


@dataclass
class SecurityStats:
    total_attacks: int = 0
    blocked: int = 0
    unique_ips: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    active_blocks: int = 0
    events_per_min: float = 0.0
    top_threat: str = ""
    last_update: float = 0.0


# ---------------------------------------------------------------------------
# Security Feed
# ---------------------------------------------------------------------------

class SecurityFeed:
    """Background thread that polls WAF API and provides events."""

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._events: list[SecurityEvent] = []
        self._max_events = 200
        self._stats = SecurityStats()
        self._last_event_ts: Optional[str] = None
        self._new_events_count = 0
        self._connected = False
        self._error_msg = ""
        self._auth_failures = 0
        self._auth_backoff = 0.0

        # Emotion callback
        self._emotion_callback = None

        # Track event rate
        self._event_timestamps: list[float] = []
        self._disabled = False

    def set_emotion_callback(self, callback):
        """Set callback for emotion changes: callback(emotion, message)."""
        self._emotion_callback = callback

    MAX_AUTH_FAILURES = 5  # Stop trying after this many failures

    def start(self):
        if self._running:
            return
        # Don't start if WAF is not configured
        if not WAF_API_BASE or not WAF_PASS:
            print("[SecurityFeed] Disabled — WAF not configured (set TOKIO_WAF_API + TOKIO_WAF_PASS)")
            self._disabled = True
            return
        self._disabled = False
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"[SecurityFeed] Started (WAF: {WAF_API_BASE})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self, limit: int = 50) -> list[SecurityEvent]:
        with self._lock:
            return list(self._events[-limit:])

    def get_stats(self) -> SecurityStats:
        with self._lock:
            return SecurityStats(**vars(self._stats))

    def get_new_count(self) -> int:
        with self._lock:
            count = self._new_events_count
            self._new_events_count = 0
            return count

    @property
    def connected(self) -> bool:
        return self._connected and not self._disabled

    @property
    def disabled(self) -> bool:
        return self._disabled

    def _get_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_expiry - 300:
            return self._token
        # Exponential backoff on auth failures
        now = time.time()
        if now < self._auth_backoff:
            return None
        try:
            r = requests.post(
                f"{WAF_API_BASE}/api/auth/login",
                json={"username": WAF_USER, "password": WAF_PASS},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            self._token = data["token"]
            self._token_expiry = time.time() + 86000
            self._auth_failures = 0
            self._connected = True
            print("[SecurityFeed] WAF authenticated OK")
            return self._token
        except Exception as e:
            self._auth_failures += 1
            if self._auth_failures >= self.MAX_AUTH_FAILURES:
                print(f"[SecurityFeed] Auth failed {self._auth_failures}x — disabling feed. Fix WAF credentials and restart.")
                self._running = False
                self._disabled = True
                self._error_msg = "Disabled: too many auth failures"
                return None
            # Backoff: 20s, 40s, 80s, max 300s
            backoff = min(300, 20 * (2 ** min(self._auth_failures, 4)))
            self._auth_backoff = now + backoff
            if self._auth_failures <= 3:
                print(f"[SecurityFeed] Auth failed: {e} (retry in {backoff}s)")
            self._error_msg = f"Auth: {e}"
            return None

    def _api_get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        token = self._get_token()
        if not token:
            return None
        try:
            r = requests.get(
                f"{WAF_API_BASE}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=8,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"API error {endpoint}: {e}")
            self._error_msg = str(e)
            return None

    def _poll_loop(self):
        # Initial delay to let UI start
        time.sleep(2)

        while self._running:
            try:
                self._poll_once()
                self._connected = True
                self._error_msg = ""
            except Exception as e:
                logger.error(f"Poll error: {e}")
                self._connected = False
                self._error_msg = str(e)
            time.sleep(POLL_INTERVAL)

    def _poll_once(self):
        # Fetch summary stats
        summary = self._api_get("/api/summary")
        if summary:
            with self._lock:
                self._stats.total_attacks = summary.get("total", 0)
                self._stats.blocked = summary.get("blocked", 0)
                self._stats.unique_ips = summary.get("unique_ips", 0)
                self._stats.critical = summary.get("critical", 0)
                self._stats.high = summary.get("high", 0)
                self._stats.medium = summary.get("medium", 0)
                self._stats.low = summary.get("low", 0)
                self._stats.active_blocks = summary.get("active_blocks", 0)
                self._stats.last_update = time.time()

        # Fetch recent attacks
        attacks = self._api_get("/api/attacks/recent", {"limit": 20})
        if attacks and isinstance(attacks, list):
            new_events = []
            for a in reversed(attacks):  # oldest first
                ts = a.get("timestamp", "")
                if self._last_event_ts and ts <= self._last_event_ts:
                    continue
                event = SecurityEvent(
                    timestamp=ts,
                    ip=a.get("ip", "?"),
                    method=a.get("method", "?"),
                    uri=a.get("uri", "/"),
                    severity=a.get("severity", "info"),
                    blocked=a.get("blocked") == "True" or a.get("blocked") is True,
                    threat_type=a.get("threat_type"),
                    owasp=a.get("owasp_name"),
                    action=a.get("action"),
                    sig_id=a.get("sig_id"),
                    host=a.get("host"),
                )
                new_events.append(event)

            if new_events:
                with self._lock:
                    self._events.extend(new_events)
                    if len(self._events) > self._max_events:
                        self._events = self._events[-self._max_events:]
                    self._last_event_ts = new_events[-1].timestamp
                    self._new_events_count += len(new_events)

                    # Track rate
                    now = time.time()
                    self._event_timestamps.extend([now] * len(new_events))
                    cutoff = now - 60
                    self._event_timestamps = [t for t in self._event_timestamps if t > cutoff]
                    self._stats.events_per_min = len(self._event_timestamps)

                # Trigger emotion based on most severe new event
                self._trigger_emotion(new_events)

    def _trigger_emotion(self, events: list[SecurityEvent]):
        if not self._emotion_callback:
            return

        # Find most severe event
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
        worst = max(events, key=lambda e: severity_rank.get(e.severity, 0))

        if worst.severity == "critical":
            threat = JP_THREATS.get(worst.threat_type, worst.threat_type or "ATTACK")
            if worst.blocked:
                self._emotion_callback("angry", f"\u906e\u65ad! {threat} from {worst.ip}")
            else:
                self._emotion_callback("alert", f"\u81e8\u754c! {threat} {worst.uri}")
        elif worst.severity == "high":
            if worst.blocked:
                self._emotion_callback("alert", f"\u5371\u967a \u2192 {JP_ACTIONS.get('block_ip', 'BLOCKED')} {worst.ip}")
            else:
                self._emotion_callback("scanning", f"\u5206\u6790\u4e2d... {worst.threat_type or worst.uri}")
        elif worst.severity == "medium":
            self._emotion_callback("thinking", f"\u8b66\u544a: {worst.method} {worst.uri[:25]}")
        elif len(events) > 5:
            self._emotion_callback("excited", f"\u5927\u91cf\u30a4\u30d9\u30f3\u30c8! {len(events)} events/cycle")
