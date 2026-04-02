"""
LLM Factory — Creates the right provider based on configuration.

Supports automatic fallback chain: primary -> secondary -> tertiary.
Retries transient errors before falling back to inferior models.

Providers:
    anthropic  — Claude (Opus, Sonnet, Haiku) via API or Vertex AI
    openai     — GPT-4o, GPT-5, o1, o3 via OpenAI API
    gemini     — Gemini 2.0 Flash/Pro via Google AI
    openrouter — 200+ models via OpenRouter (OpenAI-compatible)
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
from .openrouter_llm import OpenRouterLLM
from .ollama_llm import OllamaLLM

logger = logging.getLogger(__name__)

# Map of provider names to classes
PROVIDERS = {
    "anthropic": AnthropicLLM,
    "claude": AnthropicLLM,
    "openai": OpenAILLM,
    "gpt": OpenAILLM,
    "gemini": GeminiLLM,
    "google": GeminiLLM,
    "openrouter": OpenRouterLLM,
    "or": OpenRouterLLM,
    "ollama": OllamaLLM,
    "local": OllamaLLM,
}


def create_llm(provider: Optional[str] = None, **kwargs) -> "LLMWithFallback":
    """Create an LLM instance with automatic fallback.

    Args:
        provider: Primary provider name (anthropic, openai, gemini, openrouter).
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
            f"Available: anthropic, openai, gemini, openrouter"
        )

    primary = cls(**kwargs)

    # Build fallback chain: all other available providers
    fallback_order = {
        "anthropic": ["openai", "openrouter", "gemini", "ollama"],
        "claude": ["openai", "openrouter", "gemini", "ollama"],
        "openai": ["anthropic", "openrouter", "gemini", "ollama"],
        "gpt": ["anthropic", "openrouter", "gemini", "ollama"],
        "gemini": ["anthropic", "openai", "openrouter", "ollama"],
        "google": ["anthropic", "openai", "openrouter", "ollama"],
        "openrouter": ["anthropic", "openai", "gemini", "ollama"],
        "or": ["anthropic", "openai", "gemini", "ollama"],
        "ollama": ["anthropic", "openai", "openrouter", "gemini"],
        "local": ["anthropic", "openai", "openrouter", "gemini"],
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

        # 1. Try primary with retries
        last_primary_error: Optional[Exception] = None
        for attempt in range(1, self.PRIMARY_RETRIES + 1):
            try:
                result = await self.primary.generate(**gen_kwargs)
                if self._fallback_count > 0:
                    logger.info("Primary LLM recovered after previous fallbacks")
                    self._fallback_count = 0
                return result
            except Exception as e:
                last_primary_error = e
                if attempt < self.PRIMARY_RETRIES:
                    delay = self.RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        f"Primary LLM {self.primary.display_name()} attempt "
                        f"{attempt}/{self.PRIMARY_RETRIES} failed: {e} — "
                        f"retrying in {delay:.0f}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Primary LLM {self.primary.display_name()} failed "
                        f"all {self.PRIMARY_RETRIES} attempts. Last error: {e}"
                    )

        # 2. Try fallbacks (one attempt each)
        for llm in self.fallbacks:
            try:
                result = await llm.generate(**gen_kwargs)
                self._fallback_count += 1
                logger.warning(
                    f"Used fallback #{self._fallback_count}: "
                    f"{llm.display_name()} (primary {self.primary.display_name()} "
                    f"exhausted {self.PRIMARY_RETRIES} retries)"
                )
                return result
            except Exception as e:
                logger.warning(f"Fallback {llm.display_name()} also failed: {e}")
                continue

        raise RuntimeError(
            f"All LLM providers failed after {self.PRIMARY_RETRIES} primary "
            f"retries + {len(self.fallbacks)} fallbacks. "
            f"Last primary error: {last_primary_error}"
        )

    async def generate_with_tools(self, system_prompt, messages, tools, max_tokens=4096):
        """Native tool use — try primary, fallback if needed."""
        # 1. Try primary
        last_error: Optional[Exception] = None
        for attempt in range(1, self.PRIMARY_RETRIES + 1):
            try:
                return await self.primary.generate_with_tools(
                    system_prompt, messages, tools, max_tokens,
                )
            except NotImplementedError:
                # Primary doesn't support tool use, try fallbacks immediately
                logger.warning(
                    f"{self.primary.display_name()} doesn't support native tool use"
                )
                break
            except Exception as e:
                last_error = e
                if attempt < self.PRIMARY_RETRIES:
                    delay = self.RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(f"Tool use attempt {attempt} failed: {e}, retrying in {delay:.0f}s")
                    await asyncio.sleep(delay)

        # 2. Try fallbacks
        for llm in self.fallbacks:
            try:
                result = await llm.generate_with_tools(
                    system_prompt, messages, tools, max_tokens,
                )
                logger.warning(f"Tool use fallback to {llm.display_name()}")
                return result
            except (NotImplementedError, Exception) as e:
                logger.warning(f"Tool use fallback {llm.display_name()} failed: {e}")
                continue

        raise RuntimeError(
            f"No provider supports tool use or all failed. Last error: {last_error}"
        )

    async def stream_with_tools(self, system_prompt, messages, tools, max_tokens=4096):
        """Stream with native tool use — delegate to primary, fallback if needed."""
        try:
            async for event in self.primary.stream_with_tools(
                system_prompt, messages, tools, max_tokens,
            ):
                yield event
            return
        except NotImplementedError:
            logger.warning(f"{self.primary.display_name()} doesn't support stream_with_tools")
        except Exception as e:
            logger.warning(f"Primary stream_with_tools failed: {e}")

        # Fallback: try other providers
        for llm in self.fallbacks:
            try:
                async for event in llm.stream_with_tools(
                    system_prompt, messages, tools, max_tokens,
                ):
                    yield event
                return
            except (NotImplementedError, Exception) as e:
                logger.warning(f"Fallback stream {llm.display_name()} failed: {e}")
                continue

        raise RuntimeError("No provider supports streaming tool use")

    async def stream(self, system_prompt, user_prompt, conversation=None,
                     max_tokens=4096, temperature=0.3, images=None):
        """Stream from primary (legacy, for non-tool-use streaming)."""
        try:
            async for token in self.primary.stream(
                system_prompt, user_prompt, conversation,
                max_tokens, temperature, images,
            ):
                yield token
        except Exception as e:
            logger.warning(f"Streaming failed: {e}, falling back to generate()")
            resp = await self.generate(
                system_prompt, user_prompt, conversation,
                max_tokens, temperature, images,
            )
            yield resp.text

    def display_name(self) -> str:
        return self.primary.display_name()

    def is_available(self) -> bool:
        return self.primary.is_available() or any(
            fb.is_available() for fb in self.fallbacks
        )
