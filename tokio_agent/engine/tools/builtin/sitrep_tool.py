"""
TokioAI SITREP Tool — Unified Situational Awareness Report.

One command to see EVERYTHING:
  - All system health (Entity, GCP, Drone, HA, BLE)
  - Unified threat level (DEFCON)
  - WAF stats + active attacks
  - WiFi defense status
  - Health monitor readings
  - Active correlations/insights
  - Self-healing status
  - Resource usage

This replaces calling 5+ separate tools to understand system state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

RASPI_API = os.getenv("RASPI_API_URL", "")
DRONE_PROXY = os.getenv("DRONE_PROXY_URL", "")
_GCP_SSH_KEY = os.path.expanduser("~/.ssh/google_compute_engine")
_GCP_HOST = "osboxes@35.225.133.230"
_RASPI_SSH_KEY = os.path.expanduser("~/.ssh/id_rsa_raspberry")
_RASPI_HOST = "mrmoz@192.168.8.161"


async def _quick_get(url: str, timeout: float = 5.0) -> Optional[dict]:
    """Quick HTTP GET with timeout."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def _ssh_cmd(host: str, key: str, cmd: str, timeout: int = 10) -> Optional[str]:
    """Quick SSH command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-i", key, "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            host, cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
        return None
    except Exception:
        return None


async def sitrep(action: str = "full", params: dict = None, **kwargs) -> str:
    """
    Unified situational awareness report.
    
    action:
      full    — Complete SITREP (all systems)
      quick   — Quick health check (no details)
      threats — Threat-focused view
      health  — Health data focused
      infra   — Infrastructure focused
    """
    if params is None:
        params = {}

    if action == "quick":
        return await _quick_sitrep()
    elif action == "threats":
        return await _threat_sitrep()
    elif action == "health":
        return await _health_sitrep()
    elif action == "infra":
        return await _infra_sitrep()
    else:
        return await _full_sitrep()


async def _full_sitrep() -> str:
    """Complete situational awareness report."""
    lines = ["═══ TOKIOAI SITREP ═══", ""]

    # Gather all data in parallel
    entity_task = _quick_get(f"{RASPI_API}/status") if RASPI_API else None
    security_task = _quick_get(f"{RASPI_API}/security/dashboard") if RASPI_API else None
    threat_task = _quick_get(f"{RASPI_API}/threat/status") if RASPI_API else None
    health_task = _quick_get(f"{RASPI_API}/health/status") if RASPI_API else None
    wifi_task = _quick_get(f"{RASPI_API}/wifi/status") if RASPI_API else None
    drone_task = _quick_get(f"{DRONE_PROXY}/drone/status") if DRONE_PROXY else None
    ha_task = _quick_get(f"{RASPI_API}/ha/status") if RASPI_API else None

    tasks = [t for t in [entity_task, security_task, threat_task,
                          health_task, wifi_task, drone_task, ha_task] if t]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    idx = 0
    entity = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    security = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    threat = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    health = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    wifi = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    drone = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1
    ha = results[idx] if idx < len(results) and not isinstance(results[idx], Exception) else None; idx += 1

    # ── THREAT LEVEL ──
    if threat and isinstance(threat, dict) and "level_name" in threat:
        level = threat.get("level", 5)
        level_name = threat.get("level_name", "UNKNOWN")
        score = threat.get("overall_score", 0)
        level_icon = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🔵", 5: "🟢"}.get(level, "⚪")
        lines.append(f"THREAT LEVEL: {level_icon} DEFCON {level} — {level_name} (score: {score:.0f}/100)")

        # Vector breakdown
        vectors = threat.get("vectors", {})
        if vectors:
            vec_parts = []
            for vname, vdata in vectors.items():
                vscore = vdata.get("score", 0)
                if vscore > 0:
                    vec_parts.append(f"{vname}:{vscore:.0f}")
            if vec_parts:
                lines.append(f"  Vectors: {' | '.join(vec_parts)}")

        # Insights
        insights = threat.get("insights", [])
        if insights:
            lines.append(f"  Insights:")
            for i in insights[:3]:
                lines.append(f"    ⚡ [{i['severity']}] {i['title']}")
    else:
        lines.append("THREAT LEVEL: ⚪ Not available (threat engine offline)")

    lines.append("")

    # ── ENTITY STATUS ──
    lines.append("─── ENTITY ───")
    if entity and isinstance(entity, dict):
        v = entity.get("vision", {})
        lines.append(f"  Status: ✅ ONLINE")
        lines.append(f"  Camera: {'✅' if v.get('camera_open') else '❌'} | "
                     f"Hailo: {'✅' if v.get('hailo_available') else '❌'} | "
                     f"FPS: {v.get('fps', 0):.1f}")
        lines.append(f"  Emotion: {entity.get('emotion')} | "
                     f"Persons: {entity.get('persons_detected', 0)} | "
                     f"Known faces: {entity.get('faces_known', 0)}")
        lines.append(f"  Security feed: {'✅' if entity.get('security_connected') else '❌'}")
    else:
        lines.append("  Status: ❌ OFFLINE")

    lines.append("")

    # ── WAF / CLOUD DEFENSE ──
    lines.append("─── WAF / CLOUD ───")
    if security and isinstance(security, dict):
        waf = security.get("waf", {})
        if waf:
            lines.append(f"  Total attacks: {waf.get('total_attacks', 0):,}")
            lines.append(f"  Blocked: {waf.get('blocked', 0):,} | "
                        f"Active blocks: {waf.get('active_blocks', 0)} IPs")
            lines.append(f"  Critical: {waf.get('critical', 0):,} | "
                        f"High: {waf.get('high', 0):,}")

        recent = security.get("recent_attacks", [])
        if recent:
            lines.append(f"  Last attacks:")
            for a in recent[:3]:
                icon = "🛑" if a.get("blocked") else "⚠️"
                lines.append(f"    {icon} {a.get('ip')} → {a.get('uri', '/')} "
                           f"[{a.get('threat_type', '?')}]")
    else:
        lines.append("  Status: ❌ Not connected")

    lines.append("")

    # ── WIFI DEFENSE ──
    lines.append("─── WIFI DEFENSE ───")
    if wifi and isinstance(wifi, dict) and wifi.get("available"):
        lines.append(f"  Monitoring: {'✅' if wifi.get('monitoring') else '❌'}")
        lines.append(f"  Deauth detected: {wifi.get('deauth_detected', 0)} | "
                     f"Evil twins: {wifi.get('evil_twins', 0)}")
        lines.append(f"  Counter-deauth: {'✅ ON' if wifi.get('counter_deauth') else '❌ OFF'}")
        lines.append(f"  Mitigations: {wifi.get('mitigations', 0)}")
    else:
        lines.append("  Status: ❌ Not available")

    lines.append("")

    # ── HEALTH MONITOR ──
    lines.append("─── HEALTH ───")
    if health and isinstance(health, dict) and health.get("available"):
        d = health
        hr = d.get("heart_rate", 0)
        bp_s = d.get("blood_pressure_sys", 0)
        bp_d = d.get("blood_pressure_dia", 0)
        spo2 = d.get("spo2", 0)
        lines.append(f"  HR: {hr} bpm | BP: {bp_s}/{bp_d} mmHg | SpO2: {spo2}%")
        lines.append(f"  Watch: {d.get('watch_name', '?')} | "
                     f"Battery: {d.get('battery', '?')}%")
    else:
        lines.append("  Status: ❌ Not connected")

    lines.append("")

    # ── DRONE ──
    lines.append("─── DRONE ───")
    if drone and isinstance(drone, dict):
        lines.append(f"  Proxy: ✅ ONLINE")
        lines.append(f"  Safety: {drone.get('safety_level', '?')} | "
                     f"Geofence: {drone.get('geofence_cm', '?')}cm")
        lines.append(f"  WiFi: {'✅' if drone.get('wifi_connected') else '❌'} | "
                     f"Flying: {'✅' if drone.get('is_flying') else '❌'}")
    else:
        lines.append("  Proxy: ❌ OFFLINE")

    lines.append("")

    # ── PICAR-X ──
    lines.append("─── PICAR-X ROBOT ───")
    picar = await _quick_get("http://192.168.8.107:5002/status")
    if picar and isinstance(picar, dict) and picar.get("initialized"):
        lines.append(f"  Hardware: ✅ Online")
        lines.append(f"  Battery: {picar.get('battery_v', '?')}V | "
                     f"Ultrasonic: {picar.get('ultrasonic_cm', '?')}cm")
        mode = picar.get('autonomous_mode')
        if picar.get('moving'):
            lines.append(f"  Moving: 🟢 {picar.get('direction', '?')} @ {picar.get('speed', 0)}%")
        elif mode:
            lines.append(f"  Mode: {mode}")
        else:
            lines.append(f"  Status: ⚪ Idle | Commands: {picar.get('commands', 0)}")
    elif picar:
        lines.append(f"  Hardware: ❌ Not initialized")
    else:
        lines.append("  Proxy: ❌ OFFLINE (Raspi off?)")
    lines.append("")

    # ── HOME ASSISTANT ──
    lines.append("─── HOME ASSISTANT ───")
    if ha and isinstance(ha, dict) and ha.get("available", ha.get("connected")):
        lines.append(f"  Status: ✅ Connected")
        entities = ha.get("entities", ha.get("entity_count", "?"))
        lines.append(f"  Entities: {entities}")
    else:
        lines.append("  Status: ❌ Not connected")

    lines.append("")

    # ── GCP CONTAINERS ──
    lines.append("─── GCP INFRASTRUCTURE ───")
    try:
        containers = await _ssh_cmd(
            _GCP_HOST, _GCP_SSH_KEY,
            "sudo docker ps --format '{{.Names}}:{{.Status}}' 2>/dev/null"
        )
        if containers:
            for line in containers.strip().split("\n"):
                parts = line.split(":", 1)
                name = parts[0] if parts else "?"
                status = parts[1] if len(parts) > 1 else "?"
                icon = "✅" if "Up" in status else "❌"
                lines.append(f"  {icon} {name}: {status}")
        else:
            lines.append("  ❌ Cannot reach GCP")
    except Exception:
        lines.append("  ❌ Cannot reach GCP")

    lines.append("")
    lines.append("═══ END SITREP ═══")

    return "\n".join(lines)


async def _quick_sitrep() -> str:
    """Quick health check — just up/down for everything."""
    checks = {}

    tasks = {}
    if RASPI_API:
        tasks["entity"] = _quick_get(f"{RASPI_API}/status")
    if DRONE_PROXY:
        tasks["drone"] = _quick_get(f"{DRONE_PROXY}/drone/status")

    results = {}
    if tasks:
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for name, result in zip(tasks.keys(), gathered):
            results[name] = result if not isinstance(result, Exception) else None

    entity = results.get("entity")
    drone = results.get("drone")

    lines = ["QUICK SITREP:"]
    lines.append(f"  Entity: {'✅' if entity else '❌'}")
    if entity:
        v = entity.get("vision", {})
        lines.append(f"    Camera: {'✅' if v.get('camera_open') else '❌'} "
                     f"Hailo: {'✅' if v.get('hailo_available') else '❌'} "
                     f"FPS: {v.get('fps', 0):.0f}")
    lines.append(f"  Drone: {'✅' if drone else '❌'}")
    lines.append(f"  Security feed: {'✅' if entity and entity.get('security_connected') else '❌'}")

    return "\n".join(lines)


async def _threat_sitrep() -> str:
    """Threat-focused view."""
    if not RASPI_API:
        return "RASPI_API_URL not configured"

    threat = await _quick_get(f"{RASPI_API}/threat/status")
    security = await _quick_get(f"{RASPI_API}/security/dashboard")
    defense = await _quick_get(f"{RASPI_API}/defense/status")

    lines = ["═══ THREAT SITREP ═══", ""]

    if threat and isinstance(threat, dict):
        level = threat.get("level", 5)
        lines.append(f"DEFCON {level}: {threat.get('level_name')} "
                     f"(score: {threat.get('overall_score', 0):.0f}/100)")
        lines.append("")
        for vname, vdata in threat.get("vectors", {}).items():
            score = vdata.get("score", 0)
            bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            lines.append(f"  {vdata.get('name', vname):15s} [{bar}] {score:.0f}")
            if vdata.get("events_5m", 0) > 0:
                lines.append(f"    └─ {vdata['events_5m']} events/5m, "
                           f"{vdata.get('critical', 0)} critical")

        insights = threat.get("insights", [])
        if insights:
            lines.append("")
            lines.append("CORRELATIONS:")
            for i in insights:
                lines.append(f"  ⚡ [{i['severity']}] {i['title']}")
                lines.append(f"    {i['detail']}")
    else:
        lines.append("Threat engine not responding")

    if defense and isinstance(defense, dict):
        lines.append("")
        lines.append(f"DEFENSE ACTIONS: {defense.get('total_actions', 0)} total")
        for a in defense.get("recent_actions", [])[:5]:
            lines.append(f"  • {a.get('action')}: {a.get('detail')}")

    return "\n".join(lines)


async def _health_sitrep() -> str:
    """Health-focused view."""
    if not RASPI_API:
        return "RASPI_API_URL not configured"

    health = await _quick_get(f"{RASPI_API}/health/report")
    if not health or not isinstance(health, dict):
        return "Health monitor not responding"

    lines = ["═══ HEALTH SITREP ═══", ""]

    if health.get("available"):
        current = health.get("current", {})
        lines.append(f"Heart Rate: {current.get('heart_rate', 0)} bpm")
        lines.append(f"Blood Pressure: {current.get('bp_sys', 0)}/{current.get('bp_dia', 0)} mmHg")
        lines.append(f"SpO2: {current.get('spo2', 0)}%")
        lines.append(f"Steps: {current.get('steps', 0)}")

        assessment = health.get("assessment", "")
        if assessment:
            lines.append(f"\nAssessment: {assessment}")

        daily = health.get("daily_stats", {})
        if daily:
            lines.append("\n7-DAY HISTORY:")
            for day, data in list(daily.items())[:7]:
                hr = data.get("hr", {})
                if hr:
                    lines.append(f"  {day}: HR {hr.get('min', 0)}-{hr.get('max', 0)} "
                               f"(avg {hr.get('avg', 0)})")

    return "\n".join(lines)


async def _infra_sitrep() -> str:
    """Infrastructure-focused view."""
    lines = ["═══ INFRA SITREP ═══", ""]

    # GCP containers
    containers = await _ssh_cmd(
        _GCP_HOST, _GCP_SSH_KEY,
        "sudo docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null"
    )

    if containers:
        lines.append("GCP CONTAINERS:")
        for line in containers.strip().split("\n"):
            parts = line.split("|")
            name = parts[0] if parts else "?"
            status = parts[1] if len(parts) > 1 else "?"
            icon = "✅" if "Up" in status else "❌"
            lines.append(f"  {icon} {name}: {status}")
    else:
        lines.append("GCP: ❌ Unreachable")

    # Raspi system info
    raspi_info = await _ssh_cmd(
        _RASPI_HOST, _RASPI_SSH_KEY,
        "echo \"CPU: $(cat /sys/class/thermal/thermal_zone0/temp | "
        "awk '{printf \"%.1f\", $1/1000}')°C | "
        "MEM: $(free -m | awk '/Mem:/{printf \"%d/%dMB\", $3, $2}') | "
        "DISK: $(df -h / | awk 'NR==2{print $5}') | "
        "UPTIME: $(uptime -p)\""
    )

    lines.append("")
    lines.append("RASPBERRY PI:")
    if raspi_info:
        lines.append(f"  {raspi_info}")
    else:
        lines.append("  ❌ Unreachable")

    return "\n".join(lines)
