"""Tests for shared utilities (_common.py)."""
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from tokio_agent.engine.tools.builtin._common import (
    run_local,
    run_local_checked,
    ssh_run,
    ssh_run_checked,
)


class TestRunLocal:
    @patch("subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="hello world", stderr="", returncode=0
        )
        result = run_local("echo hello")
        assert result == "hello world"

    @patch("subprocess.run")
    def test_returns_stderr_on_no_stdout(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="some error", returncode=1
        )
        result = run_local("bad_cmd")
        assert result == "some error"

    @patch("subprocess.run")
    def test_returns_exit_code_on_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="", returncode=42
        )
        result = run_local("silent_cmd")
        assert "exit code 42" in result


class TestRunLocalChecked:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0
        )
        result = run_local_checked("echo ok")
        assert result == "ok"

    @patch("subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="not found", returncode=127
        )
        with pytest.raises(RuntimeError, match="not found"):
            run_local_checked("missing_cmd")


class TestSSHRun:
    @patch("subprocess.run")
    def test_basic_ssh(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="remote output", stderr="", returncode=0
        )
        result = ssh_run("1.2.3.4", "user", "whoami")
        assert result == "remote output"
        cmd = mock_run.call_args[0][0]
        assert "ssh" in cmd
        assert "user@1.2.3.4" in cmd

    @patch("subprocess.run")
    def test_ssh_with_key(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0
        )
        ssh_run("host", "user", "cmd", key="/path/to/key")
        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd
        assert "/path/to/key" in cmd

    @patch("subprocess.run")
    def test_ssh_failure_returns_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="Connection refused", returncode=255
        )
        result = ssh_run("host", "user", "cmd")
        assert "Connection refused" in result


class TestSSHRunChecked:
    @patch("subprocess.run")
    def test_raises_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="", stderr="Permission denied", returncode=1
        )
        with pytest.raises(RuntimeError, match="Permission denied"):
            ssh_run_checked("host", "user", "cmd")
