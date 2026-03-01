"""
OpenAI provider — GPT-4o, o1, o3, GPT-5, etc.

Routes models to the correct API endpoint (chat.completions vs responses).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, List, Optional

from .base import BaseLLM, LLMResponse

logger = logging.getLogger(__name__)


class OpenAILLM(BaseLLM):
    """OpenAI models via the official API."""

    provider_name = "openai"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
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

        self._client = OpenAI(api_key=self._api_key)
        logger.info(f"🧠 OpenAI: model={self.model}")

    def _needs_responses_api(self, model_name: str) -> bool:
        """Newer reasoning models use the responses API."""
        n = model_name.lower()
        return n.startswith(("o1", "o3", "o4", "gpt-5"))

    async def _call_chat(
        self, model_name: str, system_prompt: str, user_prompt: str,
        conversation: Optional[List[Dict[str, str]]], max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        messages = [{"role": "system", "content": system_prompt}]
        if conversation:
            messages.extend(conversation)
        messages.append({"role": "user", "content": user_prompt})

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
            provider="openai",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason or "",
            raw=response,
        )

    async def _call_responses(
        self, model_name: str, system_prompt: str, user_prompt: str,
        conversation: Optional[List[Dict[str, str]]], max_tokens: int,
    ) -> LLMResponse:
        input_msgs = [{"role": "system", "content": system_prompt}]
        if conversation:
            input_msgs.extend(conversation)
        input_msgs.append({"role": "user", "content": user_prompt})

        response = await asyncio.to_thread(
            self._client.responses.create,
            model=model_name,
            input=input_msgs,
            max_output_tokens=max_tokens,
        )

        # Extract text robustly
        text = getattr(response, "output_text", None) or ""
        if not text:
            parts = []
            for item in getattr(response, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    t = getattr(c, "text", None)
                    if t:
                        parts.append(t)
            text = "\n".join(parts).strip() or "(sin respuesta)"

        return LLMResponse(
            text=text,
            model=model_name,
            provider="openai",
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
    ) -> LLMResponse:
        self._ensure_client()

        models_to_try = [self.model] + [
            m for m in self._fallback_models if m != self.model
        ]

        last_error: Optional[Exception] = None
        for model_name in models_to_try:
            try:
                if self._needs_responses_api(model_name):
                    return await self._call_responses(
                        model_name, system_prompt, user_prompt,
                        conversation, max_tokens,
                    )
                return await self._call_chat(
                    model_name, system_prompt, user_prompt,
                    conversation, max_tokens, temperature,
                )
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ OpenAI model {model_name} failed: {e}")
                continue

        raise RuntimeError(
            f"All OpenAI models failed. Last error: {last_error}"
        )

    def display_name(self) -> str:
        return f"OpenAI {self.model}"

    def is_available(self) -> bool:
        return bool(self._api_key)
