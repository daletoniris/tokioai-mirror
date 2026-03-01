"""
Google Gemini provider — Gemini 2.0 Flash, Pro, etc.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, List, Optional

from .base import BaseLLM, LLMResponse

logger = logging.getLogger(__name__)


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
        self._client = genai.GenerativeModel(self.model)
        logger.info(f"🧠 Gemini: model={self.model}")

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

        response = await asyncio.to_thread(
            self._client.generate_content, full_prompt
        )

        text = response.text if hasattr(response, "text") else str(response)

        return LLMResponse(
            text=text,
            model=self.model,
            provider="gemini",
            raw=response,
        )

    def display_name(self) -> str:
        return f"Gemini {self.model}"

    def is_available(self) -> bool:
        return bool(self._api_key)
