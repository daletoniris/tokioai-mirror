"""
Google Gemini provider — Gemini 2.0 Flash, Pro, etc.

Supports native tool use via function calling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, List, Optional

from .base import BaseLLM, LLMResponse, ToolUseBlock

logger = logging.getLogger(__name__)


def _anthropic_tools_to_gemini(tools: list) -> list:
    """Convert Anthropic tool format to Gemini FunctionDeclaration format."""
    try:
        from google.generativeai.types import FunctionDeclaration  # type: ignore
    except ImportError:
        return []

    declarations = []
    for t in tools:
        schema = t.get("input_schema", {})
        # Gemini uses a simplified schema format
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Clean up properties for Gemini (it doesn't support all JSON schema features)
        clean_props = {}
        for name, prop in properties.items():
            clean_prop = {"type": prop.get("type", "STRING").upper()}
            if "description" in prop:
                clean_prop["description"] = prop["description"]
            clean_props[name] = clean_prop

        declarations.append(FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters={
                "type": "OBJECT",
                "properties": clean_props,
                "required": required,
            } if clean_props else None,
        ))

    return declarations


class GeminiLLM(BaseLLM):
    """Google Gemini via the generativeai SDK."""

    provider_name = "gemini"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY required for Gemini provider.")

        import google.generativeai as genai  # type: ignore

        genai.configure(api_key=self._api_key)
        self._client = genai
        logger.info(f"Gemini: model={self.model}")

    def _get_model(self, tools=None):
        """Get a GenerativeModel, optionally with tools."""
        kwargs = {}
        if tools:
            try:
                from google.generativeai.types import Tool  # type: ignore
                declarations = _anthropic_tools_to_gemini(tools)
                if declarations:
                    kwargs["tools"] = [Tool(function_declarations=declarations)]
            except Exception as e:
                logger.warning(f"Failed to set up Gemini tools: {e}")

        return self._client.GenerativeModel(self.model, **kwargs)

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation: Optional[List[Dict[str, str]]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> LLMResponse:
        self._ensure_client()

        # Gemini: combine system + conversation + user into a single prompt
        parts = [system_prompt, ""]
        if conversation:
            for msg in conversation:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                parts.append(f"[{role}]: {content}")
            parts.append("")
        parts.append(user_prompt)

        full_prompt = "\n\n".join(parts)

        model = self._get_model()
        response = await asyncio.to_thread(
            model.generate_content, full_prompt
        )

        text = response.text if hasattr(response, "text") else str(response)

        return LLMResponse(
            text=text,
            model=self.model,
            provider="gemini",
            raw=response,
        )

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with native tool use via Gemini function calling."""
        self._ensure_client()

        model = self._get_model(tools)

        # Build Gemini chat history from Anthropic-style messages
        gemini_history, last_user_msg = self._convert_messages(messages, system_prompt)

        chat = model.start_chat(history=gemini_history)
        response = await asyncio.to_thread(chat.send_message, last_user_msg)

        return self._parse_tool_response(response)

    async def stream_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ):
        """Stream with Gemini function calling. Falls back to non-streaming."""
        # Gemini streaming with tools is complex; use non-streaming + yield
        response = await self.generate_with_tools(
            system_prompt, messages, tools, max_tokens,
        )
        if response.text:
            yield ("text", response.text)
        yield ("done", response)

    def _convert_messages(self, messages: list, system_prompt: str):
        """Convert Anthropic messages to Gemini chat format.

        Returns (history, last_user_message).
        """
        from google.generativeai.types import ContentDict  # type: ignore

        history = []
        last_user_msg = system_prompt  # fallback

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            gemini_role = "user" if role == "user" else "model"

            if isinstance(content, str):
                if role == "user":
                    last_user_msg = content
                history.append({"role": gemini_role, "parts": [content]})

            elif isinstance(content, list):
                parts = []
                for block in content:
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(block.get("text", ""))
                        if role == "user":
                            last_user_msg = block.get("text", "")
                    elif btype == "tool_use":
                        # Model's function call
                        try:
                            from google.generativeai.types import (
                                FunctionCall,
                            )
                            parts.append(FunctionCall(
                                name=block["name"],
                                args=block.get("input", {}),
                            ))
                        except ImportError:
                            parts.append(f"[tool_call: {block.get('name', '')}]")
                    elif btype == "tool_result":
                        # Function response
                        try:
                            from google.generativeai.types import (
                                FunctionResponse,
                            )
                            # We need the function name - try to find it
                            tool_id = block.get("tool_use_id", "")
                            func_name = self._find_tool_name(messages, tool_id) or "unknown"
                            resp_content = block.get("content", "")
                            parts.append(FunctionResponse(
                                name=func_name,
                                response={"result": resp_content},
                            ))
                        except ImportError:
                            parts.append(f"[tool_result: {block.get('content', '')[:100]}]")

                if parts:
                    history.append({"role": gemini_role, "parts": parts})

        # Remove last message from history (it becomes the input)
        if history and history[-1]["role"] == "user":
            last_entry = history.pop()
            last_user_msg = last_entry["parts"][0] if last_entry["parts"] else last_user_msg

        return history, last_user_msg

    def _find_tool_name(self, messages: list, tool_id: str) -> Optional[str]:
        """Find the tool name for a given tool_use_id in the message history."""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_use" and block.get("id") == tool_id:
                        return block.get("name")
        return None

    def _parse_tool_response(self, response) -> LLMResponse:
        """Parse Gemini response into LLMResponse with tool_use blocks."""
        text_parts = []
        tool_uses = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    text_parts.append(part.text)
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    tool_uses.append(ToolUseBlock(
                        id=f"gemini_{uuid.uuid4().hex[:8]}",
                        name=fc.name,
                        input=dict(fc.args) if fc.args else {},
                    ))

        # Build raw-like object for _serialize_content compatibility
        content_blocks = []
        for text in text_parts:
            content_blocks.append(_SimpleBlock("text", text=text))
        for tu in tool_uses:
            content_blocks.append(_SimpleBlock("tool_use", id=tu.id, name=tu.name, input=tu.input))

        raw = _SimpleRaw(content_blocks)

        return LLMResponse(
            text="\n".join(text_parts),
            model=self.model,
            provider="gemini",
            tool_use=tool_uses if tool_uses else None,
            raw=raw,
        )

    def display_name(self) -> str:
        return f"Gemini {self.model}"

    def is_available(self) -> bool:
        return bool(self._api_key)


class _SimpleBlock:
    """Mimics Anthropic content block for _serialize_content compatibility."""
    def __init__(self, block_type, **kwargs):
        self.type = block_type
        for k, v in kwargs.items():
            setattr(self, k, v)


class _SimpleRaw:
    """Mimics Anthropic response.raw for _serialize_content compatibility."""
    def __init__(self, content):
        self.content = content
