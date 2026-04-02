"""
Ollama provider — Run LLMs locally for free.

Auto-detects running Ollama instance and available models.
Uses OpenAI-compatible API (inherits from OpenAILLM).

Configuration:
    OLLAMA_HOST=http://localhost:11434  (default)
    OLLAMA_MODEL=llama3.1:8b           (default, auto-detected if not set)

Popular models with tool use support:
    llama3.1:8b      — Good tool use, 8GB RAM
    llama3.1:70b     — Great tool use, 48GB RAM
    qwen2.5:14b      — Strong tool use, 12GB RAM
    qwen2.5:72b      — Excellent, 48GB RAM
    mistral:7b       — Decent tool use, 6GB RAM
    mixtral:8x7b     — Good quality, 32GB RAM
    deepseek-r1:14b  — Reasoning focused, 12GB RAM
    command-r:35b    — Cohere, good at agents, 24GB RAM

Models WITHOUT tool use (avoid for TokioAI):
    phi3, gemma2, tinyllama, codellama, llava
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from .openai_llm import OpenAILLM

logger = logging.getLogger(__name__)

# Models known to support tool use well
TOOL_USE_MODELS = {
    "llama3.1", "llama3.2", "llama3.3", "llama4",
    "qwen2.5", "qwen3",
    "mistral", "mixtral",
    "command-r", "command-r-plus",
    "deepseek-r1",
    "nemotron",
    "hermes",
}


def _detect_ollama(host: str) -> dict:
    """Detect Ollama instance and available models.

    Returns: {"available": bool, "models": [...], "version": str}
    """
    import requests
    try:
        # Check if Ollama is running
        resp = requests.get(f"{host}/api/tags", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"available": True, "models": models}
    except Exception:
        pass
    return {"available": False, "models": []}


def _pick_best_model(models: list) -> Optional[str]:
    """Pick the best model for tool use from available models."""
    # Priority order
    priority = [
        "qwen2.5:72b", "llama3.1:70b", "qwen3",
        "command-r:35b", "mixtral",
        "qwen2.5:14b", "deepseek-r1:14b",
        "llama3.1:8b", "llama3.2", "qwen2.5:7b",
        "mistral",
    ]

    for preferred in priority:
        for available in models:
            if available.startswith(preferred):
                return available

    # Fallback: any model with known tool use
    for available in models:
        base = available.split(":")[0]
        if base in TOOL_USE_MODELS:
            return available

    # Last resort: first available model
    return models[0] if models else None


class OllamaLLM(OpenAILLM):
    """Ollama — Run LLMs locally for free.

    Inherits all tool use capabilities from OpenAILLM.
    Auto-detects running instance and picks best model.
    """

    provider_name = "ollama"

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        fallback_models: Optional[List[str]] = None,
    ):
        self._host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self._detected = False
        self._available_models: list = []

        # Auto-detect model if not specified
        if not model:
            model = os.getenv("OLLAMA_MODEL", "")

        if not model:
            info = _detect_ollama(self._host)
            self._available_models = info["models"]
            if info["available"] and info["models"]:
                model = _pick_best_model(info["models"])
                logger.info(f"Ollama auto-detected model: {model} (from {len(info['models'])} available)")
                self._detected = True
            else:
                model = "llama3.1:8b"  # Default, will fail if not pulled

        if fallback_models is None:
            # Use other available models as fallbacks
            fallback_models = [m for m in self._available_models if m != model][:3]

        super().__init__(
            model=model,
            api_key="ollama",  # Ollama doesn't need a real key
            base_url=f"{self._host}/v1",
            fallback_models=fallback_models,
        )

    def _ensure_client(self):
        if self._client is not None:
            return

        from openai import OpenAI  # type: ignore

        self._client = OpenAI(
            api_key="ollama",
            base_url=f"{self._host}/v1",
        )

        # Check if Ollama is actually running
        if not self._detected:
            info = _detect_ollama(self._host)
            if not info["available"]:
                raise ConnectionError(
                    f"Ollama no esta corriendo en {self._host}. "
                    f"Instala con: curl -fsSL https://ollama.com/install.sh | sh && ollama serve"
                )
            self._available_models = info["models"]
            if self.model not in info["models"]:
                available = ", ".join(info["models"][:10])
                raise ValueError(
                    f"Modelo '{self.model}' no esta descargado. "
                    f"Descargalo con: ollama pull {self.model}\n"
                    f"Modelos disponibles: {available or 'ninguno'}"
                )

        # Warn if model doesn't support tool use well
        base_model = self.model.split(":")[0]
        if base_model not in TOOL_USE_MODELS:
            logger.warning(
                f"Modelo '{self.model}' puede no soportar tool use. "
                f"Recomendados: llama3.1, qwen2.5, mistral"
            )

        logger.info(f"Ollama: model={self.model}, host={self._host}")

    def display_name(self) -> str:
        return f"{self.model} (Ollama local)"

    def is_available(self) -> bool:
        """Check if Ollama is running and has models."""
        try:
            info = _detect_ollama(self._host)
            return info["available"] and len(info["models"]) > 0
        except Exception:
            return False

    def list_models(self) -> list:
        """List all available Ollama models."""
        info = _detect_ollama(self._host)
        return info["models"]
