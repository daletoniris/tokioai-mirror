"""
LLM Factory — Creates the right provider based on configuration.

Supports automatic fallback chain: primary → secondary → tertiary.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from .base import BaseLLM, LLMResponse
from .anthropic_llm import AnthropicLLM
from .openai_llm import OpenAILLM
from .gemini_llm import GeminiLLM

logger = logging.getLogger(__name__)

# Map of provider names to classes
PROVIDERS = {
    "anthropic": AnthropicLLM,
    "claude": AnthropicLLM,
    "openai": OpenAILLM,
    "gpt": OpenAILLM,
    "gemini": GeminiLLM,
    "google": GeminiLLM,
}


def create_llm(provider: Optional[str] = None, **kwargs) -> "LLMWithFallback":
    """Create an LLM instance with automatic fallback.

    Args:
        provider: Primary provider name (anthropic, openai, gemini).
                  Defaults to LLM_PROVIDER env var or 'anthropic'.
        **kwargs: Extra arguments passed to the primary provider.

    Returns:
        LLMWithFallback wrapping the primary provider with fallback chain.
    """
    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower().strip()

    cls = PROVIDERS.get(provider)
    if not cls:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Available: {', '.join(sorted(set(PROVIDERS.values().__class__.__name__ for _ in [0])))}"
            f" → Use: anthropic, openai, or gemini."
        )

    primary = cls(**kwargs)

    # Build fallback chain: all other available providers
    fallback_order = {
        "anthropic": ["openai", "gemini"],
        "claude": ["openai", "gemini"],
        "openai": ["anthropic", "gemini"],
        "gpt": ["anthropic", "gemini"],
        "gemini": ["anthropic", "openai"],
        "google": ["anthropic", "openai"],
    }

    fallbacks: List[BaseLLM] = []
    for fb_name in fallback_order.get(provider, []):
        try:
            fb_cls = PROVIDERS[fb_name]
            fb_instance = fb_cls()
            if fb_instance.is_available():
                fallbacks.append(fb_instance)
        except Exception:
            pass

    return LLMWithFallback(primary, fallbacks)


class LLMWithFallback(BaseLLM):
    """Wraps a primary LLM with automatic fallback to alternatives."""

    provider_name = "multi"

    def __init__(self, primary: BaseLLM, fallbacks: Optional[List[BaseLLM]] = None):
        self.primary = primary
        self.fallbacks = fallbacks or []

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation=None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images=None,
    ) -> LLMResponse:
        chain = [self.primary] + self.fallbacks
        last_error: Optional[Exception] = None

        for llm in chain:
            try:
                result = await llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    conversation=conversation,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    images=images,
                )
                if llm is not self.primary:
                    logger.warning(
                        f"⚠️ Used fallback: {llm.display_name()} "
                        f"(primary {self.primary.display_name()} failed)"
                    )
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ LLM {llm.display_name()} failed: {e}")
                continue

        raise RuntimeError(
            f"All LLM providers failed. Last error: {last_error}"
        )

    def display_name(self) -> str:
        return self.primary.display_name()

    def is_available(self) -> bool:
        return self.primary.is_available() or any(
            fb.is_available() for fb in self.fallbacks
        )
