"""
Router Tools — Universal SSH-based control for OpenWrt/GL.iNet routers.

Configure ROUTER_HOST, ROUTER_USER, ROUTER_SSH_KEY_PATH in .env.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from typing import Any, Dict, Optional

_ACTIONS = (
    "health", "firewall_status", "wifi_status", "detect_attack_signals",
    "wifi_defense_status", "wifi_defense_harden", "recover_wifi",
    "add_block_ip", "remove_block_ip", "run",
)


def _cfg() -> Dict[str, Any]:
    return {
        "host": os.getenv("ROUTER_HOST", "").strip(),
        "user": os.getenv("ROUTER_USER", "root").strip(),
        "port": int(os.getenv("ROUTER_PORT", "22")),
        "ssh_key_path": os.getenv("ROUTER_SSH_KEY_PATH", "").strip(),
        "connect_timeout": int(os.getenv("ROUTER_CONNECT_TIMEOUT", "8")),
        "cmd_timeout": int(os.getenv("ROUTER_CMD_TIMEOUT", "45")),
    }


def _ssh_cmd(cfg: Dict, remote: str) -> list:
    cmd = [
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ConnectTimeout={cfg['connect_timeout']}", "-p", str(cfg["port"]),
    ]
    if cfg["ssh_key_path"]:
        cmd += ["-i", cfg["ssh_key_path"]]
    cmd += [f"{cfg['user']}@{cfg['host']}", remote]
    return cmd


def _run(cfg: Dict, remote: str, timeout: Optional[int] = None) -> str:
    p = subprocess.run(
        _ssh_cmd(cfg, remote), capture_output=True, text=True,
        timeout=timeout or cfg["cmd_timeout"],
    )
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise RuntimeError(err or out or f"SSH failed ({p.returncode})")
    return out


def _validate_ip(ip: str) -> bool:
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip or ""))


def _count(text: str, pattern: str) -> int:
    try:
        return len(re.findall(pattern, text or "", flags=re.IGNORECASE))
    except Exception:
        return 0


def router_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Universal router control tool.

    Actions: health, firewall_status, wifi_status, detect_attack_signals,
             wifi_defense_status, wifi_defense_harden, recover_wifi,
             add_block_ip, remove_block_ip, run
    """
    params = params or {}
    cfg = _cfg()
    if not cfg["host"]:
        return json.dumps({"success": False, "error": "ROUTER_HOST no configurado"}, ensure_ascii=False)

    try:
        if action == "health":
            result = {"uname": _run(cfg, "uname -a"), "uptime": _run(cfg, "uptime")}

        elif action == "firewall_status":
            result = {
                "uci": _run(cfg, "uci show firewall || true"),
                "iptables": _run(cfg, "iptables -L -n -v --line-numbers || true"),
            }

        elif action == "wifi_status":
            result = {
                "wifi_info": _run(cfg, "iwinfo || true"),
                "interfaces": _run(cfg, "ip a || ifconfig || true"),
                "logs": _run(cfg, "logread | tail -n 200 | grep -Ei 'wifi|wlan|hostapd|deauth|assoc|auth' || true"),
            }

        elif action == "detect_attack_signals":
            result = {
                "drop_scan": _run(cfg, "logread | tail -n 300 | grep -Ei 'DROP|REJECT|scan|flood|DoS|SYN' || true"),
                "auth": _run(cfg, "logread | tail -n 300 | grep -Ei 'auth|deauth|wpa|bruteforce|invalid' || true"),
            }

        elif action == "wifi_defense_status":
            raw = _run(cfg, "logread | tail -n 500 | grep -Ei 'deauth|disassoc|assoc|auth|flood|scan|brute|invalid|wpa|probe' || true")
            deauth = _count(raw, r"deauth|disassoc")
            scan = _count(raw, r"scan|probe")
            brute = _count(raw, r"brute|invalid|auth fail|wrong password")
            risk = "high" if deauth >= 8 or brute >= 8 else "medium" if deauth >= 3 or scan >= 5 or brute >= 3 else "low"
            result = {"risk_level": risk, "deauth": deauth, "scan": scan, "brute": brute, "logs": raw}

        elif action == "wifi_defense_harden":
            if not params.get("confirm"):
                raise ValueError("wifi_defense_harden requiere params.confirm=true")
            result = _run(cfg,
                "uci set wireless.@wifi-iface[0].wpa_disable_eapol_key_retries='0' 2>/dev/null || true; "
                "uci set firewall.@defaults[0].drop_invalid='1' 2>/dev/null || true; "
                "uci commit wireless; uci commit firewall; "
                "wifi reload 2>/dev/null || wifi; /etc/init.d/firewall reload",
                timeout=max(90, cfg["cmd_timeout"]),
            )

        elif action == "recover_wifi":
            result = _run(cfg, "wifi down; sleep 2; wifi up; /etc/init.d/network restart",
                          timeout=max(90, cfg["cmd_timeout"]))

        elif action == "add_block_ip":
            ip = str(params.get("ip", "")).strip()
            if not _validate_ip(ip):
                raise ValueError("IP inválida")
            ip_esc = shlex.quote(ip)
            result = _run(cfg,
                "uci add firewall rule; "
                "uci set firewall.@rule[-1].name='tokio_block_ip'; "
                "uci set firewall.@rule[-1].src='wan'; "
                f"uci set firewall.@rule[-1].src_ip={ip_esc}; "
                "uci set firewall.@rule[-1].target='DROP'; "
                "uci commit firewall; /etc/init.d/firewall reload",
                timeout=max(60, cfg["cmd_timeout"]),
            )

        elif action == "remove_block_ip":
            ip = str(params.get("ip", "")).strip()
            if not _validate_ip(ip):
                raise ValueError("IP inválida")
            ip_esc = shlex.quote(ip)
            result = _run(cfg,
                f"for i in $(uci show firewall | grep 'src_ip' | grep {ip_esc} | cut -d'=' -f1 | sed 's/\\.src_ip//'); do "
                "uci delete $i; done; uci commit firewall; /etc/init.d/firewall reload",
                timeout=max(60, cfg["cmd_timeout"]),
            )

        elif action == "run":
            cmd = str(params.get("command", "")).strip()
            if not cmd:
                raise ValueError("params.command es obligatorio")
            result = _run(cfg, cmd, timeout=cfg["cmd_timeout"])

        else:
            return json.dumps({
                "success": False,
                "error": f"Acción no soportada: {action}",
                "supported": list(_ACTIONS),
            }, ensure_ascii=False)

        return json.dumps({"success": True, "action": action, "result": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "action": action, "error": str(e)}, ensure_ascii=False)
