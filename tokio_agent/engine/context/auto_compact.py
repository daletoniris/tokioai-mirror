"""
Auto-Compact — Automatic conversation compaction when context is near limit.

Inspired by Claude Code's auto-compaction system. When the conversation
approaches the context window limit (~80%), the agent forks a summarization
call to compress old messages into a summary, then replaces history with
the summary + recent messages.

Key design decisions:
- Threshold at 80% (conservative — leaves room for the next response)
- Keeps the last N recent messages intact (configurable, default 6)
- Circuit breaker after 3 consecutive failures
- Summary replaces conversation history in the session
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

from .token_counter import estimate_tokens, estimate_conversation_tokens, get_context_usage
from .compact_prompts import (
    get_compact_prompt,
    format_compact_summary,
    build_continuation_message,
)

if TYPE_CHECKING:
    from ..llm import BaseLLM

logger = logging.getLogger(__name__)


# ─── Configuration ───

# Context window sizes per model family (input tokens)
MODEL_CONTEXT_WINDOWS = {
    "claude-opus-4": 200_000,
    "claude-sonnet-4": 200_000,
    "claude-haiku-3": 200_000,
    "claude-3-5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5": 16_385,
    "gemini-2.0": 1_000_000,
    "gemini-1.5": 1_000_000,
}
DEFAULT_CONTEXT_WINDOW = 200_000

# Reserve tokens for the response
MAX_OUTPUT_TOKENS = 4_096

# Trigger compaction at this percentage of context used
COMPACT_THRESHOLD_PCT = 80

# Number of recent messages to keep intact during compaction
RECENT_MESSAGES_TO_KEEP = 6

# Circuit breaker: max consecutive failures before giving up
MAX_CONSECUTIVE_FAILURES = 3


class AutoCompactor:
    """Manages automatic conversation compaction."""

    def __init__(
        self,
        llm: "BaseLLM",
        context_window: Optional[int] = None,
    ):
        self.llm = llm
        self._context_window = context_window or self._detect_context_window()
        self._effective_window = self._context_window - MAX_OUTPUT_TOKENS
        self._consecutive_failures = 0
        self._total_compactions = 0

        logger.info(
            f"AutoCompactor initialized: window={self._context_window}, "
            f"effective={self._effective_window}, "
            f"threshold={COMPACT_THRESHOLD_PCT}%"
        )

    def _detect_context_window(self) -> int:
        """Detect context window size from LLM model name."""
        model_name = self.llm.display_name().lower()
        for prefix, window in MODEL_CONTEXT_WINDOWS.items():
            if prefix in model_name:
                return window
        return DEFAULT_CONTEXT_WINDOW

    def should_compact(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        current_prompt: str = "",
    ) -> bool:
        """Check if compaction is needed.

        Returns True if estimated token usage exceeds the threshold.
        """
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.warning("AutoCompact circuit breaker tripped — skipping")
            return False

        if len(messages) < RECENT_MESSAGES_TO_KEEP + 2:
            # Not enough messages to compact
            return False

        tokens = estimate_conversation_tokens(system_prompt, messages, current_prompt)
        threshold = int(self._effective_window * COMPACT_THRESHOLD_PCT / 100)

        if tokens >= threshold:
            usage = get_context_usage(tokens, self._effective_window)
            logger.info(
                f"Context at {usage['usage_percent']}% "
                f"({tokens}/{self._effective_window} tokens) — compaction needed"
            )
            return True

        return False

    async def compact(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
    ) -> Optional[List[Dict[str, str]]]:
        """Compact conversation by summarizing old messages.

        Splits messages into old (to summarize) and recent (to keep).
        Calls the LLM to summarize the old portion, then returns a
        new message list: [summary_message] + recent_messages.

        Args:
            system_prompt: Current system prompt.
            messages: Full conversation history.

        Returns:
            New compacted message list, or None if compaction failed.
        """
        if len(messages) <= RECENT_MESSAGES_TO_KEEP:
            return None

        # Split: old messages to summarize, recent to keep
        split_point = len(messages) - RECENT_MESSAGES_TO_KEEP
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        logger.info(
            f"Compacting: {len(old_messages)} old + "
            f"{len(recent_messages)} recent messages"
        )

        # Build the conversation to summarize
        summary_conversation = []
        for msg in old_messages:
            summary_conversation.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        # Call LLM to generate summary
        compact_prompt = get_compact_prompt()

        try:
            response = await self.llm.generate(
                system_prompt=(
                    "Eres un agente de compactacion. Tu unica tarea es "
                    "resumir la conversacion que se te presenta. "
                    "NO uses herramientas. Responde SOLO con texto: "
                    "un bloque <analysis> seguido de un bloque <summary>."
                ),
                user_prompt=compact_prompt,
                conversation=summary_conversation,
                max_tokens=4096,
                temperature=0.2,
            )

            summary_text = response.text
            if not summary_text or len(summary_text.strip()) < 100:
                logger.warning("Compaction produced too-short summary, aborting")
                self._consecutive_failures += 1
                return None

            # Build the continuation message
            continuation = build_continuation_message(
                summary_text,
                recent_preserved=True,
            )

            # New message list: summary + recent
            compacted = [
                {"role": "user", "content": continuation},
                {"role": "assistant", "content": "Entendido. Continuo con la tarea."},
            ] + recent_messages

            # Success — reset circuit breaker
            self._consecutive_failures = 0
            self._total_compactions += 1

            old_tokens = sum(estimate_tokens(m["content"]) for m in old_messages)
            new_tokens = estimate_tokens(continuation)
            logger.info(
                f"Compaction #{self._total_compactions} complete: "
                f"{old_tokens} tokens -> {new_tokens} tokens "
                f"({len(old_messages)} msgs -> 1 summary), "
                f"keeping {len(recent_messages)} recent"
            )

            return compacted

        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            self._consecutive_failures += 1
            return None

    def get_context_status(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
    ) -> Dict:
        """Get current context window usage status."""
        tokens = estimate_conversation_tokens(system_prompt, messages)
        usage = get_context_usage(tokens, self._effective_window)
        usage["total_compactions"] = self._total_compactions
        usage["consecutive_failures"] = self._consecutive_failures
        return usage
