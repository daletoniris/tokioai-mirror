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

from .base import BaseLLM, LLMResponse

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

        messages: list = []
        if conversation:
            messages.extend(conversation)

        # Build user message content (multimodal if images present)
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

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        # Vertex AI: system prompt goes as 'system' parameter
        # For older Vertex API that doesn't support 'system', combine into user prompt
        if self._use_vertex:
            # Try system parameter first (supported since mid-2025)
            kwargs["system"] = system_prompt
        else:
            kwargs["system"] = system_prompt
            kwargs["temperature"] = temperature

        try:
            response = await asyncio.to_thread(
                self._client.messages.create, **kwargs
            )
        except Exception as e:
            # If system parameter fails on Vertex, retry with combined prompt
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

        text = response.content[0].text if response.content else ""
        usage = getattr(response, "usage", None)

        return LLMResponse(
            text=text,
            model=self.model,
            provider="anthropic-vertex" if self._use_vertex else "anthropic",
            input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
            finish_reason=getattr(response, "stop_reason", ""),
            raw=response,
        )

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
