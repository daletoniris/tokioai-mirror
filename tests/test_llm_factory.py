"""Tests for the LLM Factory."""
import os
import pytest
from tokio_agent.engine.llm.factory import create_llm, LLMWithFallback
from tokio_agent.engine.llm.anthropic_llm import AnthropicLLM
from tokio_agent.engine.llm.openai_llm import OpenAILLM
from tokio_agent.engine.llm.gemini_llm import GeminiLLM


def test_create_anthropic():
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    llm = create_llm("anthropic")
    assert isinstance(llm, LLMWithFallback)
    assert isinstance(llm.primary, AnthropicLLM)
    del os.environ["ANTHROPIC_API_KEY"]


def test_create_openai():
    os.environ["OPENAI_API_KEY"] = "test-key"
    llm = create_llm("openai")
    assert isinstance(llm, LLMWithFallback)
    assert isinstance(llm.primary, OpenAILLM)
    del os.environ["OPENAI_API_KEY"]


def test_create_gemini():
    os.environ["GEMINI_API_KEY"] = "test-key"
    llm = create_llm("gemini")
    assert isinstance(llm, LLMWithFallback)
    assert isinstance(llm.primary, GeminiLLM)
    del os.environ["GEMINI_API_KEY"]


def test_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_llm("nonexistent")
