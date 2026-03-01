"""
Tunnel Tools — Cloudflared tunnel lifecycle management.

Deploy, stop, restart, status, and logs for cloudflared tunnels.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from ._common import run_local as _run


def tunnel_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Cloudflared tunnel lifecycle tool.

    Actions:
      - status: Check if cloudflared is running
      - start: Start tunnel (params: tunnel_token or uses CLOUDFLARED_TUNNEL_TOKEN)
      - stop: Stop tunnel
      - restart: Restart tunnel
      - logs: Get recent tunnel logs
      - deploy: Full deploy (install + start)
      - info: Get tunnel info
    """
    params = params or {}
    action = (action or "").strip().lower()
    token = str(params.get("tunnel_token", os.getenv("CLOUDFLARED_TUNNEL_TOKEN", ""))).strip()

    try:
        if action == "status":
            ps = _run("ps aux | grep -v grep | grep cloudflared || echo 'NOT_RUNNING'")
            running = "NOT_RUNNING" not in ps
            return json.dumps({"ok": True, "running": running, "processes": ps}, ensure_ascii=False)

        elif action == "start":
            if not token:
                return json.dumps({"ok": False, "error": "CLOUDFLARED_TUNNEL_TOKEN no configurado"})
            _run("nohup cloudflared tunnel --no-autoupdate run --token "
                 f"{token} > /var/log/cloudflared.log 2>&1 &")
            import time
            time.sleep(3)
            ps = _run("ps aux | grep -v grep | grep cloudflared || echo 'NOT_RUNNING'")
            running = "NOT_RUNNING" not in ps
            return json.dumps({"ok": running, "action": "start",
                              "result": "Tunnel iniciado" if running else "No se pudo iniciar"}, ensure_ascii=False)

        elif action == "stop":
            _run("pkill -f 'cloudflared tunnel' || true")
            return json.dumps({"ok": True, "action": "stop", "result": "Tunnel detenido"}, ensure_ascii=False)

        elif action == "restart":
            _run("pkill -f 'cloudflared tunnel' || true")
            import time
            time.sleep(2)
            if token:
                _run("nohup cloudflared tunnel --no-autoupdate run --token "
                     f"{token} > /var/log/cloudflared.log 2>&1 &")
                time.sleep(3)
            ps = _run("ps aux | grep -v grep | grep cloudflared || echo 'NOT_RUNNING'")
            running = "NOT_RUNNING" not in ps
            return json.dumps({"ok": running, "action": "restart"}, ensure_ascii=False)

        elif action == "logs":
            lines = int(params.get("lines", 100))
            logs = _run(f"tail -n {lines} /var/log/cloudflared.log 2>/dev/null || echo 'No log file'")
            return json.dumps({"ok": True, "logs": logs}, ensure_ascii=False)

        elif action == "deploy":
            steps = []
            # Check if installed
            which = _run("which cloudflared 2>/dev/null || echo 'NOT_FOUND'")
            if "NOT_FOUND" in which:
                install = _run(
                    "curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/"
                    "cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && "
                    "chmod +x /usr/local/bin/cloudflared", timeout=120)
                steps.append(f"install: {install or 'ok'}")
            else:
                steps.append(f"already installed: {which}")
            # Start
            if not token:
                return json.dumps({"ok": False, "error": "CLOUDFLARED_TUNNEL_TOKEN requerido", "steps": steps})
            _run("pkill -f 'cloudflared tunnel' || true")
            import time
            time.sleep(1)
            _run("nohup cloudflared tunnel --no-autoupdate run --token "
                 f"{token} > /var/log/cloudflared.log 2>&1 &")
            time.sleep(3)
            ps = _run("ps aux | grep -v grep | grep cloudflared || echo 'NOT_RUNNING'")
            running = "NOT_RUNNING" not in ps
            steps.append(f"start: {'ok' if running else 'failed'}")
            return json.dumps({"ok": running, "action": "deploy", "steps": steps}, ensure_ascii=False)

        elif action == "info":
            version = _run("cloudflared --version 2>/dev/null || echo 'not installed'")
            ps = _run("ps aux | grep -v grep | grep cloudflared || echo 'NOT_RUNNING'")
            return json.dumps({"ok": True, "version": version, "processes": ps}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["status", "start", "stop", "restart", "logs", "deploy", "info"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
