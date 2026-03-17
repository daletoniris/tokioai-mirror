"""
TokioAI Security & Pentest Tools — Offensive + Defensive capabilities.

Provides network scanning, WiFi monitoring, vulnerability assessment,
and real-time security monitoring capabilities.

All operations are authorized-context only (pentesting, CTF, defense).
Runs commands on the Raspi or locally via SSH.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RASPI_IP = os.getenv("RASPI_IP", "YOUR_RASPI_TAILSCALE_IP")
RASPI_SSH_KEY = os.getenv("RASPI_SSH_KEY", "/keys/id_rsa_raspberry")
RASPI_USER = os.getenv("RASPI_USER", "mrmoz")


def _local_cmd(cmd: str, timeout: int = 30) -> str:
    """Run a command locally."""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = r.stdout.strip()
        if r.returncode != 0 and r.stderr.strip():
            output += f"\nSTDERR: {r.stderr.strip()}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _raspi_cmd(cmd: str, timeout: int = 30) -> str:
    """Run a command on the Raspi via SSH."""
    ssh_cmd = (
        f"ssh -i {RASPI_SSH_KEY} -o ConnectTimeout=10 "
        f"-o StrictHostKeyChecking=no -o BatchMode=yes "
        f"{RASPI_USER}@{RASPI_IP} {json.dumps(cmd)}"
    )
    return _local_cmd(ssh_cmd, timeout)


# ===========================================================================
# Network Scanning & Reconnaissance
# ===========================================================================

def security_nmap(params: Dict[str, Any]) -> str:
    """
    Escaneo de red con nmap. Para reconocimiento de red autorizado.
    params:
      target: IP, rango CIDR, o hostname (ej: 192.168.8.0/24)
      scan_type: quick|full|vuln|os|ports|stealth (default: quick)
      ports: puertos especificos (ej: "80,443,8080" o "1-1000")
    """
    target = params.get("target", "")
    if not target:
        return json.dumps({"ok": False, "error": "Falta 'target' (IP o rango)"})

    scan_type = params.get("scan_type", "quick")
    ports = params.get("ports", "")

    scan_flags = {
        "quick": "-sn",                      # ping scan only
        "full": "-sV -sC -O",               # version + scripts + OS
        "vuln": "--script vuln",             # vulnerability scripts
        "os": "-O -sV",                      # OS detection + versions
        "ports": f"-sV -p {ports}" if ports else "-sV --top-ports 1000",
        "stealth": "-sS -T2 -f",            # SYN stealth scan
        "service": "-sV --version-intensity 5",  # deep service detection
        "udp": "-sU --top-ports 100",        # UDP scan
    }.get(scan_type, "-sn")

    if ports and scan_type not in ("ports",):
        scan_flags += f" -p {ports}"

    cmd = f"nmap {scan_flags} {target} 2>&1"
    output = _local_cmd(cmd, timeout=120)

    return json.dumps({
        "ok": True,
        "command": f"nmap {scan_flags} {target}",
        "scan_type": scan_type,
        "target": target,
        "result": output,
    }, ensure_ascii=False, indent=2)


def security_wifi_scan(params: Dict[str, Any]) -> str:
    """
    Escaneo de redes WiFi desde la Raspi. Detecta todas las redes cercanas.
    params:
      band: all|2.4|5 (default: all)
      detail: basic|full (default: basic)
    """
    detail = params.get("detail", "basic")

    if detail == "full":
        cmd = "sudo iw dev wlan0 scan 2>/dev/null | grep -E 'BSS |SSID:|signal:|freq:|capability:' | head -100"
    else:
        cmd = "nmcli device wifi list --rescan yes 2>/dev/null"

    output = _raspi_cmd(cmd, timeout=20)

    return json.dumps({
        "ok": True,
        "command": "wifi_scan",
        "result": output,
    }, ensure_ascii=False, indent=2)


def security_wifi_monitor(params: Dict[str, Any]) -> str:
    """
    Monitoreo de seguridad WiFi. Detecta ataques y anomalias.
    params:
      action: status|scan_threats|check_deauth|connected_devices|signal_history
    """
    action = params.get("action", "status")

    if action == "status":
        cmd = (
            "echo '=== WiFi Status ==='; "
            "nmcli -t -f NAME,DEVICE,TYPE connection show --active 2>/dev/null; "
            "echo '=== Signal ==='; "
            "iwconfig wlan0 2>/dev/null | grep -i 'signal\\|essid\\|freq'; "
            "echo '=== IP ==='; "
            "ip addr show wlan0 2>/dev/null | grep inet"
        )
    elif action == "scan_threats":
        cmd = (
            "echo '=== Nearby Networks ==='; "
            "sudo iw dev wlan0 scan 2>/dev/null | grep -E 'SSID:|signal:' | head -40; "
            "echo '=== Suspicious (TELLO/T0K10 clones) ==='; "
            "sudo iw dev wlan0 scan 2>/dev/null | grep -i 'tello\\|t0k10\\|tokio' | head -10; "
            "echo '=== Open Networks ==='; "
            "nmcli device wifi list 2>/dev/null | grep -i '\\-\\-' | head -10"
        )
    elif action == "check_deauth":
        cmd = (
            "echo '=== Connection Drops (dmesg) ==='; "
            "dmesg | grep -i 'deauth\\|disassoc\\|disconnect' | tail -20; "
            "echo '=== WiFi Events ==='; "
            "journalctl --no-pager -n 20 -u NetworkManager 2>/dev/null | grep -i 'wifi\\|wlan\\|disconnect\\|connect'"
        )
    elif action == "connected_devices":
        cmd = (
            "echo '=== ARP Table ==='; "
            "arp -a 2>/dev/null; "
            "echo '=== Active Connections ==='; "
            "ss -tuln 2>/dev/null | head -20"
        )
    elif action == "signal_history":
        cmd = (
            "for i in $(seq 1 5); do "
            "  iwconfig wlan0 2>/dev/null | grep 'Signal level' | awk '{print $4}'; "
            "  sleep 1; "
            "done"
        )
    else:
        return json.dumps({"ok": False, "error": f"Unknown action: {action}"})

    output = _raspi_cmd(cmd, timeout=30)
    return json.dumps({
        "ok": True,
        "action": action,
        "result": output,
    }, ensure_ascii=False, indent=2)


# ===========================================================================
# Vulnerability Assessment
# ===========================================================================

def security_vuln_scan(params: Dict[str, Any]) -> str:
    """
    Escaneo de vulnerabilidades basico.
    params:
      target: IP o hostname
      type: web|ssl|headers|dns|all (default: web)
    """
    target = params.get("target", "")
    if not target:
        return json.dumps({"ok": False, "error": "Falta 'target'"})

    scan_type = params.get("type", "web")
    results = []

    if scan_type in ("web", "all"):
        # HTTP headers check
        r = _local_cmd(f"curl -sI -m 10 {target} 2>&1 | head -30", timeout=15)
        results.append({"check": "HTTP Headers", "result": r})

    if scan_type in ("ssl", "all"):
        # SSL/TLS check
        host = target.replace("https://", "").replace("http://", "").split("/")[0]
        r = _local_cmd(
            f"echo | openssl s_client -connect {host}:443 -servername {host} 2>/dev/null | "
            f"openssl x509 -noout -subject -dates -issuer 2>&1",
            timeout=15
        )
        results.append({"check": "SSL Certificate", "result": r})

        # Check for weak ciphers
        r2 = _local_cmd(
            f"nmap --script ssl-enum-ciphers -p 443 {host} 2>&1 | tail -30",
            timeout=30
        )
        results.append({"check": "SSL Ciphers", "result": r2})

    if scan_type in ("headers", "all"):
        # Security headers analysis
        r = _local_cmd(
            f"curl -sI -m 10 {target} 2>&1 | grep -iE "
            f"'strict-transport|content-security|x-frame|x-content-type|"
            f"x-xss|referrer-policy|permissions-policy|feature-policy'",
            timeout=15
        )
        missing = []
        important_headers = [
            "Strict-Transport-Security", "Content-Security-Policy",
            "X-Frame-Options", "X-Content-Type-Options",
            "Referrer-Policy", "Permissions-Policy"
        ]
        for h in important_headers:
            if h.lower() not in r.lower():
                missing.append(h)
        results.append({
            "check": "Security Headers",
            "present": r or "(none found)",
            "missing": missing,
        })

    if scan_type in ("dns", "all"):
        host = target.replace("https://", "").replace("http://", "").split("/")[0]
        r = _local_cmd(f"dig {host} ANY +short 2>&1", timeout=10)
        results.append({"check": "DNS Records", "result": r})

        # Check for zone transfer
        r2 = _local_cmd(f"dig axfr {host} 2>&1 | head -20", timeout=10)
        results.append({"check": "Zone Transfer", "result": r2})

    return json.dumps({
        "ok": True,
        "target": target,
        "scan_type": scan_type,
        "results": results,
    }, ensure_ascii=False, indent=2)


# ===========================================================================
# Network Analysis
# ===========================================================================

def security_net_analysis(params: Dict[str, Any]) -> str:
    """
    Analisis de red: ARP, rutas, puertos abiertos, conexiones.
    params:
      action: arp|routes|ports|connections|interfaces|tailscale|traceroute
      target: (para traceroute) IP o hostname
    """
    action = params.get("action", "arp")
    target = params.get("target", "")

    cmds = {
        "arp": "arp -a 2>/dev/null || ip neigh show",
        "routes": "ip route show",
        "ports": "ss -tuln | head -40",
        "connections": "ss -tun | head -40",
        "interfaces": "ip addr show",
        "tailscale": "tailscale status",
        "traceroute": f"traceroute -m 15 -w 2 {target} 2>&1" if target else "echo 'Falta target'",
        "dns": "cat /etc/resolv.conf; echo '---'; resolvectl status 2>/dev/null | head -20",
        "firewall": "sudo iptables -L -n --line-numbers 2>&1 | head -40",
    }

    cmd = cmds.get(action, "echo 'Unknown action'")
    where = params.get("where", "local")

    if where == "raspi":
        output = _raspi_cmd(cmd, timeout=30)
    else:
        output = _local_cmd(cmd, timeout=30)

    return json.dumps({
        "ok": True,
        "action": action,
        "where": where,
        "result": output,
    }, ensure_ascii=False, indent=2)


# ===========================================================================
# Password & Credential Testing
# ===========================================================================

def security_password_audit(params: Dict[str, Any]) -> str:
    """
    Auditoria de passwords y credenciales.
    params:
      action: strength|hash_crack|ssh_audit
      password: (para strength) password a evaluar
      hash: (para hash_crack) hash a identificar
      target: (para ssh_audit) IP del servidor SSH
    """
    action = params.get("action", "strength")

    if action == "strength":
        password = params.get("password", "")
        if not password:
            return json.dumps({"ok": False, "error": "Falta 'password'"})

        import re
        score = 0
        checks = []
        if len(password) >= 8: score += 1; checks.append("length >= 8")
        if len(password) >= 12: score += 1; checks.append("length >= 12")
        if len(password) >= 16: score += 1; checks.append("length >= 16")
        if re.search(r'[a-z]', password): score += 1; checks.append("lowercase")
        if re.search(r'[A-Z]', password): score += 1; checks.append("uppercase")
        if re.search(r'[0-9]', password): score += 1; checks.append("digits")
        if re.search(r'[^a-zA-Z0-9]', password): score += 1; checks.append("special chars")
        if not re.search(r'(.)\1{2,}', password): score += 1; checks.append("no char repetition")

        rating = "WEAK" if score < 4 else ("MEDIUM" if score < 6 else ("STRONG" if score < 8 else "EXCELLENT"))
        entropy = len(password) * 4.7  # rough estimate

        return json.dumps({
            "ok": True,
            "score": f"{score}/8",
            "rating": rating,
            "entropy_bits": round(entropy, 1),
            "checks_passed": checks,
            "length": len(password),
        }, ensure_ascii=False, indent=2)

    elif action == "hash_crack":
        hash_val = params.get("hash", "")
        if not hash_val:
            return json.dumps({"ok": False, "error": "Falta 'hash'"})

        # Identify hash type
        hash_len = len(hash_val)
        hash_types = []
        if hash_len == 32: hash_types = ["MD5", "NTLM"]
        elif hash_len == 40: hash_types = ["SHA-1"]
        elif hash_len == 64: hash_types = ["SHA-256", "SHA3-256"]
        elif hash_len == 128: hash_types = ["SHA-512", "SHA3-512"]
        elif hash_val.startswith("$2"): hash_types = ["bcrypt"]
        elif hash_val.startswith("$6$"): hash_types = ["SHA-512 (crypt)"]
        elif hash_val.startswith("$5$"): hash_types = ["SHA-256 (crypt)"]
        elif hash_val.startswith("$1$"): hash_types = ["MD5 (crypt)"]
        elif hash_val.startswith("$argon2"): hash_types = ["Argon2"]

        return json.dumps({
            "ok": True,
            "hash": hash_val[:20] + "...",
            "length": hash_len,
            "possible_types": hash_types or ["Unknown"],
            "recommendation": "Use hashcat or john for cracking" if hash_types else "Unknown format",
        }, ensure_ascii=False, indent=2)

    elif action == "ssh_audit":
        target = params.get("target", "")
        if not target:
            return json.dumps({"ok": False, "error": "Falta 'target'"})

        output = _local_cmd(f"ssh-audit {target} 2>&1 | head -50", timeout=15)
        if "command not found" in output:
            # Fallback: manual check
            output = _local_cmd(
                f"nmap --script ssh2-enum-algos -p 22 {target} 2>&1 | tail -30",
                timeout=20
            )

        return json.dumps({
            "ok": True,
            "target": target,
            "result": output,
        }, ensure_ascii=False, indent=2)

    return json.dumps({"ok": False, "error": f"Unknown action: {action}"})


# ===========================================================================
# Web Security Testing
# ===========================================================================

def security_web_test(params: Dict[str, Any]) -> str:
    """
    Testing de seguridad web.
    params:
      target: URL
      test: headers|dirs|tech|cors|methods|robots (default: headers)
    """
    target = params.get("target", "")
    if not target:
        return json.dumps({"ok": False, "error": "Falta 'target' (URL)"})

    test = params.get("test", "headers")

    if test == "headers":
        output = _local_cmd(f"curl -sI -m 10 '{target}' 2>&1", timeout=15)

    elif test == "dirs":
        # Common directory/file check
        paths = [
            "/.env", "/robots.txt", "/sitemap.xml", "/.git/config",
            "/wp-login.php", "/admin", "/api", "/.well-known/security.txt",
            "/server-status", "/phpinfo.php", "/.htaccess", "/backup.zip",
            "/config.json", "/swagger.json", "/api/docs", "/graphql",
        ]
        results = []
        for path in paths:
            url = target.rstrip("/") + path
            r = _local_cmd(f"curl -so /dev/null -w '%{{http_code}}' -m 5 '{url}' 2>&1", timeout=8)
            code = r.strip().strip("'")
            if code and code != "000" and code != "404":
                results.append(f"{code} {path}")
        output = "\n".join(results) if results else "No interesting paths found"

    elif test == "tech":
        # Technology detection
        output = _local_cmd(
            f"curl -sI -m 10 '{target}' 2>&1 | grep -iE "
            f"'server:|x-powered|x-generator|x-aspnet|x-drupal|x-shopify|via:'",
            timeout=15
        )
        if not output.strip():
            output = "No technology headers exposed (good security practice)"

    elif test == "cors":
        output = _local_cmd(
            f"curl -sI -m 10 -H 'Origin: https://evil.com' '{target}' 2>&1 | "
            f"grep -i 'access-control'",
            timeout=15
        )
        if not output.strip():
            output = "No CORS headers returned for cross-origin request"

    elif test == "methods":
        output = _local_cmd(
            f"curl -sI -m 10 -X OPTIONS '{target}' 2>&1 | grep -i 'allow'",
            timeout=15
        )
        if not output.strip():
            output = _local_cmd(
                f"for m in GET POST PUT DELETE PATCH OPTIONS TRACE HEAD; do "
                f"echo -n \"$m: \"; curl -so /dev/null -w '%{{http_code}}' -X $m -m 5 '{target}' 2>&1; "
                f"echo; done",
                timeout=30
            )

    elif test == "robots":
        output = _local_cmd(f"curl -s -m 10 '{target.rstrip('/')}/robots.txt' 2>&1 | head -30", timeout=15)

    else:
        return json.dumps({"ok": False, "error": f"Unknown test: {test}"})

    return json.dumps({
        "ok": True,
        "target": target,
        "test": test,
        "result": output,
    }, ensure_ascii=False, indent=2)


# ===========================================================================
# Main dispatcher
# ===========================================================================

def security_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Herramientas de seguridad ofensiva y defensiva de TokioAI.

    Categorias:
      Reconocimiento:
        nmap (target, scan_type: quick/full/vuln/os/ports/stealth/udp)
        wifi_scan (band, detail: basic/full)
        wifi_monitor (action: status/scan_threats/check_deauth/connected_devices/signal_history)

      Vulnerabilidades:
        vuln_scan (target, type: web/ssl/headers/dns/all)
        web_test (target, test: headers/dirs/tech/cors/methods/robots)

      Red:
        net (action: arp/routes/ports/connections/interfaces/tailscale/traceroute/dns/firewall)

      Credenciales:
        password (action: strength/hash_crack/ssh_audit, password/hash/target)
    """
    params = params or {}
    action = (action or "").strip().lower()

    handlers = {
        "nmap": security_nmap,
        "escanear": security_nmap,
        "scan": security_nmap,
        "wifi_scan": security_wifi_scan,
        "wifi": security_wifi_scan,
        "wifi_monitor": security_wifi_monitor,
        "monitor_wifi": security_wifi_monitor,
        "vuln_scan": security_vuln_scan,
        "vulnerabilidades": security_vuln_scan,
        "vuln": security_vuln_scan,
        "web_test": security_web_test,
        "web": security_web_test,
        "net": security_net_analysis,
        "red": security_net_analysis,
        "network": security_net_analysis,
        "password": security_password_audit,
        "passwd": security_password_audit,
        "credenciales": security_password_audit,
    }

    handler = handlers.get(action)
    if handler is None:
        return json.dumps({
            "ok": False,
            "error": f"Accion no soportada: {action}",
            "supported": sorted(set(handlers.keys())),
        }, ensure_ascii=False, indent=2)

    try:
        return handler(params)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"Error en {action}: {exc}",
        }, ensure_ascii=False, indent=2)
