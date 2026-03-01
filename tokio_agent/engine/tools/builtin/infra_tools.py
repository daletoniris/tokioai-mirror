"""
Infrastructure Tools — System info, processes, services, logs, network, backups.

Comprehensive local system monitoring and management.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from ._common import run_local as _run


def infra_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Infrastructure control tool.

    Actions:
      - system_info: CPU, memory, disk, uptime
      - processes: List top processes
      - services: List systemd services or docker containers
      - logs: Read system/service logs
      - network: Network interfaces, connections, ports
      - disk_usage: Detailed disk usage
      - backup_db: Backup PostgreSQL database
      - restore_db: Restore PostgreSQL database
      - check_ports: Check open/listening ports
      - monitor: Quick health overview
    """
    params = params or {}
    action = (action or "").strip().lower()

    try:
        if action == "system_info":
            info = {
                "hostname": _run("hostname"),
                "uname": _run("uname -a"),
                "uptime": _run("uptime"),
                "cpu": _run("nproc"),
                "memory": _run("free -h"),
                "disk": _run("df -h /"),
                "load": _run("cat /proc/loadavg"),
            }
            return json.dumps({"ok": True, "info": info}, ensure_ascii=False)

        elif action == "processes":
            n = int(params.get("count", 20))
            sort = str(params.get("sort", "cpu")).lower()
            if sort == "mem":
                ps = _run(f"ps aux --sort=-%mem | head -n {n + 1}")
            else:
                ps = _run(f"ps aux --sort=-%cpu | head -n {n + 1}")
            return json.dumps({"ok": True, "processes": ps}, ensure_ascii=False)

        elif action == "services":
            svc_type = str(params.get("type", "auto")).lower()
            if svc_type == "docker" or (svc_type == "auto" and os.path.exists("/var/run/docker.sock")):
                result = _run("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo 'Docker not available'")
            else:
                result = _run("systemctl list-units --type=service --state=running --no-pager 2>/dev/null || "
                             "service --status-all 2>/dev/null || echo 'No service manager found'")
            return json.dumps({"ok": True, "services": result}, ensure_ascii=False)

        elif action == "logs":
            service = str(params.get("service", "")).strip()
            lines = int(params.get("lines", 100))
            follow = bool(params.get("follow", False))
            if service:
                if service.startswith("docker:"):
                    container = service.split(":", 1)[1]
                    result = _run(f"docker logs --tail {lines} {container} 2>&1", timeout=30)
                else:
                    result = _run(f"journalctl -u {service} -n {lines} --no-pager 2>/dev/null || "
                                 f"tail -n {lines} /var/log/{service}.log 2>/dev/null || "
                                 f"echo 'No logs found for {service}'")
            else:
                result = _run(f"journalctl -n {lines} --no-pager 2>/dev/null || "
                             f"tail -n {lines} /var/log/syslog 2>/dev/null || "
                             f"dmesg | tail -n {lines}")
            return json.dumps({"ok": True, "logs": result}, ensure_ascii=False)

        elif action == "network":
            info = {
                "interfaces": _run("ip -br addr 2>/dev/null || ifconfig 2>/dev/null || echo 'no network info'"),
                "routes": _run("ip route 2>/dev/null || route -n 2>/dev/null || echo 'no routes'"),
                "dns": _run("cat /etc/resolv.conf 2>/dev/null || echo 'no resolv.conf'"),
                "connections": _run("ss -tuln 2>/dev/null || netstat -tuln 2>/dev/null || echo 'no connection info'"),
            }
            return json.dumps({"ok": True, "network": info}, ensure_ascii=False)

        elif action == "disk_usage":
            path = str(params.get("path", "/")).strip()
            depth = int(params.get("depth", 1))
            result = _run(f"du -h --max-depth={depth} {path} 2>/dev/null | sort -rh | head -30")
            return json.dumps({"ok": True, "disk_usage": result}, ensure_ascii=False)

        elif action == "backup_db":
            db_host = str(params.get("host", os.getenv("POSTGRES_HOST", "postgres"))).strip()
            db_port = str(params.get("port", os.getenv("POSTGRES_PORT", "5432"))).strip()
            db_name = str(params.get("database", os.getenv("POSTGRES_DB", "tokio"))).strip()
            db_user = str(params.get("user", os.getenv("POSTGRES_USER", "tokio"))).strip()
            db_pass = str(params.get("password", os.getenv("POSTGRES_PASSWORD", ""))).strip()
            output = str(params.get("output", f"/workspace/backup_{db_name}.sql")).strip()
            env = f"PGPASSWORD={db_pass} " if db_pass else ""
            result = _run(f"{env}pg_dump -h {db_host} -p {db_port} -U {db_user} -d {db_name} > {output} 2>&1",
                         timeout=300)
            size = _run(f"ls -lh {output} 2>/dev/null | awk '{{print $5}}'")
            return json.dumps({"ok": True, "file": output, "size": size, "detail": result}, ensure_ascii=False)

        elif action == "restore_db":
            db_host = str(params.get("host", os.getenv("POSTGRES_HOST", "postgres"))).strip()
            db_port = str(params.get("port", os.getenv("POSTGRES_PORT", "5432"))).strip()
            db_name = str(params.get("database", os.getenv("POSTGRES_DB", "tokio"))).strip()
            db_user = str(params.get("user", os.getenv("POSTGRES_USER", "tokio"))).strip()
            db_pass = str(params.get("password", os.getenv("POSTGRES_PASSWORD", ""))).strip()
            input_file = str(params.get("input", "")).strip()
            if not input_file:
                return json.dumps({"ok": False, "error": "params.input (ruta del backup) es requerido"})
            if not os.path.exists(input_file):
                return json.dumps({"ok": False, "error": f"Archivo no encontrado: {input_file}"})
            env = f"PGPASSWORD={db_pass} " if db_pass else ""
            result = _run(f"{env}psql -h {db_host} -p {db_port} -U {db_user} -d {db_name} < {input_file} 2>&1",
                         timeout=300)
            return json.dumps({"ok": True, "detail": result}, ensure_ascii=False)

        elif action == "check_ports":
            ports = params.get("ports", [80, 443, 5432, 8080, 9092])
            if isinstance(ports, str):
                ports = [int(p.strip()) for p in ports.split(",") if p.strip().isdigit()]
            results = {}
            for port in ports:
                check = _run(f"ss -tuln | grep :{port} || echo 'CLOSED'")
                results[str(port)] = "OPEN" if "CLOSED" not in check else "CLOSED"
            return json.dumps({"ok": True, "ports": results}, ensure_ascii=False)

        elif action == "monitor":
            info = {
                "load": _run("cat /proc/loadavg"),
                "memory": _run("free -h | grep Mem"),
                "disk": _run("df -h / | tail -1"),
                "docker": _run("docker ps --format '{{.Names}}: {{.Status}}' 2>/dev/null | head -20 || echo 'no docker'"),
                "top_cpu": _run("ps aux --sort=-%cpu | head -6"),
            }
            return json.dumps({"ok": True, "monitor": info}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["system_info", "processes", "services", "logs",
                                        "network", "disk_usage", "backup_db", "restore_db",
                                        "check_ports", "monitor"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
