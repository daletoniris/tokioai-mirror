"""
Token Counter — Fast token estimation without requiring tokenizer libraries.

Uses a character-based heuristic calibrated for Claude/GPT models:
~4 characters per token for English, ~3 for code/Spanish.
This is intentionally approximate — compaction triggers at ~80% capacity,
so ±10% accuracy is fine.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


# Average chars per token — calibrated conservatively (lower = more tokens estimated = earlier compaction = safer)
CHARS_PER_TOKEN = 3.5

# Overhead per message (role markers, formatting)
MESSAGE_OVERHEAD_TOKENS = 4

# System prompt typically gets extra framing tokens
SYSTEM_PROMPT_OVERHEAD = 50


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string.

    Uses char-based heuristic. Intentionally slightly overestimates
    to trigger compaction early rather than late.
    """
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def estimate_conversation_tokens(
    system_prompt: str,
    messages: List[Dict[str, str]],
    current_prompt: Optional[str] = None,
) -> int:
    """Estimate total tokens for a full LLM call.

    Args:
        system_prompt: The system prompt text.
        messages: Conversation history [{"role": ..., "content": ...}].
        current_prompt: The current user message about to be sent.

    Returns:
        Estimated total input tokens.
    """
    total = SYSTEM_PROMPT_OVERHEAD + estimate_tokens(system_prompt)

    for msg in messages:
        total += MESSAGE_OVERHEAD_TOKENS + estimate_tokens(msg.get("content", ""))

    if current_prompt:
        total += MESSAGE_OVERHEAD_TOKENS + estimate_tokens(current_prompt)

    return total


def get_context_usage(
    current_tokens: int,
    max_tokens: int,
) -> Dict[str, any]:
    """Get context window usage statistics.

    Returns:
        Dict with usage_percent, tokens_used, tokens_remaining, should_warn, should_compact.
    """
    usage_pct = (current_tokens / max_tokens * 100) if max_tokens > 0 else 0
    remaining = max(0, max_tokens - current_tokens)

    return {
        "usage_percent": round(usage_pct, 1),
        "tokens_used": current_tokens,
        "tokens_remaining": remaining,
        "max_tokens": max_tokens,
        "should_warn": usage_pct >= 70,
        "should_compact": usage_pct >= 80,
    }
