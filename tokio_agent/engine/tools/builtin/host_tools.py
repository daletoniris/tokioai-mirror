"""
Host Control Tools — Full remote host management via SSH.

Supports Raspberry Pi and any SSH-accessible host.
Actions: status, run, reboot, services, update, cron_list, cron_add,
         cron_remove, write_file, read_file, journalctl, systemctl,
         install_packages, list_web_backends, get_public_ip,
         setup_log_retention, network_info, disk_info
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ACTIONS = (
    "status", "run", "reboot", "services", "update",
    "cron_list", "cron_add", "cron_remove",
    "write_file", "read_file",
    "journalctl", "systemctl",
    "install_packages", "list_web_backends",
    "get_public_ip", "setup_log_retention",
    "network_info", "disk_info",
)


def _get_host_config() -> Dict[str, Any]:
    """Get host connection configuration."""
    return {
        "host": os.getenv("HOST_SSH_HOST", "").strip(),
        "user": os.getenv("HOST_SSH_USER", "pi").strip(),
        "port": os.getenv("HOST_SSH_PORT", "22").strip(),
        "key_path": os.getenv("HOST_SSH_KEY_PATH", "~/.ssh/id_rsa").strip(),
        "allow_run": os.getenv("HOST_CONTROL_ALLOW_RUN", "false").lower() == "true",
        "connect_timeout": int(os.getenv("HOST_SSH_CONNECT_TIMEOUT", "10")),
        "cmd_timeout": int(os.getenv("HOST_SSH_CMD_TIMEOUT", "120")),
    }


def _ssh_base(config: Dict) -> str:
    return (
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout={config['connect_timeout']} "
        f"-i {config['key_path']} -p {config['port']} "
        f"{config['user']}@{config['host']}"
    )


async def _exec(cmd: str, timeout: int = 120) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return f"Error (exit {proc.returncode}):\n{err}\n{out}".strip()
        return out or "✅ Ejecutado sin salida"
    except asyncio.TimeoutError:
        return f"⏱️ Timeout: comando excedió {timeout}s"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


async def host_control(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Full remote host control via SSH.

    Actions:
      - status: Uptime, CPU, memory, disk
      - run: Execute command (requires HOST_CONTROL_ALLOW_RUN=true)
      - reboot: Reboot host
      - services: List running services
      - update: System update
      - cron_list: List cron jobs
      - cron_add: Add cron (params: schedule, command, comment)
      - cron_remove: Remove cron by comment (params: comment)
      - write_file: Write file on host (params: path, content)
      - read_file: Read file on host (params: path)
      - journalctl: Read journal logs (params: unit, lines)
      - systemctl: Control systemd (params: service, command=start/stop/restart/status)
      - install_packages: Install packages (params: packages)
      - list_web_backends: List web server backends
      - get_public_ip: Get host public IP
      - setup_log_retention: Configure logrotate
      - network_info: Network interfaces, routes, DNS
      - disk_info: Detailed disk usage
    """
    params = params or {}
    config = _get_host_config()
    action = (action or "").strip().lower()

    # Auto-confirm if allow_run is enabled
    if config["allow_run"] and "confirm" not in params:
        params["confirm"] = True

    if not config["host"]:
        return json.dumps({
            "ok": False,
            "error": "HOST_SSH_HOST no configurado. Configúralo en .env o ejecuta 'tokio setup'.",
        }, ensure_ascii=False)

    ssh = _ssh_base(config)
    timeout = config["cmd_timeout"]

    # Actions that need allow_run
    needs_run = {"run", "reboot", "update", "cron_add", "cron_remove",
                 "write_file", "systemctl", "install_packages", "setup_log_retention"}

    if action in needs_run and not config["allow_run"]:
        if not params.get("confirm"):
            return json.dumps({
                "ok": False,
                "error": f"⚠️ Acción '{action}' requiere HOST_CONTROL_ALLOW_RUN=true o params.confirm=true",
            }, ensure_ascii=False)

    if action == "status":
        cmd = (
            f'{ssh} "'
            "echo '=== Uptime ===' && uptime && "
            "echo '=== CPU ===' && top -bn1 | head -5 && "
            "echo '=== Memory ===' && free -h && "
            "echo '=== Disk ===' && df -h / && "
            "echo '=== Load ===' && cat /proc/loadavg"
            '"'
        )
        return await _exec(cmd, timeout)

    elif action == "run":
        remote_cmd = str(params.get("command", "")).strip()
        if not remote_cmd:
            return json.dumps({"ok": False, "error": "params.command requerido"})
        escaped = shlex.quote(remote_cmd)
        cmd = f"{ssh} {escaped}"
        return await _exec(cmd, timeout)

    elif action == "reboot":
        return await _exec(f'{ssh} "sudo reboot"', 30)

    elif action == "services":
        return await _exec(
            f'{ssh} "systemctl list-units --type=service --state=running --no-pager 2>/dev/null || '
            f'service --status-all 2>/dev/null || echo no-service-manager"',
            timeout,
        )

    elif action == "update":
        return await _exec(
            f'{ssh} "sudo apt-get update -y && sudo apt-get upgrade -y 2>&1"',
            max(300, timeout),
        )

    elif action == "cron_list":
        return await _exec(f'{ssh} "crontab -l 2>/dev/null || echo no-crontab"', timeout)

    elif action == "cron_add":
        schedule = str(params.get("schedule", "")).strip()
        command = str(params.get("command", "")).strip()
        comment = str(params.get("comment", "tokio_task")).strip()
        if not schedule or not command:
            return json.dumps({"ok": False, "error": "schedule y command requeridos"})
        line = f"{schedule} {command} # {comment}"
        escaped = shlex.quote(line)
        return await _exec(
            f'{ssh} "(crontab -l 2>/dev/null; echo {escaped}) | crontab -"',
            timeout,
        )

    elif action == "cron_remove":
        comment = str(params.get("comment", "")).strip()
        if not comment:
            return json.dumps({"ok": False, "error": "params.comment requerido"})
        return await _exec(
            f'{ssh} "crontab -l 2>/dev/null | grep -v \'# {comment}\' | crontab -"',
            timeout,
        )

    elif action == "write_file":
        path = str(params.get("path", "")).strip()
        content = str(params.get("content", ""))
        if not path:
            return json.dumps({"ok": False, "error": "params.path requerido"})
        escaped = shlex.quote(content)
        return await _exec(f'{ssh} "echo {escaped} > {path}"', timeout)

    elif action == "read_file":
        path = str(params.get("path", "")).strip()
        if not path:
            return json.dumps({"ok": False, "error": "params.path requerido"})
        lines = int(params.get("lines", 0))
        if lines > 0:
            return await _exec(f'{ssh} "tail -n {lines} {path}"', timeout)
        return await _exec(f'{ssh} "cat {path}"', timeout)

    elif action == "journalctl":
        unit = str(params.get("unit", "")).strip()
        lines = int(params.get("lines", 100))
        cmd_parts = ["journalctl", "--no-pager", f"-n {lines}"]
        if unit:
            cmd_parts.append(f"-u {unit}")
        return await _exec(f'{ssh} "{" ".join(cmd_parts)}"', timeout)

    elif action == "systemctl":
        service = str(params.get("service", "")).strip()
        svc_cmd = str(params.get("command", "status")).strip()
        if not service:
            return json.dumps({"ok": False, "error": "params.service requerido"})
        if svc_cmd not in ("start", "stop", "restart", "status", "enable", "disable"):
            return json.dumps({"ok": False, "error": f"command debe ser start/stop/restart/status/enable/disable"})
        prefix = "sudo " if svc_cmd != "status" else ""
        return await _exec(f'{ssh} "{prefix}systemctl {svc_cmd} {service} --no-pager"', timeout)

    elif action == "install_packages":
        packages = params.get("packages", "")
        if isinstance(packages, list):
            packages = " ".join(packages)
        if not packages:
            return json.dumps({"ok": False, "error": "params.packages requerido"})
        return await _exec(
            f'{ssh} "sudo apt-get install -y {packages} 2>&1"',
            max(300, timeout),
        )

    elif action == "list_web_backends":
        return await _exec(
            f'{ssh} "nginx -T 2>/dev/null | grep -E \'(server_name|proxy_pass|upstream)\' || '
            f'cat /etc/nginx/sites-enabled/* 2>/dev/null | grep -E \'(server_name|proxy_pass)\' || '
            f'echo no-web-server-found"',
            timeout,
        )

    elif action == "get_public_ip":
        return await _exec(
            f'{ssh} "curl -s --connect-timeout 5 ifconfig.me || '
            f'curl -s --connect-timeout 5 icanhazip.com || '
            f'curl -s --connect-timeout 5 api.ipify.org || echo unknown"',
            30,
        )

    elif action == "setup_log_retention":
        days = int(params.get("days", 7))
        return await _exec(
            f'{ssh} "sudo bash -c \'cat > /etc/logrotate.d/tokio <<EOF\n'
            f'/var/log/tokio/*.log {{\n'
            f'    daily\n    rotate {days}\n    compress\n    delaycompress\n'
            f'    missingok\n    notifempty\n    create 0640 root root\n}}\nEOF\'"',
            timeout,
        )

    elif action == "network_info":
        return await _exec(
            f'{ssh} "'
            "echo '=== Interfaces ===' && ip -br addr 2>/dev/null || ifconfig && "
            "echo '=== Routes ===' && ip route 2>/dev/null || route -n && "
            "echo '=== DNS ===' && cat /etc/resolv.conf && "
            "echo '=== Ports ===' && ss -tuln 2>/dev/null || netstat -tuln"
            '"',
            timeout,
        )

    elif action == "disk_info":
        return await _exec(
            f'{ssh} "'
            "echo '=== Disk Usage ===' && df -h && "
            "echo '=== Largest dirs ===' && du -h --max-depth=2 / 2>/dev/null | sort -rh | head -20"
            '"',
            timeout,
        )

    else:
        return json.dumps({
            "ok": False,
            "error": f"Acción no soportada: '{action}'",
            "supported": list(_ACTIONS),
        }, ensure_ascii=False)
