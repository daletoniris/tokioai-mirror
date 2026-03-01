"""Tests for the Tool Executor."""
import asyncio
import pytest
from tokio_agent.engine.tools.registry import ToolRegistry
from tokio_agent.engine.tools.executor import ToolExecutor, ToolResult


async def slow_tool():
    await asyncio.sleep(10)
    return "done"


async def failing_tool():
    raise RuntimeError("boom")


async def good_tool(msg: str = "hi") -> str:
    return f"ok: {msg}"


@pytest.fixture
def setup():
    reg = ToolRegistry()
    reg.register("good", "good tool", "Test", {"msg": "message"}, good_tool)
    reg.register("slow", "slow tool", "Test", {}, slow_tool)
    reg.register("fail", "failing tool", "Test", {}, failing_tool)
    executor = ToolExecutor(reg)
    return executor


@pytest.mark.asyncio
async def test_successful_execution(setup):
    result = await setup.execute("good", {"msg": "hello"})
    assert result.success
    assert "ok: hello" in result.output


@pytest.mark.asyncio
async def test_tool_not_found(setup):
    result = await setup.execute("nonexistent", {})
    assert not result.success
    assert "no encontrada" in result.error


@pytest.mark.asyncio
async def test_timeout(setup):
    result = await setup.execute("slow", {}, timeout=1)
    assert not result.success
    assert "Timeout" in result.error


@pytest.mark.asyncio
async def test_error_handling(setup):
    result = await setup.execute("fail", {})
    assert not result.success
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_circuit_breaker(setup):
    # Trigger 5 failures to open circuit breaker
    for _ in range(5):
        await setup.execute("fail", {})

    # Next call should be blocked by circuit breaker
    result = await setup.execute("fail", {})
    assert not result.success
    assert "temporalmente deshabilitada" in result.error
