"""
Container Watchdog — Auto-recovery for Docker containers.

Background asyncio task that:
- Checks container health every 30 seconds
- Auto-restarts unhealthy/stopped containers (max 3 attempts)
- Optionally checks GCP containers via SSH
- Sends alerts via Telegram if configured
- Exposes self_heal tool for on-demand checks
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

WATCHDOG_INTERVAL = int(os.getenv("WATCHDOG_INTERVAL", "30"))
WATCHDOG_MAX_RESTARTS = int(os.getenv("WATCHDOG_MAX_RESTARTS", "3"))
WATCHDOG_ENABLED = os.getenv("WATCHDOG_ENABLED", "true").strip().lower() not in ("0", "false", "no")

# Containers to ignore (won't be restarted)
_IGNORE_CONTAINERS = set(
    os.getenv("WATCHDOG_IGNORE_CONTAINERS", "").split(",")
) - {""}


class ContainerEvent:
    """Represents a watchdog event."""
    __slots__ = ("timestamp", "container", "event", "detail", "success")

    def __init__(self, container: str, event: str, detail: str = "", success: bool = True):
        self.timestamp = datetime.now().isoformat()
        self.container = container
        self.event = event
        self.detail = detail
        self.success = success

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "container": self.container,
            "event": self.event,
            "detail": self.detail,
            "success": self.success,
        }


class ContainerWatchdog:
    """Monitors and auto-heals Docker containers."""

    def __init__(
        self,
        on_alert: Optional[Callable[[str], Any]] = None,
    ):
        self._restart_counts: Dict[str, int] = {}
        self._events: List[ContainerEvent] = []
        self._max_events = 200
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._on_alert = on_alert
        self._docker_client = None

    def _get_docker(self):
        """Lazy-init Docker client."""
        if self._docker_client is not None:
            return self._docker_client
        try:
            import docker
            self._docker_client = docker.from_env()
            return self._docker_client
        except Exception as exc:
            logger.debug("Docker not available for watchdog: %s", exc)
            return None

    def _log_event(self, event: ContainerEvent):
        self._events.append(event)
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    async def _send_alert(self, message: str):
        """Send alert via callback (usually Telegram)."""
        if self._on_alert:
            try:
                result = self._on_alert(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.debug("Watchdog alert send failed: %s", exc)

    def check_local_containers(self) -> List[Dict[str, Any]]:
        """Check all local Docker containers. Returns list of issues found."""
        client = self._get_docker()
        if not client:
            return [{"error": "Docker not available"}]

        issues = []
        try:
            containers = client.containers.list(all=True)
            for c in containers:
                name = c.name
                if name in _IGNORE_CONTAINERS:
                    continue

                status = c.status  # running, exited, paused, restarting, dead
                health = "unknown"
                try:
                    health_obj = c.attrs.get("State", {}).get("Health", {})
                    if health_obj:
                        health = health_obj.get("Status", "unknown")
                except Exception:
                    pass

                if status == "running" and health in ("healthy", "unknown"):
                    continue  # All good

                issues.append({
                    "container": name,
                    "status": status,
                    "health": health,
                    "image": c.image.tags[0] if c.image.tags else str(c.image.id)[:12],
                })
        except Exception as exc:
            issues.append({"error": f"Docker check failed: {exc}"})

        return issues

    def restart_container(self, name: str) -> bool:
        """Attempt to restart a container. Returns True on success."""
        client = self._get_docker()
        if not client:
            return False

        count = self._restart_counts.get(name, 0)
        if count >= WATCHDOG_MAX_RESTARTS:
            event = ContainerEvent(name, "restart_limit_reached",
                                   f"Max {WATCHDOG_MAX_RESTARTS} restarts exceeded", False)
            self._log_event(event)
            logger.warning("Watchdog: %s exceeded max restart attempts", name)
            return False

        try:
            container = client.containers.get(name)
            container.restart(timeout=30)
            self._restart_counts[name] = count + 1
            event = ContainerEvent(name, "restarted",
                                   f"Attempt {count + 1}/{WATCHDOG_MAX_RESTARTS}")
            self._log_event(event)
            logger.info("Watchdog: restarted %s (attempt %d)", name, count + 1)
            return True
        except Exception as exc:
            self._restart_counts[name] = count + 1
            event = ContainerEvent(name, "restart_failed", str(exc), False)
            self._log_event(event)
            logger.error("Watchdog: failed to restart %s: %s", name, exc)
            return False

    def check_gcp_containers(self) -> List[Dict[str, Any]]:
        """Check containers on GCP host via SSH (if configured)."""
        from .tools.builtin._common import ssh_run

        gcp_host = os.getenv("GCP_WAF_HOST", "").strip()
        gcp_user = os.getenv("GCP_WAF_USER", "").strip()
        gcp_key = os.getenv("GCP_WAF_SSH_KEY", "").strip()

        if not gcp_host or not gcp_user:
            return []

        issues = []
        try:
            output = ssh_run(
                gcp_host, gcp_user,
                "docker ps --format '{{.Names}}\\t{{.Status}}' 2>/dev/null || echo 'DOCKER_UNAVAILABLE'",
                key=gcp_key, timeout=15,
            )
            if "DOCKER_UNAVAILABLE" in output:
                return [{"error": "Docker not available on GCP host"}]

            for line in output.strip().splitlines():
                parts = line.split("\t", 1)
                if len(parts) < 2:
                    continue
                name, status = parts
                if "Up" not in status:
                    issues.append({
                        "container": name,
                        "status": status,
                        "location": "gcp",
                    })
        except Exception as exc:
            issues.append({"error": f"GCP check failed: {exc}"})

        return issues

    async def run_check(self) -> Dict[str, Any]:
        """Run a single health check cycle. Returns summary."""
        local_issues = await asyncio.to_thread(self.check_local_containers)
        gcp_issues = await asyncio.to_thread(self.check_gcp_containers)

        # Auto-heal local containers
        healed = []
        failed = []
        for issue in local_issues:
            if "error" in issue:
                continue
            name = issue["container"]
            status = issue.get("status", "")
            health = issue.get("health", "")

            if status in ("exited", "dead") or health == "unhealthy":
                ok = await asyncio.to_thread(self.restart_container, name)
                if ok:
                    healed.append(name)
                else:
                    failed.append(name)

        # Reset restart counts for healthy containers
        client = self._get_docker()
        if client:
            try:
                for c in client.containers.list():
                    if c.status == "running" and c.name in self._restart_counts:
                        del self._restart_counts[c.name]
            except Exception:
                pass

        # Send alert if there are issues
        alert_parts = []
        if healed:
            alert_parts.append(f"Auto-restarted: {', '.join(healed)}")
        if failed:
            alert_parts.append(f"Failed to restart: {', '.join(failed)}")
        if gcp_issues:
            gcp_names = [i.get("container", "?") for i in gcp_issues if "error" not in i]
            if gcp_names:
                alert_parts.append(f"GCP issues: {', '.join(gcp_names)}")

        if alert_parts:
            alert_msg = f"🔧 Watchdog Alert\n" + "\n".join(f"• {p}" for p in alert_parts)
            await self._send_alert(alert_msg)

        return {
            "local_issues": local_issues,
            "gcp_issues": gcp_issues,
            "healed": healed,
            "failed": failed,
            "timestamp": datetime.now().isoformat(),
        }

    async def _loop(self):
        """Background loop."""
        logger.info("Watchdog started (interval=%ds)", WATCHDOG_INTERVAL)
        while self._running:
            try:
                await self.run_check()
            except Exception as exc:
                logger.error("Watchdog loop error: %s", exc)
            await asyncio.sleep(WATCHDOG_INTERVAL)

    def start(self):
        """Start the watchdog background task."""
        if not WATCHDOG_ENABLED:
            logger.info("Watchdog disabled via WATCHDOG_ENABLED=false")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        """Stop the watchdog."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Watchdog stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get current watchdog status."""
        return {
            "running": self._running,
            "interval": WATCHDOG_INTERVAL,
            "max_restarts": WATCHDOG_MAX_RESTARTS,
            "restart_counts": dict(self._restart_counts),
            "recent_events": [e.to_dict() for e in self._events[-20:]],
        }


# ── Singleton ─────────────────────────────────────────────────────────────

_instance: Optional[ContainerWatchdog] = None


def get_watchdog(on_alert: Optional[Callable] = None) -> ContainerWatchdog:
    """Get or create the global watchdog instance."""
    global _instance
    if _instance is None:
        _instance = ContainerWatchdog(on_alert=on_alert)
    return _instance


# ── Tool function for agent ──────────────────────────────────────────────

async def self_heal_tool(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """
    Container self-healing tool.

    Actions:
      - status: Get watchdog status and recent events
      - check: Run an immediate health check
      - restart: Restart a specific container (params: container)
      - events: Get recent watchdog events
    """
    params = params or {}
    action = (action or "").strip().lower()
    wd = get_watchdog()

    try:
        if action == "status":
            status = wd.get_status()
            local = await asyncio.to_thread(wd.check_local_containers)
            status["current_issues"] = local
            return json.dumps({"ok": True, **status}, ensure_ascii=False)

        elif action == "check":
            result = await wd.run_check()
            return json.dumps({"ok": True, **result}, ensure_ascii=False)

        elif action == "restart":
            container = str(params.get("container", "")).strip()
            if not container:
                return json.dumps({"ok": False, "error": "params.container es requerido"})
            ok = await asyncio.to_thread(wd.restart_container, container)
            return json.dumps({"ok": ok, "container": container,
                              "result": "Reiniciado" if ok else "Falló"}, ensure_ascii=False)

        elif action == "events":
            limit = int(params.get("limit", 50))
            events = [e.to_dict() for e in wd._events[-limit:]]
            return json.dumps({"ok": True, "events": events}, ensure_ascii=False)

        return json.dumps({"ok": False, "error": f"Acción no soportada: {action}",
                          "supported": ["status", "check", "restart", "events"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "action": action, "error": str(e)}, ensure_ascii=False)
