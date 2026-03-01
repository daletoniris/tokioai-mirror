"""Tests for the TokioAI Agent — tool parsing and response cleaning."""
import json
import pytest
from tokio_agent.engine.agent import TokioAgent, TOOL_CALL_RE


class TestToolParsing:
    """Test tool call extraction from LLM output."""

    def setup_method(self):
        """Create a minimal agent for testing (no LLM calls)."""
        # We test the parsing methods directly without needing an LLM
        pass

    def test_simple_tool_call(self):
        text = 'Let me check. TOOL:bash({"command": "ls -la"})'
        matches = TOOL_CALL_RE.findall(text)
        assert len(matches) == 1
        assert matches[0][0] == "bash"
        args = json.loads(matches[0][1])
        assert args["command"] == "ls -la"

    def test_multiple_tool_calls(self):
        text = (
            'TOOL:bash({"command": "whoami"}) and then '
            'TOOL:read_file({"path": "/etc/hostname"})'
        )
        matches = TOOL_CALL_RE.findall(text)
        assert len(matches) == 2

    def test_empty_args(self):
        from tokio_agent.engine.agent import TOOL_CALL_SIMPLE_RE
        text = 'TOOL:docker_ps()'
        matches = TOOL_CALL_SIMPLE_RE.findall(text)
        assert len(matches) == 1
        assert matches[0] == "docker_ps"

    def test_nested_json(self):
        text = 'TOOL:curl({"url": "https://api.example.com", "headers": {"Authorization": "Bearer xxx"}})'
        matches = TOOL_CALL_RE.findall(text)
        assert len(matches) == 1

    def test_no_tool_calls(self):
        text = "This is just a regular response with no tools."
        matches = TOOL_CALL_RE.findall(text)
        assert len(matches) == 0
