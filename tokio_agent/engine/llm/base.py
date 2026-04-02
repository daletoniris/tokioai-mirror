"""
Base LLM interface — All providers implement this contract.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Dict, Optional


@dataclass
class ToolUseBlock:
    """A tool use request from the LLM."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    tool_use: Optional[List["ToolUseBlock"]] = field(default=None)
    raw: Optional[object] = field(default=None, repr=False)

    @property
    def has_tool_use(self) -> bool:
        return bool(self.tool_use)


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
        """Generate a response from the LLM."""

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation: Optional[List[Dict[str, str]]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens. Default falls back to generate()."""
        resp = await self.generate(
            system_prompt, user_prompt, conversation,
            max_tokens, temperature, images,
        )
        yield resp.text

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with native tool use. Tools are in Anthropic format.

        Providers that support native tool use should override this.
        Default raises NotImplementedError.
        """
        raise NotImplementedError(
            f"{self.provider_name} does not support native tool use. "
            f"Override generate_with_tools() to add support."
        )

    async def stream_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ):
        """Stream with native tool use. Default falls back to generate_with_tools().

        Yields: ("text", str), ("tool_start", dict), ("tool_json", str), ("done", LLMResponse)
        """
        response = await self.generate_with_tools(
            system_prompt, messages, tools, max_tokens,
        )
        if response.text:
            yield ("text", response.text)
        yield ("done", response)

    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable name for this provider+model combination."""

    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        return True
