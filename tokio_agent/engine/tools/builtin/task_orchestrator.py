"""
Task Orchestrator — Autonomous operational playbooks.

Manage cron jobs, scheduled scripts, package installation,
and one-shot or recurring tasks.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ._common import run_local as _run, ssh_run as _ssh_run


def task_orchestrator(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Task orchestrator tool.

    Actions:
      - cron_list: List cron jobs (local or remote)
      - cron_add: Add cron job (params: schedule, command, comment)
      - cron_remove: Remove cron job by comment (params: comment)
      - run_once: Execute command once (params: command, target)
      - install_package: Install system package (params: package)
      - create_script: Create executable script (params: path, content)
      - run_playbook: Execute a sequence of commands (params: steps[])
      - schedule_task: Add systemd timer or cron for recurring task
    """
    params = params or {}
    action = (action or "").strip().lower()
    target = str(params.get("target", "local")).strip().lower()

    # Determine if remote
    remote_host = str(params.get("remote_host", os.getenv("REMOTE_HOST", ""))).strip()
    remote_user = str(params.get("remote_user", os.getenv("REMOTE_USER", "tokio"))).strip()
    remote_key = str(params.get("remote_key", os.getenv("REMOTE_SSH_KEY", ""))).strip()
    is_remote = target == "remote" and remote_host

    def exec_cmd(cmd: str, timeout: int = 120) -> str:
        if is_remote:
            return _ssh_run(remote_host, remote_user, cmd, remote_key, timeout)
        return _run(cmd, timeout)

    try:
        if action == "cron_list":
            result = exec_cmd("crontab -l 2>/dev/null || echo 'No crontab'")
            return json.dumps({"ok": True, "crontab": result}, ensure_ascii=False)

        elif action == "cron_add":
            schedule = str(params.get("schedule", "")).strip()
            command = str(params.get("command", "")).strip()
            comment = str(params.get("comment", "tokio_task")).strip()
            if not schedule or not command:
                return json.dumps({"ok": False, "error": "schedule y command son requeridos"})
            line = f"{schedule} {command} # {comment}"
            result = exec_cmd(f'(crontab -l 2>/dev/null; echo "{line}") | crontab -')
            verify = exec_cmd("crontab -l 2>/dev/null | tail -3")
            return json.dumps({"ok": True, "added": line, "verify": verify}, ensure_ascii=False)

        elif action == "cron_remove":
            comment = str(params.get("comment", "")).strip()
            if not comment:
                return json.dumps({"ok": False, "error": "comment es requerido para identificar el cron"})
            result = exec_cmd(f"crontab -l 2>/dev/null | grep -v '# {comment}' | crontab -")
            verify = exec_cmd("crontab -l 2>/dev/null || echo 'empty'")
            return json.dumps({"ok": True, "removed_comment": comment, "remaining": verify}, ensure_ascii=False)

        elif action == "run_once":
            command = str(params.get("command", "")).strip()
            if not command:
                return json.dumps({"ok": False, "error": "command es requerido"})
            timeout = int(params.get("timeout", 120))
            result = exec_cmd(command, timeout=timeout)
            return json.dumps({"ok": True, "output": result}, ensure_ascii=False)

        elif action == "install_package":
            package = str(params.get("package", "")).strip()
            if not package:
                return json.dumps({"ok": False, "error": "package es requerido"})
            # Try apt, then yum, then apk
            result = exec_cmd(
                f"apt-get install -y {package} 2>/dev/null || "
                f"yum install -y {package} 2>/dev/null || "
                f"apk add {package} 2>/dev/null || "
                f"echo 'No package manager found'",
                timeout=180,
            )
            return json.dumps({"ok": True, "output": result}, ensure_ascii=False)

        elif action == "create_script":
            path = str(params.get("path", "")).strip()
            content = str(params.get("content", "")).strip()
            if not path or not content:
                return json.dumps({"ok": False, "error": "path y content son requeridos"})
            if is_remote:
                import shlex
                escaped = shlex.quote(content)
                result = exec_cmd(f"echo {escaped} > {path} && chmod +x {path}")
            else:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                os.chmod(path, 0o755)
                result = f"Script creado en {path}"
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)

        elif action == "run_playbook":
            steps: List[Dict] = params.get("steps", [])
            if not steps:
                return json.dumps({"ok": False, "error": "steps[] es requerido"})
            results = []
            for i, step in enumerate(steps):
                cmd = str(step.get("command", "")).strip()
                desc = str(step.get("description", f"Step {i + 1}")).strip()
                if not cmd:
                    results.append({"step": i + 1, "description": desc, "ok": False, "error": "no command"})
                    continue
                timeout = int(step.get("timeout", 120))
                try:
                    out = exec_cmd(cmd, timeout=timeout)
                    results.append({"step": i + 1, "description": desc, "ok": True, "output": out[:500]})
                except Exception as e:
                    results.append({"step": i + 1, "description": desc, "ok": False, "error": str(e)})
                    if step.get("stop_on_error", False):
                        break
            return json.dumps({"ok": True, "playbook_results": results}, ensure_ascii=False)

        elif action == "schedule_task":
            name = str(params.get("name", "tokio_scheduled")).strip()
            schedule = str(params.get("schedule", "")).strip()
            command = str(params.get("command", "")).strip()
            method = str(params.get("method", "cron")).strip().lower()
            if not schedule or not command:
                return json.dumps({"ok": False, "error": "schedule y command son requeridos"})
            if method == "systemd":
                timer_content = f"""[Unit]
Description={name}

[Timer]
OnCalendar={schedule}
Persistent=true

[Install]
WantedBy=timers.target"""
                service_content = f"""[Unit]
Description={name}

[Service]
Type=oneshot
ExecStart=/bin/bash -c '{command}'"""
                exec_cmd(f"echo '{timer_content}' > /etc/systemd/system/{name}.timer")
                exec_cmd(f"echo '{service_content}' > /etc/systemd/system/{name}.service")
                exec_cmd(f"systemctl daemon-reload && systemctl enable --now {name}.timer")
                result = exec_cmd(f"systemctl status {name}.timer --no-pager")
                return json.dumps({"ok": True, "method": "systemd", "result": result}, ensure_ascii=False)
            else:
                line = f"{schedule} {command} # {name}"
                exec_cmd(f'(crontab -l 2>/dev/null; echo "{line}") | crontab -')
                return json.dumps({"ok": True, "method": "cron", "added": line}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["cron_list", "cron_add", "cron_remove", "run_once",
                                        "install_package", "create_script", "run_playbook",
                                        "schedule_task"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
