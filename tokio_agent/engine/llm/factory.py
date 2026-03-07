"""
LLM Factory — Creates the right provider based on configuration.

Supports automatic fallback chain: primary → secondary → tertiary.
Retries transient errors before falling back to inferior models.
"""
from __future__ import annotations

import asyncio
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
    """Wraps a primary LLM with automatic fallback to alternatives.

    Retries the primary provider up to PRIMARY_RETRIES times with exponential
    backoff before falling back to inferior models.
    """

    provider_name = "multi"
    PRIMARY_RETRIES = 3          # retries on primary before fallback
    RETRY_BASE_DELAY = 2.0       # seconds (exponential: 2, 4, 8)

    def __init__(self, primary: BaseLLM, fallbacks: Optional[List[BaseLLM]] = None):
        self.primary = primary
        self.fallbacks = fallbacks or []
        self._fallback_count = 0  # track how often we fall back

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation=None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images=None,
    ) -> LLMResponse:
        gen_kwargs = dict(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            conversation=conversation,
            max_tokens=max_tokens,
            temperature=temperature,
            images=images,
        )

        # ── 1. Try primary with retries ──
        last_primary_error: Optional[Exception] = None
        for attempt in range(1, self.PRIMARY_RETRIES + 1):
            try:
                result = await self.primary.generate(**gen_kwargs)
                if self._fallback_count > 0:
                    logger.info("✅ Primary LLM recovered after previous fallbacks")
                    self._fallback_count = 0
                return result
            except Exception as e:
                last_primary_error = e
                if attempt < self.PRIMARY_RETRIES:
                    delay = self.RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"⚠️ Primary LLM {self.primary.display_name()} attempt "
                        f"{attempt}/{self.PRIMARY_RETRIES} failed: {e} — "
                        f"retrying in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"❌ Primary LLM {self.primary.display_name()} failed "
                        f"all {self.PRIMARY_RETRIES} attempts. Last error: {e}"
                    )

        # ── 2. Try fallbacks (one attempt each) ──
        for llm in self.fallbacks:
            try:
                result = await llm.generate(**gen_kwargs)
                self._fallback_count += 1
                logger.warning(
                    f"⚠️ Used fallback #{self._fallback_count}: "
                    f"{llm.display_name()} (primary {self.primary.display_name()} "
                    f"exhausted {self.PRIMARY_RETRIES} retries)"
                )
                return result
            except Exception as e:
                logger.warning(f"⚠️ Fallback {llm.display_name()} also failed: {e}")
                continue

        raise RuntimeError(
            f"All LLM providers failed after {self.PRIMARY_RETRIES} primary "
            f"retries + {len(self.fallbacks)} fallbacks. "
            f"Last primary error: {last_primary_error}"
        )

    def display_name(self) -> str:
        return self.primary.display_name()

    def is_available(self) -> bool:
        return self.primary.is_available() or any(
            fb.is_available() for fb in self.fallbacks
        )
