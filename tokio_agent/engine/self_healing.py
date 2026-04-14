"""
TokioAI Self-Healing Engine v2 — Advanced auto-repair with intelligence.

Improvements over v1:
  - Cascade healing: Entity down → check if HA/BLE/Drone also affected
  - Degradation detection: not just up/down, checks FPS, response time, memory
  - Predictive healing: detects resource exhaustion before crash
  - Telegram notification on auto-heal events
  - GCP container monitoring
  - Escalation: if heal fails 3x, notify owner
  - Health history: tracks service uptime percentage
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
GCP_SSH_KEY = "/root/.ssh/id_ed25519_tokio_host"  # For GCP container checks
CHECK_INTERVAL = int(os.getenv("SELF_HEAL_INTERVAL", "30"))
ENABLED = os.getenv("SELF_HEAL_ENABLED", "true").lower() in ("true", "1", "yes")


@dataclass
class ServiceStatus:
    name: str
    healthy: bool = True
    degraded: bool = False
    last_check: float = 0
    last_healthy: float = 0
    consecutive_failures: int = 0
    total_heals: int = 0
    failed_heals: int = 0
    last_error: str = ""
    last_heal_action: str = ""
    last_heal_time: float = 0
    response_time_ms: float = 0
    # Degradation metrics
    fps: float = 0
    memory_pct: float = 0
    cpu_temp: float = 0
    # Uptime tracking
    uptime_checks: int = 0
    uptime_healthy: int = 0


@dataclass
class HealingLog:
    timestamp: float
    service: str
    action: str
    success: bool
    error: str = ""
    escalated: bool = False


class SelfHealingEngine:
    """Monitors all Tokio services and auto-repairs failures."""

    MAX_CONSECUTIVE_FAILURES = 3
    HEAL_COOLDOWN = 120
    ESCALATION_THRESHOLD = 3  # Failed heals before escalating
    MAX_LOG_SIZE = 500
    DEGRADATION_FPS_MIN = 10.0  # Below this FPS = degraded
    DEGRADATION_RESPONSE_MS = 3000  # Above this response time = degraded

    def __init__(self, on_event: Optional[Callable] = None,
                 on_telegram: Optional[Callable] = None):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._on_event = on_event
        self._on_telegram = on_telegram  # Send Telegram alerts

        self._services: Dict[str, ServiceStatus] = {
            "entity": ServiceStatus(name="Raspi Entity"),
            "ha": ServiceStatus(name="Home Assistant"),
            "health_monitor": ServiceStatus(name="BLE Health Monitor"),
            "drone_proxy": ServiceStatus(name="Drone Proxy"),
            "gcp_agent": ServiceStatus(name="GCP Agent"),
            "gcp_telegram": ServiceStatus(name="GCP Telegram Bot"),
            "gcp_waf": ServiceStatus(name="GCP WAF Proxy"),
        }
        self._log: List[HealingLog] = []
        self._start_time = time.time()

    def start(self):
        if not ENABLED or self._running:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._monitor_loop())
        logger.info("Self-healing engine v2 started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Self-healing loop error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _check_all(self):
        """Check all services in parallel."""
        checks = [
            self._check_entity_with_degradation(),
            self._check_drone_proxy(),
            self._check_gcp_containers(),
        ]
        await asyncio.gather(*checks, return_exceptions=True)

    async def _http_check(self, url: str, timeout: float = 5.0) -> tuple[Optional[dict], float]:
        """HTTP health check returning (result, response_time_ms)."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url)
                r.raise_for_status()
                elapsed = (time.monotonic() - start) * 1000
                return r.json(), elapsed
        except Exception:
            elapsed = (time.monotonic() - start) * 1000
            return None, elapsed

    def _update_status(self, name: str, healthy: bool, error: str = "",
                       degraded: bool = False, response_ms: float = 0):
        """Update service status and trigger healing if needed."""
        svc = self._services.get(name)
        if not svc:
            return

        now = time.time()
        svc.last_check = now
        svc.response_time_ms = response_ms
        svc.uptime_checks += 1

        if healthy:
            if not svc.healthy and svc.consecutive_failures > 0:
                msg = f"✅ {svc.name} recovered after {svc.consecutive_failures} failures"
                logger.info(msg)
                self._notify_telegram(msg)
            svc.healthy = True
            svc.degraded = degraded
            svc.last_healthy = now
            svc.consecutive_failures = 0
            svc.last_error = ""
            svc.uptime_healthy += 1
        else:
            svc.healthy = False
            svc.degraded = False
            svc.consecutive_failures += 1
            svc.last_error = error
            logger.warning(f"Service {name} unhealthy ({svc.consecutive_failures}x): {error}")

    async def _check_entity_with_degradation(self):
        """Check Entity API with degradation detection."""
        result, response_ms = await self._http_check(f"{RASPI_API}/status")

        if result is None:
            self._update_status("entity", False, "Entity API not responding",
                               response_ms=response_ms)
            if self._should_heal("entity"):
                await self._heal_entity()
            # Cascade: if entity is down, HA/BLE/drone are also unreachable
            self._update_status("ha", False, "Entity down (cascade)")
            self._update_status("health_monitor", False, "Entity down (cascade)")
            return

        # Check for degradation
        degraded = False
        vision = result.get("vision", {})
        fps = vision.get("fps", 0)
        svc = self._services["entity"]
        svc.fps = fps

        if fps > 0 and fps < self.DEGRADATION_FPS_MIN:
            degraded = True
            logger.warning(f"Entity degraded: FPS={fps:.1f} (min={self.DEGRADATION_FPS_MIN})")

        if response_ms > self.DEGRADATION_RESPONSE_MS:
            degraded = True
            logger.warning(f"Entity degraded: response={response_ms:.0f}ms")

        self._update_status("entity", True, degraded=degraded,
                           response_ms=response_ms)

        # Check sub-services
        ha_result, _ = await self._http_check(f"{RASPI_API}/ha/status")
        if ha_result is None or not ha_result.get("available", False):
            self._update_status("ha", False, "HA not available")
            if self._should_heal("ha"):
                await self._heal_ha()
        else:
            self._update_status("ha", True)

        health_result, _ = await self._http_check(f"{RASPI_API}/health/status")
        if health_result is None or not health_result.get("available", False):
            self._update_status("health_monitor", False, "BLE health not connected")
            if self._should_heal("health_monitor"):
                await self._heal_ble()
        else:
            self._update_status("health_monitor", True)

        # Check Raspi resource usage for predictive healing
        await self._check_raspi_resources()

    async def _check_raspi_resources(self):
        """Predictive healing: check Raspi resources before they crash."""
        try:
            result, _ = await self._http_check(f"{RASPI_API}/status")
            if not result:
                return

            # Check CPU temp via SSH (quick)
            proc = await asyncio.create_subprocess_exec(
                "ssh", "-i", RASPI_SSH_KEY, "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                f"{RASPI_SSH_USER}@{RASPI_SSH_HOST}",
                "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null && "
                "free -m | awk '/Mem:/{printf \"%d\\n\", $3*100/$2}'",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                lines = stdout.decode().strip().split("\n")
                if len(lines) >= 2:
                    cpu_temp = float(lines[0]) / 1000
                    mem_pct = float(lines[1])
                    svc = self._services["entity"]
                    svc.cpu_temp = cpu_temp
                    svc.memory_pct = mem_pct

                    if cpu_temp > 80:
                        logger.warning(f"PREDICTIVE: Raspi CPU temp critical: {cpu_temp}°C")
                        self._notify_telegram(f"⚠️ Raspi CPU temp: {cpu_temp}°C — throttling likely")

                    if mem_pct > 90:
                        logger.warning(f"PREDICTIVE: Raspi memory critical: {mem_pct}%")
                        self._notify_telegram(f"⚠️ Raspi memory: {mem_pct}% — OOM risk")
        except Exception:
            pass

    async def _check_drone_proxy(self):
        """Check drone safety proxy."""
        result, response_ms = await self._http_check(f"{DRONE_PROXY}/drone/status")
        if result is None:
            self._update_status("drone_proxy", False, "Drone proxy not responding",
                               response_ms=response_ms)
            if self._should_heal("drone_proxy"):
                await self._heal_drone_proxy()
        else:
            self._update_status("drone_proxy", True, response_ms=response_ms)

    async def _check_gcp_containers(self):
        """Check GCP containers health."""
        try:
            # Check agent health endpoint
            result, response_ms = await self._http_check("http://localhost:8000/health", timeout=3.0)
            if result:
                self._update_status("gcp_agent", True, response_ms=response_ms)
            else:
                self._update_status("gcp_agent", False, "Agent health check failed")

            # Check if telegram container is running (via docker)
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "--filter", "name=tokio-telegram",
                "--format", "{{.Status}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            status = stdout.decode().strip()
            if "Up" in status:
                self._update_status("gcp_telegram", True)
            else:
                self._update_status("gcp_telegram", False, f"Container status: {status}")
                if self._should_heal("gcp_telegram"):
                    await self._heal_gcp_container("tokio-telegram")

            # Check WAF proxy
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "--filter", "name=tokio-gcp-waf-proxy",
                "--format", "{{.Status}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            status = stdout.decode().strip()
            if "Up" in status:
                self._update_status("gcp_waf", True)
            else:
                self._update_status("gcp_waf", False, f"WAF status: {status}")
                if self._should_heal("gcp_waf"):
                    await self._heal_gcp_container("tokio-gcp-waf-proxy")
        except Exception as e:
            logger.debug(f"GCP check skipped (not in GCP?): {e}")

    def _should_heal(self, name: str) -> bool:
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
        action = "Restarting Entity via systemd"
        logger.warning(f"Self-healing: {action}")
        self._notify_telegram(f"🔧 Self-healing: {action}")

        success = await self._ssh_command("sudo systemctl restart tokio-entity")
        if success:
            await asyncio.sleep(5)
            check, _ = await self._http_check(f"{RASPI_API}/status")
            if check:
                self._record_heal("entity", action, True)
                self._notify_telegram("✅ Entity recovered successfully")
                return

        action = "Manual restart (systemd failed)"
        logger.warning(f"Self-healing: {action}")
        commands = [
            "pkill -f 'tokio_raspi' || true",
            "sleep 2",
            "sudo fuser -k 5000/tcp || true",
            "rm -f /tmp/tokio-entity.lock",
            "sleep 1",
            "sudo systemctl start tokio-entity",
        ]
        success = await self._ssh_command(" && ".join(commands))
        self._record_heal("entity", action, success)
        if not success:
            self._notify_telegram("❌ Entity heal FAILED — manual intervention needed")

    async def _heal_ha(self):
        action = "Restarting HA container"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command("docker restart homeassistant")
        if success:
            await asyncio.sleep(30)
        self._record_heal("ha", action, success)

    async def _heal_ble(self):
        action = "Resetting BLE adapter"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command(
            "sudo pkill -9 gatttool 2>/dev/null; "
            "sudo hciconfig hci0 down 2>/dev/null; sleep 1; "
            "sudo hciconfig hci0 up 2>/dev/null"
        )
        self._record_heal("health_monitor", action, success)

    async def _heal_drone_proxy(self):
        action = "Restarting drone proxy"
        logger.warning(f"Self-healing: {action}")
        success = await self._ssh_command("sudo systemctl restart tokio-drone-proxy")
        self._record_heal("drone_proxy", action, success)

    async def _heal_gcp_container(self, container_name: str):
        """Restart a GCP Docker container."""
        action = f"Restarting {container_name}"
        logger.warning(f"Self-healing: {action}")
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "restart", container_name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            success = proc.returncode == 0
            self._record_heal(container_name, action, success)
            if success:
                self._notify_telegram(f"✅ {container_name} restarted successfully")
            else:
                self._notify_telegram(f"❌ {container_name} restart FAILED")
        except Exception as e:
            self._record_heal(container_name, action, False, str(e))

    def _notify_telegram(self, message: str):
        """Send notification via Telegram if callback is set."""
        if self._on_telegram:
            try:
                self._on_telegram(message)
            except Exception:
                pass

    def _record_heal(self, service: str, action: str, success: bool, error: str = ""):
        now = time.time()
        svc = self._services.get(service)
        escalated = False

        if svc:
            svc.total_heals += 1
            svc.last_heal_action = action
            svc.last_heal_time = now
            if not success:
                svc.failed_heals += 1
                # Check for escalation
                if svc.failed_heals >= self.ESCALATION_THRESHOLD:
                    escalated = True
                    self._notify_telegram(
                        f"🚨 ESCALATION: {svc.name} has failed {svc.failed_heals} heals. "
                        f"Manual intervention required!"
                    )

        self._log.append(HealingLog(
            timestamp=now, service=service,
            action=action, success=success, error=error, escalated=escalated,
        ))
        if len(self._log) > self.MAX_LOG_SIZE:
            self._log = self._log[-self.MAX_LOG_SIZE:]

        if self._on_event:
            try:
                self._on_event("self_heal", f"{'OK' if success else 'FAILED'}: {action}",
                              "high" if not success else "info")
            except Exception:
                pass

        logger.info(f"Self-heal [{service}] {'OK' if success else 'FAILED'}: {action}")

    def get_status(self) -> Dict:
        """Get all service statuses with enhanced metrics."""
        now = time.time()
        return {
            name: {
                "name": svc.name,
                "healthy": svc.healthy,
                "degraded": svc.degraded,
                "consecutive_failures": svc.consecutive_failures,
                "total_heals": svc.total_heals,
                "failed_heals": svc.failed_heals,
                "last_error": svc.last_error,
                "last_heal_action": svc.last_heal_action,
                "response_time_ms": round(svc.response_time_ms),
                "fps": round(svc.fps, 1) if svc.fps else None,
                "cpu_temp": round(svc.cpu_temp, 1) if svc.cpu_temp else None,
                "memory_pct": round(svc.memory_pct, 1) if svc.memory_pct else None,
                "uptime_pct": round(svc.uptime_healthy / max(svc.uptime_checks, 1) * 100, 1),
                "last_healthy_ago": round(now - svc.last_healthy) if svc.last_healthy else None,
            }
            for name, svc in self._services.items()
        }

    def get_log(self, limit: int = 20) -> List[Dict]:
        return [
            {
                "time": entry.timestamp,
                "service": entry.service,
                "action": entry.action,
                "success": entry.success,
                "error": entry.error,
                "escalated": entry.escalated,
            }
            for entry in self._log[-limit:]
        ]

    def get_summary(self) -> Dict:
        """Quick summary: how many healthy, degraded, down."""
        healthy = sum(1 for s in self._services.values() if s.healthy and not s.degraded)
        degraded = sum(1 for s in self._services.values() if s.healthy and s.degraded)
        down = sum(1 for s in self._services.values() if not s.healthy)
        total_heals = sum(s.total_heals for s in self._services.values())
        return {
            "healthy": healthy,
            "degraded": degraded,
            "down": down,
            "total": len(self._services),
            "total_heals": total_heals,
            "uptime_s": round(time.time() - self._start_time),
        }
