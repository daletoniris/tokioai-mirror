"""
TokioAI Threat Correlation Engine — Unified threat intelligence.

Fuses data from:
  - WAF (cloud attacks: SQLi, XSS, RCE, scans)
  - WiFi Defense (physical proximity attacks: deauth, evil twin)
  - BLE Security (smartwatch spoofing, replay attacks)
  - Vision (unknown persons, suspicious behavior)

Outputs:
  - Unified threat level (DEFCON 1-5)
  - Per-vector threat scores
  - Auto-escalation actions
  - Correlation insights (e.g., "WiFi deauth + WAF spike = coordinated attack")
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable, List, Dict

logger = logging.getLogger("threat_correlation")


class ThreatLevel(IntEnum):
    """DEFCON-style threat levels (lower = more dangerous)."""
    DEFCON5 = 5  # Peace — all systems normal
    DEFCON4 = 4  # Elevated — minor anomalies
    DEFCON3 = 3  # Increased — active probing detected
    DEFCON2 = 2  # High — active attack in progress
    DEFCON1 = 1  # Maximum — coordinated multi-vector attack


@dataclass
class ThreatVector:
    """Individual threat vector with score and metadata."""
    name: str
    score: float = 0.0        # 0-100
    events_1m: int = 0        # events in last minute
    events_5m: int = 0        # events in last 5 minutes
    events_1h: int = 0        # events in last hour
    last_event: float = 0     # timestamp
    last_detail: str = ""
    blocked_count: int = 0
    critical_count: int = 0


@dataclass
class CorrelationInsight:
    """Cross-vector correlation finding."""
    timestamp: float
    title: str
    detail: str
    vectors: List[str]
    severity: str  # info, warning, critical
    ttl: float = 300  # seconds until expired


@dataclass
class ThreatState:
    """Complete threat state snapshot."""
    level: ThreatLevel = ThreatLevel.DEFCON5
    level_name: str = "PEACE"
    overall_score: float = 0.0
    vectors: Dict[str, ThreatVector] = field(default_factory=dict)
    insights: List[CorrelationInsight] = field(default_factory=list)
    auto_actions: List[str] = field(default_factory=list)
    last_update: float = 0


class ThreatCorrelationEngine:
    """
    Fuses all security signals into a unified threat assessment.
    
    Runs as a background thread, polling each source and computing
    cross-vector correlations every few seconds.
    """

    # Score thresholds for each DEFCON level
    LEVEL_THRESHOLDS = {
        ThreatLevel.DEFCON1: 85,
        ThreatLevel.DEFCON2: 65,
        ThreatLevel.DEFCON3: 40,
        ThreatLevel.DEFCON4: 15,
        ThreatLevel.DEFCON5: 0,
    }

    # Weight for each vector in overall score
    VECTOR_WEIGHTS = {
        "waf": 0.35,
        "wifi": 0.30,
        "ble": 0.15,
        "vision": 0.20,
    }

    def __init__(self, on_level_change: Optional[Callable] = None,
                 on_insight: Optional[Callable] = None):
        self._state = ThreatState()
        self._state.vectors = {
            "waf": ThreatVector(name="WAF/Cloud"),
            "wifi": ThreatVector(name="WiFi/Physical"),
            "ble": ThreatVector(name="BLE/Wearable"),
            "vision": ThreatVector(name="Vision/Physical"),
        }
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._on_level_change = on_level_change
        self._on_insight = on_insight

        # Event buffers for time-window analysis
        self._event_buffer: Dict[str, List[float]] = {
            "waf": [], "wifi": [], "ble": [], "vision": []
        }
        self._event_details: Dict[str, List[dict]] = {
            "waf": [], "wifi": [], "ble": [], "vision": []
        }
        self._max_buffer = 500
        self._insights: List[CorrelationInsight] = []

        # External data sources (set by Entity)
        self._waf_fetcher: Optional[Callable] = None
        self._wifi_stats: Optional[Callable] = None
        self._ble_stats: Optional[Callable] = None
        self._vision_stats: Optional[Callable] = None

    def set_sources(self, waf=None, wifi=None, ble=None, vision=None):
        """Set data source callables."""
        self._waf_fetcher = waf
        self._wifi_stats = wifi
        self._ble_stats = ble
        self._vision_stats = vision

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name="threat-correlation")
        self._thread.start()
        logger.info("Threat Correlation Engine started")

    def stop(self):
        self._running = False

    def push_event(self, vector: str, severity: str = "medium",
                   detail: str = "", blocked: bool = False):
        """Push a security event from any vector."""
        now = time.time()
        if vector not in self._event_buffer:
            return
        self._event_buffer[vector].append(now)
        self._event_details[vector].append({
            "ts": now, "severity": severity,
            "detail": detail, "blocked": blocked
        })
        # Trim buffer
        if len(self._event_buffer[vector]) > self._max_buffer:
            self._event_buffer[vector] = self._event_buffer[vector][-self._max_buffer:]
            self._event_details[vector] = self._event_details[vector][-self._max_buffer:]

    def get_state(self) -> dict:
        """Get current threat state as serializable dict."""
        with self._lock:
            s = self._state
            return {
                "level": s.level.value,
                "level_name": s.level_name,
                "overall_score": round(s.overall_score, 1),
                "vectors": {
                    k: {
                        "name": v.name,
                        "score": round(v.score, 1),
                        "events_1m": v.events_1m,
                        "events_5m": v.events_5m,
                        "events_1h": v.events_1h,
                        "last_detail": v.last_detail,
                        "blocked": v.blocked_count,
                        "critical": v.critical_count,
                    }
                    for k, v in s.vectors.items()
                },
                "insights": [
                    {"title": i.title, "detail": i.detail,
                     "vectors": i.vectors, "severity": i.severity,
                     "age_s": round(time.time() - i.timestamp)}
                    for i in self._insights if time.time() - i.timestamp < i.ttl
                ],
                "auto_actions": s.auto_actions[-10:],
                "last_update": s.last_update,
            }

    def _run_loop(self):
        """Main correlation loop."""
        while self._running:
            try:
                self._update_vectors()
                self._compute_scores()
                self._detect_correlations()
                self._determine_level()
            except Exception as e:
                logger.error(f"Correlation loop error: {e}")
            time.sleep(5)

    def _count_events(self, vector: str, window_s: float) -> int:
        cutoff = time.time() - window_s
        return sum(1 for t in self._event_buffer.get(vector, []) if t > cutoff)

    def _update_vectors(self):
        """Pull latest data from all sources."""
        now = time.time()

        for vec_name in ("waf", "wifi", "ble", "vision"):
            vec = self._state.vectors[vec_name]
            vec.events_1m = self._count_events(vec_name, 60)
            vec.events_5m = self._count_events(vec_name, 300)
            vec.events_1h = self._count_events(vec_name, 3600)

            recent = [d for d in self._event_details.get(vec_name, [])
                      if now - d["ts"] < 300]
            if recent:
                vec.last_event = recent[-1]["ts"]
                vec.last_detail = recent[-1]["detail"]
                vec.blocked_count = sum(1 for d in recent if d.get("blocked"))
                vec.critical_count = sum(1 for d in recent
                                         if d.get("severity") in ("high", "critical"))

        # Pull WAF stats from security_feed if available
        if self._waf_fetcher:
            try:
                waf_data = self._waf_fetcher()
                if waf_data and isinstance(waf_data, dict):
                    waf_recent = waf_data.get("recent_attacks", [])
                    for atk in waf_recent:
                        sev = atk.get("severity", "medium")
                        self.push_event("waf", sev,
                                       f"{atk.get('threat_type','?')} from {atk.get('ip','?')}",
                                       atk.get("blocked", False))
            except Exception:
                pass

        # Pull WiFi stats
        if self._wifi_stats:
            try:
                wifi_data = self._wifi_stats()
                if wifi_data:
                    deauth = wifi_data.get("deauth_detected", 0)
                    if deauth > 0:
                        self.push_event("wifi", "critical",
                                       f"Deauth frames detected: {deauth}")
                    evil = wifi_data.get("evil_twins", 0)
                    if evil > 0:
                        self.push_event("wifi", "critical",
                                       f"Evil twin AP detected: {evil}")
            except Exception:
                pass

    def _compute_scores(self):
        """Compute per-vector and overall threat scores."""
        for vec_name, vec in self._state.vectors.items():
            # Base score from event rate
            score = 0.0

            # Events per minute contribution (0-40 points)
            if vec.events_1m > 50:
                score += 40
            elif vec.events_1m > 20:
                score += 30
            elif vec.events_1m > 5:
                score += 20
            elif vec.events_1m > 0:
                score += 10

            # Critical events boost (0-30 points)
            if vec.critical_count > 10:
                score += 30
            elif vec.critical_count > 3:
                score += 20
            elif vec.critical_count > 0:
                score += 15

            # 5-minute trend (0-20 points)
            if vec.events_5m > 100:
                score += 20
            elif vec.events_5m > 30:
                score += 15
            elif vec.events_5m > 10:
                score += 10

            # Recency boost (0-10 points)
            if vec.last_event and time.time() - vec.last_event < 30:
                score += 10
            elif vec.last_event and time.time() - vec.last_event < 120:
                score += 5

            vec.score = min(score, 100)

        # Weighted overall score
        total = sum(
            self._state.vectors[v].score * w
            for v, w in self.VECTOR_WEIGHTS.items()
        )
        self._state.overall_score = min(total, 100)
        self._state.last_update = time.time()

    def _detect_correlations(self):
        """Find cross-vector correlations."""
        now = time.time()
        waf = self._state.vectors["waf"]
        wifi = self._state.vectors["wifi"]
        ble = self._state.vectors["ble"]
        vision = self._state.vectors["vision"]

        # Correlation 1: WiFi deauth + WAF spike = coordinated attack
        if (wifi.events_5m > 0 and wifi.critical_count > 0 and
            waf.events_5m > 10):
            self._add_insight(
                "COORDINATED ATTACK SUSPECTED",
                f"WiFi deauth ({wifi.events_5m} events) + WAF spike "
                f"({waf.events_5m} events) in 5min window. "
                "Possible coordinated physical+digital attack.",
                ["wifi", "waf"], "critical"
            )

        # Correlation 2: Unknown person + network probing
        if (vision.events_5m > 0 and waf.events_5m > 5):
            self._add_insight(
                "PHYSICAL + DIGITAL CORRELATION",
                f"Unknown person detected while {waf.events_5m} WAF events "
                f"in 5min. Could be on-site attacker.",
                ["vision", "waf"], "warning"
            )

        # Correlation 3: BLE spoofing + WiFi attack
        if (ble.critical_count > 0 and wifi.critical_count > 0):
            self._add_insight(
                "MULTI-RADIO ATTACK",
                "Both BLE and WiFi attacks detected simultaneously. "
                "Attacker may have SDR/multi-radio capability.",
                ["ble", "wifi"], "critical"
            )

        # Correlation 4: Sustained high-volume attack
        if waf.events_1h > 500 and waf.events_1m > 10:
            self._add_insight(
                "SUSTAINED ASSAULT",
                f"High-volume attack ongoing: {waf.events_1h} events/hr, "
                f"{waf.events_1m} events/min. Possible DDoS or automated scanner.",
                ["waf"], "warning"
            )

    def _add_insight(self, title: str, detail: str,
                     vectors: List[str], severity: str):
        """Add insight if not duplicate."""
        # Dedup: don't add same title within TTL
        now = time.time()
        for i in self._insights:
            if i.title == title and now - i.timestamp < i.ttl:
                return  # Already exists and not expired
        insight = CorrelationInsight(
            timestamp=now, title=title, detail=detail,
            vectors=vectors, severity=severity
        )
        self._insights.append(insight)
        if len(self._insights) > 50:
            self._insights = self._insights[-50:]

        if self._on_insight:
            try:
                self._on_insight(insight)
            except Exception:
                pass

        logger.warning(f"INSIGHT [{severity}] {title}: {detail}")

    def _determine_level(self):
        """Determine DEFCON level from overall score."""
        score = self._state.overall_score
        old_level = self._state.level

        for level, threshold in sorted(self.LEVEL_THRESHOLDS.items()):
            if score >= threshold:
                new_level = level
                break
        else:
            new_level = ThreatLevel.DEFCON5

        level_names = {
            ThreatLevel.DEFCON1: "MAXIMUM",
            ThreatLevel.DEFCON2: "HIGH",
            ThreatLevel.DEFCON3: "INCREASED",
            ThreatLevel.DEFCON4: "ELEVATED",
            ThreatLevel.DEFCON5: "PEACE",
        }

        with self._lock:
            self._state.level = new_level
            self._state.level_name = level_names.get(new_level, "UNKNOWN")

        if new_level != old_level:
            logger.warning(f"THREAT LEVEL CHANGED: {level_names.get(old_level)} -> "
                          f"{level_names.get(new_level)} (score: {score:.1f})")
            if self._on_level_change:
                try:
                    self._on_level_change(old_level, new_level, score)
                except Exception:
                    pass
