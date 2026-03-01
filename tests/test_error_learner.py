"""Tests for the Error Learner."""
import pytest
from tokio_agent.engine.error_learner import ErrorLearner


def test_analyze_known_error():
    learner = ErrorLearner()
    suggestion = learner.analyze_error("bash", "bash: jq: command not found")
    assert suggestion is not None
    assert "instalarlo" in suggestion.lower() or "install" in suggestion.lower()


def test_max_retries():
    learner = ErrorLearner()
    # Exhaust retries
    for _ in range(3):
        result = learner.analyze_error("bash", "command not found")
        assert result is not None  # Still has suggestions

    # 4th attempt should return None (bail out)
    result = learner.analyze_error("bash", "command not found")
    assert result is None


def test_should_retry():
    learner = ErrorLearner()
    assert learner.should_retry("bash", "command not found")

    for _ in range(3):
        learner.analyze_error("bash", "command not found")

    assert not learner.should_retry("bash", "command not found")


def test_reset_tool():
    learner = ErrorLearner()
    for _ in range(3):
        learner.analyze_error("bash", "command not found")

    learner.reset_tool("bash")
    assert learner.should_retry("bash", "command not found")


def test_context_for_prompt():
    learner = ErrorLearner()
    learner.analyze_error("bash", "Permission denied")
    ctx = learner.get_context_for_prompt()
    assert "bash" in ctx
    assert "Permission" in ctx or "permisos" in ctx.lower()
