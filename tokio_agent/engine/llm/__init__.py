"""LLM providers — Unified interface for multiple AI backends.

Supported providers:
    anthropic  — Claude (Opus, Sonnet, Haiku) via API or Vertex AI
    openai     — GPT-4o, GPT-5, o1 via OpenAI API
    gemini     — Gemini 2.0 Flash/Pro via Google AI
    openrouter — 200+ models via OpenRouter (OpenAI-compatible)
"""

from .base import BaseLLM, LLMResponse, ToolUseBlock
from .factory import create_llm

__all__ = ["BaseLLM", "LLMResponse", "ToolUseBlock", "create_llm"]
