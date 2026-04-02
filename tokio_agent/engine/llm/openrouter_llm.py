"""
OpenRouter provider — Access 200+ models via OpenAI-compatible API.

Models: Claude, GPT, Llama, Mixtral, DeepSeek, Qwen, etc.
Pricing: pay-per-token, often cheaper than direct API.

Configuration:
    OPENROUTER_API_KEY=sk-or-...
    OPENROUTER_MODEL=anthropic/claude-sonnet-4  (default)
    OPENROUTER_FALLBACK_MODELS=openai/gpt-4o,google/gemini-2.0-flash

Popular models:
    anthropic/claude-opus-4      — Best quality, most expensive
    anthropic/claude-sonnet-4    — Great balance
    openai/gpt-4o               — OpenAI flagship
    google/gemini-2.0-flash     — Fast and cheap
    meta-llama/llama-4-maverick — Open source, good tool use
    deepseek/deepseek-r1        — Reasoning specialist
    qwen/qwen3-235b             — Large open model
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from .openai_llm import OpenAILLM

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterLLM(OpenAILLM):
    """OpenRouter — 200+ models via OpenAI-compatible API.

    Inherits all tool use capabilities from OpenAILLM.
    Just changes the base URL and default model.
    """

    provider_name = "openrouter"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ):
        model = model or os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
        api_key = api_key or os.getenv("OPENROUTER_API_KEY")

        if fallback_models is None:
            raw = os.getenv(
                "OPENROUTER_FALLBACK_MODELS",
                "openai/gpt-4o,google/gemini-2.0-flash",
            )
            fallback_models = [m.strip() for m in raw.split(",") if m.strip()]

        super().__init__(
            model=model,
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            fallback_models=fallback_models,
        )

    def _ensure_client(self):
        if self._client is not None:
            return
        if not self._api_key:
            raise ValueError(
                "OPENROUTER_API_KEY required. Get one at https://openrouter.ai/keys"
            )
        from openai import OpenAI  # type: ignore

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/TokioAI/tokioai",
                "X-Title": "TokioAI Agent",
            },
        )
        logger.info(f"OpenRouter: model={self.model}")

    def display_name(self) -> str:
        # Clean up model name for display
        short = self.model.split("/")[-1] if "/" in self.model else self.model
        return f"{short} (OpenRouter)"

    def is_available(self) -> bool:
        return bool(self._api_key or os.getenv("OPENROUTER_API_KEY"))
