"""
OpenAI provider — GPT-4o, o1, o3, GPT-5, etc.

Supports native tool use via chat.completions with tools parameter.
Also works with OpenRouter (set OPENAI_BASE_URL).
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


def _anthropic_tools_to_openai(tools: list) -> list:
    """Convert Anthropic tool format to OpenAI format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result


def _anthropic_messages_to_openai(messages: list, system_prompt: str) -> list:
    """Convert Anthropic-style messages (with tool_result blocks) to OpenAI format."""
    result = [{"role": "system", "content": system_prompt}]

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": role, "content": content})

        elif isinstance(content, list):
            if role == "assistant":
                # May contain text + tool_use blocks
                text_parts = []
                tool_calls = []
                for block in content:
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })

                assistant_msg = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                else:
                    assistant_msg["content"] = None
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                result.append(assistant_msg)

            elif role == "user":
                # May contain tool_result blocks or text+image
                for block in content:
                    btype = block.get("type", "")
                    if btype == "tool_result":
                        result.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": block.get("content", ""),
                        })
                    elif btype == "text":
                        result.append({"role": "user", "content": block.get("text", "")})
                    elif btype == "image":
                        # OpenAI vision format
                        source = block.get("source", {})
                        result.append({
                            "role": "user",
                            "content": [{
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}",
                                },
                            }],
                        })
        else:
            result.append({"role": role, "content": str(content)})

    return result


class OpenAILLM(BaseLLM):
    """OpenAI models via the official API. Also supports OpenRouter."""

    provider_name = "openai"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self._fallback_models = fallback_models
        if self._fallback_models is None:
            raw = os.getenv("OPENAI_FALLBACK_MODELS", "gpt-4o,gpt-4-turbo")
            self._fallback_models = [m.strip() for m in raw.split(",") if m.strip()]
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY required for OpenAI provider.")
        from openai import OpenAI  # type: ignore

        kwargs = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = OpenAI(**kwargs)
        logger.info(f"OpenAI: model={self.model}, base_url={self._base_url or 'default'}")

    def _needs_responses_api(self, model_name: str) -> bool:
        """Newer reasoning models use the responses API."""
        n = model_name.lower()
        return n.startswith(("o1", "o3", "o4", "gpt-5"))

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

        messages = [{"role": "system", "content": system_prompt}]
        if conversation:
            messages.extend(conversation)
        messages.append({"role": "user", "content": user_prompt})

        models_to_try = [self.model] + [
            m for m in self._fallback_models if m != self.model
        ]

        last_error: Optional[Exception] = None
        for model_name in models_to_try:
            try:
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=model_name,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                choice = response.choices[0]
                usage = response.usage

                return LLMResponse(
                    text=choice.message.content or "",
                    model=model_name,
                    provider=self.provider_name,
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                    finish_reason=choice.finish_reason or "",
                    raw=response,
                )
            except Exception as e:
                last_error = e
                logger.warning(f"OpenAI model {model_name} failed: {e}")
                continue

        raise RuntimeError(f"All OpenAI models failed. Last error: {last_error}")

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with native tool use. Accepts Anthropic-format tools and messages."""
        self._ensure_client()

        openai_tools = _anthropic_tools_to_openai(tools)
        openai_messages = _anthropic_messages_to_openai(messages, system_prompt)

        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self.model,
            messages=openai_messages,
            tools=openai_tools,
            max_tokens=max_tokens,
        )

        return self._parse_tool_response(response)

    async def stream_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ):
        """Stream with native tool use. Yields Anthropic-compatible events."""
        self._ensure_client()

        openai_tools = _anthropic_tools_to_openai(tools)
        openai_messages = _anthropic_messages_to_openai(messages, system_prompt)

        import queue
        import threading

        event_queue: queue.Queue = queue.Queue()

        def _run_stream():
            try:
                stream = self._client.chat.completions.create(
                    model=self.model,
                    messages=openai_messages,
                    tools=openai_tools,
                    max_tokens=max_tokens,
                    stream=True,
                )

                current_tool_calls = {}  # index -> {id, name, args_parts}
                text_parts = []

                for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Text content
                    if delta.content:
                        text_parts.append(delta.content)
                        event_queue.put(("text", delta.content))

                    # Tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in current_tool_calls:
                                current_tool_calls[idx] = {
                                    "id": tc.id or f"call_{uuid.uuid4().hex[:8]}",
                                    "name": "",
                                    "args_parts": [],
                                }
                            if tc.id:
                                current_tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    current_tool_calls[idx]["name"] = tc.function.name
                                    event_queue.put(("tool_start", {
                                        "id": current_tool_calls[idx]["id"],
                                        "name": tc.function.name,
                                    }))
                                if tc.function.arguments:
                                    current_tool_calls[idx]["args_parts"].append(
                                        tc.function.arguments
                                    )
                                    event_queue.put(("tool_json", tc.function.arguments))

                # Build final response
                tool_uses = []
                for idx, tc_data in sorted(current_tool_calls.items()):
                    args_str = "".join(tc_data["args_parts"])
                    try:
                        args = json.loads(args_str) if args_str else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_uses.append(ToolUseBlock(
                        id=tc_data["id"],
                        name=tc_data["name"],
                        input=args,
                    ))

                # Create a mock response for _parse compatibility
                final = LLMResponse(
                    text="".join(text_parts),
                    model=self.model,
                    provider=self.provider_name,
                    tool_use=tool_uses if tool_uses else None,
                    raw=None,
                )
                event_queue.put(("done", final))

            except Exception as e:
                event_queue.put(("error", e))

        thread = threading.Thread(target=_run_stream, daemon=True)
        thread.start()

        while True:
            try:
                kind, value = await asyncio.to_thread(event_queue.get, timeout=0.1)
            except Exception:
                if not thread.is_alive():
                    break
                continue

            if kind == "error":
                raise value
            yield (kind, value)
            if kind == "done":
                break

    def _parse_tool_response(self, response) -> LLMResponse:
        """Parse OpenAI response into LLMResponse with tool_use blocks."""
        choice = response.choices[0]
        message = choice.message
        usage = response.usage

        tool_uses = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_uses.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))

        # Build a raw-like object for _serialize_content compatibility
        content_blocks = []
        if message.content:
            content_blocks.append(_SimpleBlock("text", text=message.content))
        for tu in tool_uses:
            content_blocks.append(_SimpleBlock("tool_use", id=tu.id, name=tu.name, input=tu.input))

        raw = _SimpleRaw(content_blocks)

        return LLMResponse(
            text=message.content or "",
            model=response.model or self.model,
            provider=self.provider_name,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason or "",
            tool_use=tool_uses if tool_uses else None,
            raw=raw,
        )

    def display_name(self) -> str:
        return f"OpenAI {self.model}"

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
