"""Tests for the Workspace (file-based, no PG required)."""
import os
import tempfile
import pytest
from tokio_agent.engine.memory.workspace import Workspace


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Disable PG for unit tests
        os.environ["POSTGRES_HOST"] = ""
        ws = Workspace(workspace_dir=tmpdir)
        yield ws


def test_soul_default(workspace):
    soul = workspace.get_soul()
    assert "TokioAI" in soul


def test_soul_update(workspace):
    workspace.update_soul("Custom soul")
    assert workspace.get_soul() == "Custom soul"


def test_memory_add(workspace):
    workspace.add_memory("Test entry")
    memory = workspace.get_memory()
    assert "Test entry" in memory


def test_memory_search(workspace):
    workspace.add_memory("User likes Python")
    workspace.add_memory("User dislikes Java")
    results = workspace.search_memory("Python")
    assert len(results) == 1
    assert "Python" in results[0]


def test_preferences(workspace):
    workspace.set_preference("user_name", "Carlos")
    assert workspace.get_preference("user_name") == "Carlos"
    assert workspace.get_preference("nonexistent", "default") == "default"


def test_config(workspace):
    workspace.save_config({"key": "value"})
    config = workspace.get_config()
    assert config["key"] == "value"
