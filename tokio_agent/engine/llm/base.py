"""
Base LLM interface — All providers implement this contract.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    raw: Optional[object] = field(default=None, repr=False)


class BaseLLM(abc.ABC):
    """Abstract base for all LLM providers."""

    provider_name: str = "base"

    @abc.abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation: Optional[List[Dict[str, str]]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            system_prompt: System-level instructions.
            user_prompt: The current user message.
            conversation: Optional prior messages [{"role": ..., "content": ...}].
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            images: Optional list of images [{"data": base64_str, "media_type": "image/jpeg"}].

        Returns:
            LLMResponse with the generated text and metadata.
        """

    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable name for this provider+model combination."""

    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        return True
