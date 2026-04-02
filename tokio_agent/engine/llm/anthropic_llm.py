"""
Anthropic Claude provider — Supports direct API and Vertex AI.

Supports: Claude Opus 4.6, Sonnet 4, Sonnet 3.7, Haiku, etc.

Vertex AI Configuration:
    USE_ANTHROPIC_VERTEX=true
    GOOGLE_APPLICATION_CREDENTIALS=/path/to/vertex-credentials.json
    GCP_VERTEX_PROJECT_ID=your-vertex-project  (separate from infra project)
    ANTHROPIC_VERTEX_REGION=global
    CLAUDE_MODEL=claude-opus-4-6
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

from .base import BaseLLM, LLMResponse, ToolUseBlock

logger = logging.getLogger(__name__)


class AnthropicLLM(BaseLLM):
    """Claude via Anthropic API (direct or Vertex AI)."""

    provider_name = "anthropic"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        use_vertex: Optional[bool] = None,
        vertex_project: Optional[str] = None,
        vertex_region: Optional[str] = None,
    ):
        self.model = model or os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

        # Determine if we should use Vertex AI
        if use_vertex is None:
            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            use_vertex = bool(creds_path and os.path.exists(creds_path)) or \
                         os.getenv("USE_ANTHROPIC_VERTEX", "").lower() == "true"
        self._use_vertex = use_vertex

        # Vertex project: GCP_VERTEX_PROJECT_ID takes priority over GCP_PROJECT_ID
        # This allows separating the Vertex AI project from the infrastructure project
        self._vertex_project = vertex_project or os.getenv(
            "GCP_VERTEX_PROJECT_ID",
            os.getenv("ANTHROPIC_VERTEX_PROJECT_ID",
                       os.getenv("GCP_PROJECT_ID", "")),
        )
        self._vertex_region = vertex_region or os.getenv(
            "ANTHROPIC_VERTEX_REGION", "global"
        )

        self._client = None

    def _ensure_client(self):
        """Lazy-init the Anthropic client."""
        if self._client is not None:
            return

        if self._use_vertex:
            from anthropic import AnthropicVertex  # type: ignore

            # Try to read project_id from credentials file if not set
            if not self._vertex_project:
                creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
                if creds_path and os.path.exists(creds_path):
                    with open(creds_path) as f:
                        self._vertex_project = json.load(f).get("project_id", "")

            if not self._vertex_project:
                raise ValueError(
                    "Vertex AI project not configured. "
                    "Set GCP_VERTEX_PROJECT_ID or GCP_PROJECT_ID, "
                    "or ensure GOOGLE_APPLICATION_CREDENTIALS contains project_id."
                )

            self._client = AnthropicVertex(
                region=self._vertex_region,
                project_id=self._vertex_project,
            )
            logger.info(
                f"🧠 Anthropic Vertex AI: project={self._vertex_project}, "
                f"region={self._vertex_region}, model={self.model}"
            )
        else:
            if not self._api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY required for direct Anthropic API."
                )
            from anthropic import Anthropic  # type: ignore

            self._client = Anthropic(api_key=self._api_key)
            logger.info(f"🧠 Anthropic API direct: model={self.model}")

    def _build_messages(
        self,
        user_prompt: str,
        conversation: Optional[List[Dict]] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> list:
        """Build the messages list for the API call."""
        messages: list = []
        if conversation:
            messages.extend(conversation)

        if images:
            content: list = []
            for img in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.get("media_type", "image/jpeg"),
                        "data": img["data"],
                    },
                })
            content.append({"type": "text", "text": user_prompt})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_prompt})

        return messages

    def _parse_response(self, response) -> LLMResponse:
        """Parse an Anthropic API response into LLMResponse with tool_use support."""
        text_parts = []
        tool_uses = []

        for block in (response.content or []):
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                tool_uses.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        usage = getattr(response, "usage", None)

        return LLMResponse(
            text="\n".join(text_parts),
            model=self.model,
            provider="anthropic-vertex" if self._use_vertex else "anthropic",
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            finish_reason=getattr(response, "stop_reason", ""),
            tool_use=tool_uses if tool_uses else None,
            raw=response,
        )

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation: Optional[List[Dict[str, str]]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        images: Optional[List[Dict[str, str]]] = None,
        tools: Optional[list] = None,
    ) -> LLMResponse:
        self._ensure_client()

        messages = self._build_messages(user_prompt, conversation, images)

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system_prompt,
        }

        if tools:
            kwargs["tools"] = tools

        try:
            response = await asyncio.to_thread(
                self._client.messages.create, **kwargs
            )
        except Exception as e:
            if self._use_vertex and "system" in str(e).lower():
                logger.warning("Vertex AI system param failed, combining into prompt")
                del kwargs["system"]
                full_prompt = f"{system_prompt}\n\n{user_prompt}"
                kwargs["messages"] = (conversation or []) + [
                    {"role": "user", "content": full_prompt}
                ]
                response = await asyncio.to_thread(
                    self._client.messages.create, **kwargs
                )
            else:
                raise

        return self._parse_response(response)

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate with native tool use — the core method for agentic loops.

        Args:
            system_prompt: System prompt.
            messages: Full conversation including tool_result blocks.
            tools: Anthropic tool definitions.
            max_tokens: Max tokens.

        Returns:
            LLMResponse with tool_use blocks if the model wants to call tools.
        """
        self._ensure_client()

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system_prompt,
            "tools": tools,
        }

        response = await asyncio.to_thread(
            self._client.messages.create, **kwargs
        )
        return self._parse_response(response)

    async def stream_with_tools(
        self,
        system_prompt: str,
        messages: list,
        tools: list,
        max_tokens: int = 4096,
    ):
        """Stream response with native tool use support.

        Yields events:
            ("text", str)       — text token
            ("tool_use", ToolUseBlock) — complete tool use block
            ("done", LLMResponse)      — final response with all data
        """
        self._ensure_client()

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "system": system_prompt,
            "tools": tools,
        }

        import queue
        import threading

        event_queue: queue.Queue = queue.Queue()

        def _run_stream():
            try:
                with self._client.messages.stream(**kwargs) as stream:
                    for event in stream:
                        event_type = getattr(event, "type", "")

                        if event_type == "content_block_start":
                            block = getattr(event, "content_block", None)
                            if block and getattr(block, "type", "") == "tool_use":
                                event_queue.put(("tool_start", {
                                    "id": block.id,
                                    "name": block.name,
                                }))

                        elif event_type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta:
                                delta_type = getattr(delta, "type", "")
                                if delta_type == "text_delta":
                                    event_queue.put(("text", delta.text))
                                elif delta_type == "input_json_delta":
                                    event_queue.put(("tool_json", delta.partial_json))

                    msg = stream.get_final_message()
                    event_queue.put(("done", msg))
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

            if kind == "text":
                yield ("text", value)
            elif kind == "tool_start":
                yield ("tool_start", value)
            elif kind == "tool_json":
                yield ("tool_json", value)
            elif kind == "done":
                yield ("done", self._parse_response(value))
                break
            elif kind == "error":
                raise value

    def display_name(self) -> str:
        nice = self.model
        for tag, label in [
            ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"),
        ]:
            if tag in self.model.lower():
                nice = f"Claude {label}"
                break
        via = "Vertex AI" if self._use_vertex else "API"
        return f"{nice} ({self.model}) via {via}"

    def is_available(self) -> bool:
        if self._use_vertex:
            creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            return bool(creds and os.path.exists(creds))
        return bool(self._api_key)
