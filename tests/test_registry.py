"""Tests for the Tool Registry."""
import pytest
from tokio_agent.engine.tools.registry import ToolRegistry


def dummy_tool(x: str = "") -> str:
    return f"result: {x}"


def test_register_and_get():
    reg = ToolRegistry()
    reg.register(
        name="test_tool",
        description="A test tool",
        category="Test",
        parameters={"x": "input"},
        executor=dummy_tool,
    )
    assert reg.has("test_tool")
    assert reg.count() == 1
    tool = reg.get("test_tool")
    assert tool is not None
    assert tool.name == "test_tool"
    assert tool.category == "Test"


def test_unregister():
    reg = ToolRegistry()
    reg.register("t", "d", "c", {}, dummy_tool)
    assert reg.unregister("t")
    assert not reg.has("t")
    assert reg.count() == 0


def test_list_by_category():
    reg = ToolRegistry()
    reg.register("a", "desc a", "Cat1", {}, dummy_tool)
    reg.register("b", "desc b", "Cat2", {}, dummy_tool)
    reg.register("c", "desc c", "Cat1", {}, dummy_tool)

    cats = reg.list_by_category()
    assert len(cats["Cat1"]) == 2
    assert len(cats["Cat2"]) == 1


def test_describe_for_prompt():
    reg = ToolRegistry()
    reg.register("bash", "Run bash", "System", {"command": "cmd"}, dummy_tool)
    desc = reg.describe_for_prompt()
    assert "bash" in desc
    assert "bash" in desc
    assert "System" in desc
