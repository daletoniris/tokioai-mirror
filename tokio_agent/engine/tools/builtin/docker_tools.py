"""
Docker tools — Container management via the Docker SDK.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _get_client():
    """Get Docker client, raising a clear error if not available."""
    try:
        import docker  # type: ignore
        return docker.from_env()
    except Exception as e:
        raise RuntimeError(
            f"Docker no disponible: {e}. "
            f"Verifica que Docker esté instalado y el socket accesible."
        )


async def docker_cmd(command: str) -> str:
    """Execute a docker command.

    Supported commands:
        ps, logs <container>, start/stop/restart <container>,
        inspect <container>, exec <container> <cmd>,
        stats <container>, images

    Args:
        command: Docker sub-command string.

    Returns:
        Command output.
    """
    import shlex
    parts = shlex.split(command or "")
    if not parts:
        return "Error: comando docker vacío. Usa: ps, logs, start, stop, restart, inspect, exec, stats, images"

    action = parts[0].lower()
    client = _get_client()

    try:
        if action == "ps":
            containers = client.containers.list(all=True)
            lines = ["ID | NOMBRE | ESTADO | IMAGEN", "-" * 60]
            for c in containers:
                img = c.image.tags[0] if c.image.tags else "(none)"
                lines.append(f"{c.short_id} | {c.name} | {c.status} | {img}")
            return "\n".join(lines)

        elif action == "logs":
            if len(parts) < 2:
                return "Error: Nombre del contenedor requerido. Ej: docker logs mi-container"
            container = client.containers.get(parts[1])
            tail = int(parts[2]) if len(parts) > 2 else 50
            logs = container.logs(tail=tail).decode("utf-8", errors="replace")
            return f"Logs de {parts[1]} (últimas {tail} líneas):\n{logs}"

        elif action in ("start", "stop", "restart"):
            if len(parts) < 2:
                return f"Error: Nombre del contenedor requerido para docker {action}"
            container = client.containers.get(parts[1])
            getattr(container, action)(timeout=15 if action != "start" else None)
            container.reload()
            return f"✅ Contenedor {parts[1]}: {action} completado. Estado: {container.status}"

        elif action == "inspect":
            if len(parts) < 2:
                return "Error: Nombre del contenedor requerido"
            container = client.containers.get(parts[1])
            container.reload()
            info = {
                "name": container.name,
                "id": container.short_id,
                "status": container.status,
                "image": container.image.tags[0] if container.image.tags else "(none)",
                "ports": container.attrs.get("NetworkSettings", {}).get("Ports", {}),
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
            return json.dumps(info, ensure_ascii=False, indent=2)

        elif action == "exec":
            if len(parts) < 3:
                return "Error: Uso: docker exec <container> <comando>"
            container = client.containers.get(parts[1])
            cmd_to_run = " ".join(parts[2:])
            result = container.exec_run(cmd_to_run)
            output = result.output.decode("utf-8", errors="replace") if result.output else ""
            return f"Exit code: {result.exit_code}\n{output}"

        elif action == "stats":
            if len(parts) < 2:
                return "Error: Nombre del contenedor requerido"
            container = client.containers.get(parts[1])
            stats = container.stats(stream=False)
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                        stats["precpu_stats"]["cpu_usage"]["total_usage"]
            sys_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                        stats["precpu_stats"]["system_cpu_usage"]
            cpu_pct = (cpu_delta / sys_delta) * 100.0 if sys_delta > 0 else 0.0
            mem_mb = stats["memory_stats"]["usage"] / (1024 * 1024)
            mem_limit = stats["memory_stats"]["limit"] / (1024 * 1024)
            return (
                f"Stats de {parts[1]}:\n"
                f"CPU: {cpu_pct:.2f}%\n"
                f"RAM: {mem_mb:.1f}MB / {mem_limit:.1f}MB"
            )

        elif action == "images":
            images = client.images.list()
            lines = ["REPOSITORIO | TAG | ID | TAMAÑO", "-" * 60]
            for img in images:
                tag = img.tags[0] if img.tags else "<none>"
                size_mb = img.attrs.get("Size", 0) / (1024 * 1024)
                lines.append(f"{tag} | {img.short_id} | {size_mb:.1f}MB")
            return "\n".join(lines)

        else:
            return (
                f"Comando docker no soportado: '{action}'. "
                f"Comandos disponibles: ps, logs, start, stop, restart, inspect, exec, stats, images"
            )

    except Exception as e:
        return f"Error docker: {type(e).__name__}: {e}"
