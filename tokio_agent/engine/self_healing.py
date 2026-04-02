"""
TokioAI Self-Healing Engine — Monitors and auto-repairs all services.

Tokio is aware of every component in its body. If something stops
responding, Tokio diagnoses, repairs, and logs the incident.

Services monitored:
- Raspi Entity API (:5000)
- Home Assistant (via Entity)
- BLE Health Monitor (via Entity)
- Drone Proxy (:5001)
- GCP containers (tokio-agent, tokio-telegram)
- Tailscale connectivity
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

import httpx

logger = logging.getLogger(__name__)

RASPI_API = os.getenv("RASPI_API_URL", "")
DRONE_PROXY = os.getenv("DRONE_PROXY_URL", "")
RASPI_SSH_HOST = os.getenv("HOST_SSH_HOST", "")
RASPI_SSH_USER = os.getenv("HOST_SSH_USER", "mrmoz")
RASPI_SSH_KEY = os.getenv("HOST_SSH_KEY_PATH", "/root/.ssh/id_ed25519_tokio_host")
CHECK_INTERVAL = int(os.getenv("SELF_HEAL_INTERVAL", "30"))  # seconds
ENABLED = os.getenv("SELF_HEAL_ENABLED", "true").lower() in ("true", "1", "yes")


@dataclass
class ServiceStatus:
    name: str
    healthy: bool = True
    last_check: float = 0
    last_healthy: float = 0
    consecutive_failures: int = 0
    total_heals: int = 0
    last_error: str = ""
    last_heal_action: str = ""
    last_heal_time: float = 0


@dataclass
class HealingLog:
    timestamp: float
    service: str
    action: str
    success: bool
    error: str = ""


class SelfHealingEngine:
    """Monitors all Tokio services and auto-repairs failures."""

    MAX_CONSECUTIVE_FAILURES = 3  # heal after 3 failures
    HEAL_COOLDOWN = 120  # don't re-heal same service within 2 min
    MAX_LOG_SIZE = 200

    def __init__(self, on_event: Optional[Callable] = None):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._on_event = on_event  # callback for notifications

        self._services: Dict[str, ServiceStatus] = {
            "entity": ServiceStatus(name="Raspi Entity"),
            "ha": ServiceStatus(name="Home Assistant"),
            "health_monitor": ServiceStatus(name="BLE Health Monitor"),
            "drone_proxy": ServiceStatus(name="Drone Proxy"),
        }
        self._log: List[HealingLog] = []

    def start(self):
        if not ENABLED or self._running:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._monitor_loop())
        logger.info("Self-healing engine started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self):
        """Main monitoring loop — checks all services periodically."""
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Self-healing loop error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_all(self):
        """Check all services in parallel."""
        checks = [
            self._check_entity(),
            self._check_drone_proxy(),
        ]
        await asyncio.gather(*checks, return_exceptions=True)

    async def _http_check(self, url: str, timeout: float = 5.0) -> Optional[dict]:
        """Quick HTTP health check."""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url)
                r.raise_for_status()
                return r.json()
        except Exception:
            return None

    def _update_status(self, name: str, healthy: bool, error: str = ""):
        """Update service status and trigger healing if needed."""
        svc = self._services.get(name)
        if not svc:
            return

        now = time.time()
        svc.last_check = now

        if healthy:
            if not svc.healthy and svc.consecutive_failures > 0:
                logger.info(f"Service {name} recovered after {svc.consecutive_failures} failures")
            svc.healthy = True
            svc.last_healthy = now
            svc.consecutive_failures = 0
            svc.last_error = ""
        else:
            svc.healthy = False
            svc.consecutive_failures += 1
            svc.last_error = error
            logger.warning(f"Service {name} unhealthy ({svc.consecutive_failures}x): {error}")

    async def _check_entity(self):
        """Check Raspi Entity API + sub-services (HA, BLE)."""
        result = await self._http_check(f"{RASPI_API}/status")

        if result is None:
            self._update_status("entity", False, "Entity API not responding")
            if self._should_heal("entity"):
                await self._heal_entity()
            return

        self._update_status("entity", True)

        # Check sub-services via entity status
        # Home Assistant
        ha_result = await self._http_check(f"{RASPI_API}/ha/status")
        if ha_result is None or not ha_result.get("available", False):
            self._update_status("ha", False, "HA not available")
            if self._should_heal("ha"):
                await self._heal_ha()
        else:
            self._update_status("ha", True)

        # Health Monitor
        health_result = await self._http_check(f"{RASPI_API}/health/status")
        if health_result is None or not health_result.get("available", False):
            self._update_status("health_monitor", False, "BLE health not connected")
            if self._should_heal("health_monitor"):
                await self._heal_ble()
        else:
            self._update_status("health_monitor", True)

    async def _check_drone_proxy(self):
        """Check drone safety proxy."""
        result = await self._http_check(f"{DRONE_PROXY}/drone/status")
        if result is None:
            self._update_status("drone_proxy", False, "Drone proxy not responding")
            if self._should_heal("drone_proxy"):
                await self._heal_drone_proxy()
        else:
            self._update_status("drone_proxy", True)

    def _should_heal(self, name: str) -> bool:
        """Determine if we should attempt auto-healing."""
        svc = self._services.get(name)
        if not svc:
            return False
        if svc.consecutive_failures < self.MAX_CONSECUTIVE_FAILURES:
            return False
        now = time.time()
        if now - svc.last_heal_time < self.HEAL_COOLDOWN:
            return False
        return True

    async def _ssh_command(self, command: str) -> bool:
        """Execute SSH command on Raspi."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-i", RASPI_SSH_KEY,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"{RASPI_SSH_USER}@{RASPI_SSH_HOST}",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return proc.returncode == 0
        except Exception as e:
            logger.error(f"SSH command failed: {e}")
            return False

    async def _heal_entity(self):
        """Attempt to restart the Entity on Raspi."""
        action = "Restarting Entity via SSH"
        logger.warning(f"Self-healing: {action}")

        # Kill entity (safe pattern) and relaunch
        commands = [
            "pkill -f 'tokio_raspi.__main__' || true",
            "sleep 2",
            "sudo fuser -k 5000/tcp || true",
            "cd /home/mrmoz && nohup bash /tmp/relaunch.sh > /tmp/tokio_entity.log 2>&1 &",
        ]
        success = await self._ssh_command(" && ".join(commands))
        self._record_heal("entity", action, success)

    async def _heal_ha(self):
        """Restart Home Assistant container on Raspi."""
        action = "Restarting HA container"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command("sudo docker restart homeassistant")
        self._record_heal("ha", action, success)

    async def _heal_ble(self):
        """Try to restart BLE health monitor (kill gatttool + let entity reconnect)."""
        action = "Killing leftover gatttool processes"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command("sudo pkill gatttool || true")
        self._record_heal("health_monitor", action, success)

    async def _heal_drone_proxy(self):
        """Restart drone proxy systemd service."""
        action = "Restarting drone proxy service"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command("sudo systemctl restart tokio-drone-proxy")
        self._record_heal("drone_proxy", action, success)

    def _record_heal(self, service: str, action: str, success: bool, error: str = ""):
        """Record a healing action."""
        now = time.time()
        svc = self._services.get(service)
        if svc:
            svc.total_heals += 1
            svc.last_heal_action = action
            svc.last_heal_time = now

        self._log.append(HealingLog(
            timestamp=now, service=service,
            action=action, success=success, error=error,
        ))
        if len(self._log) > self.MAX_LOG_SIZE:
            self._log = self._log[-self.MAX_LOG_SIZE:]

        # Notify via callback
        if self._on_event:
            severity = "high" if not success else "info"
            msg = f"{'OK' if success else 'FAILED'}: {action}"
            try:
                self._on_event("self_heal", msg, severity)
            except Exception:
                pass

        status = "OK" if success else "FAILED"
        logger.info(f"Self-heal [{service}] {status}: {action}")

    def get_status(self) -> Dict:
        """Get all service statuses."""
        return {
            name: {
                "name": svc.name,
                "healthy": svc.healthy,
                "consecutive_failures": svc.consecutive_failures,
                "total_heals": svc.total_heals,
                "last_error": svc.last_error,
                "last_heal_action": svc.last_heal_action,
            }
            for name, svc in self._services.items()
        }

    def get_log(self, limit: int = 20) -> List[Dict]:
        """Get recent healing actions."""
        return [
            {
                "time": entry.timestamp,
                "service": entry.service,
                "action": entry.action,
                "success": entry.success,
                "error": entry.error,
            }
            for entry in self._log[-limit:]
        ]
