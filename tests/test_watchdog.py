"""Tests for the Container Watchdog."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from tokio_agent.engine.watchdog import (
    ContainerWatchdog,
    ContainerEvent,
    self_heal_tool,
)


class TestContainerEvent:
    def test_event_creation(self):
        event = ContainerEvent("tokio-cli", "restarted", "Attempt 1/3", True)
        assert event.container == "tokio-cli"
        assert event.event == "restarted"
        assert event.success is True

    def test_event_to_dict(self):
        event = ContainerEvent("tokio-cli", "restarted")
        d = event.to_dict()
        assert "timestamp" in d
        assert d["container"] == "tokio-cli"
        assert d["event"] == "restarted"


class TestContainerWatchdog:
    def test_init(self):
        wd = ContainerWatchdog()
        assert wd._running is False
        assert wd._restart_counts == {}
        assert wd._events == []

    def test_get_status(self):
        wd = ContainerWatchdog()
        status = wd.get_status()
        assert status["running"] is False
        assert "interval" in status
        assert "max_restarts" in status

    def test_log_event(self):
        wd = ContainerWatchdog()
        event = ContainerEvent("test", "check")
        wd._log_event(event)
        assert len(wd._events) == 1
        assert wd._events[0].container == "test"

    def test_log_event_max_limit(self):
        wd = ContainerWatchdog()
        wd._max_events = 5
        for i in range(10):
            wd._log_event(ContainerEvent(f"c{i}", "check"))
        assert len(wd._events) == 5

    @patch("tokio_agent.engine.watchdog.ContainerWatchdog._get_docker")
    def test_check_local_no_docker(self, mock_docker):
        mock_docker.return_value = None
        wd = ContainerWatchdog()
        issues = wd.check_local_containers()
        assert len(issues) == 1
        assert "error" in issues[0]

    @patch("tokio_agent.engine.watchdog.ContainerWatchdog._get_docker")
    def test_check_local_all_healthy(self, mock_docker):
        mock_container = MagicMock()
        mock_container.name = "tokio-cli"
        mock_container.status = "running"
        mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}
        mock_container.image.tags = ["tokioai:latest"]

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_docker.return_value = mock_client

        wd = ContainerWatchdog()
        issues = wd.check_local_containers()
        assert issues == []

    @patch("tokio_agent.engine.watchdog.ContainerWatchdog._get_docker")
    def test_check_local_exited_container(self, mock_docker):
        mock_container = MagicMock()
        mock_container.name = "tokio-cli"
        mock_container.status = "exited"
        mock_container.attrs = {"State": {}}
        mock_container.image.tags = ["tokioai:latest"]

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [mock_container]
        mock_docker.return_value = mock_client

        wd = ContainerWatchdog()
        issues = wd.check_local_containers()
        assert len(issues) == 1
        assert issues[0]["container"] == "tokio-cli"
        assert issues[0]["status"] == "exited"

    @patch("tokio_agent.engine.watchdog.ContainerWatchdog._get_docker")
    def test_restart_container_success(self, mock_docker):
        mock_container = MagicMock()
        mock_container.restart.return_value = None

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.return_value = mock_client

        wd = ContainerWatchdog()
        ok = wd.restart_container("tokio-cli")
        assert ok is True
        assert wd._restart_counts["tokio-cli"] == 1

    @patch("tokio_agent.engine.watchdog.ContainerWatchdog._get_docker")
    def test_restart_max_attempts(self, mock_docker):
        mock_client = MagicMock()
        mock_docker.return_value = mock_client

        wd = ContainerWatchdog()
        wd._restart_counts["tokio-cli"] = 3  # Already at max
        ok = wd.restart_container("tokio-cli")
        assert ok is False


class TestSelfHealTool:
    @pytest.mark.asyncio
    async def test_status_action(self):
        with patch("tokio_agent.engine.watchdog.get_watchdog") as mock_wd:
            mock_instance = MagicMock()
            mock_instance.get_status.return_value = {
                "running": False, "interval": 30,
                "max_restarts": 3, "restart_counts": {},
                "recent_events": [],
            }
            mock_instance.check_local_containers.return_value = []
            mock_wd.return_value = mock_instance

            result = await self_heal_tool("status")
            data = json.loads(result)
            assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_invalid_action(self):
        result = await self_heal_tool("invalid_action")
        data = json.loads(result)
        assert data["ok"] is False
        assert "supported" in data
