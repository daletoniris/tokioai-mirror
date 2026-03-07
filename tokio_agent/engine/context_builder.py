"""
Context Builder — Constructs the system prompt for the LLM.

Assembles: identity + memory + tools + preferences + rules.
Clean, modular, no duplication.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .memory.workspace import Workspace
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def build_system_prompt(
    workspace: Workspace,
    registry: ToolRegistry,
    extra_instructions: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> str:
    """Build the complete system prompt.

    Args:
        workspace: Agent workspace with identity and memory.
        registry: Tool registry with available tools.
        extra_instructions: Optional additional instructions (skills, etc.).
        session_id: Session ID for per-user preference/memory isolation.

    Returns:
        Complete system prompt string.
    """
    sections = []

    # Extract user_id from session_id (e.g., "telegram-OWNER_ID" -> "OWNER_ID")
    user_id = ""
    if session_id and session_id.startswith("telegram-"):
        user_id = session_id.replace("telegram-", "")

    # 1. Identity
    soul = workspace.get_soul()
    sections.append(soul)

    # 2. User preferences (per-user if available)
    prefs = workspace.get_all_preferences(user_id=user_id)
    if prefs:
        pref_lines = ["# User Preferences"]
        user_name = prefs.get("user_name")
        if user_name:
            pref_lines.append(f"- The user's name is **{user_name}**. Always address them by name.")
        user_lang = prefs.get("language")
        if user_lang:
            pref_lines.append(f"- Respond in **{user_lang}**.")
        for k, v in prefs.items():
            if k not in ("user_name", "language"):
                pref_lines.append(f"- {k}: {v}")
        sections.append("\n".join(pref_lines))

    # 3. Memory context (per-user if available, then global)
    if user_id:
        user_memory = workspace.get_user_memory(user_id)
        if user_memory and user_memory.strip():
            sections.append("# User Memory\n" + user_memory)
    memory = workspace.get_memory()
    if memory and memory.strip():
        lines = memory.strip().splitlines()
        recent = lines[-30:] if len(lines) > 30 else lines
        sections.append(
            "# General Memory\n" + "\n".join(recent)
        )

    # 4. Available tools
    tool_desc = registry.describe_for_prompt()
    sections.append(f"# Available Tools\n\n{tool_desc}")

    # 5. Runtime context (container status, capabilities)
    runtime_ctx = _build_runtime_context()
    if runtime_ctx:
        sections.append(runtime_ctx)

    # 6. Core behavioral rules
    sections.append(CORE_RULES)

    # 7. Extra instructions (skills, etc.)
    if extra_instructions:
        for instr in extra_instructions:
            sections.append(instr)

    return "\n\n---\n\n".join(sections)


def _build_runtime_context() -> str:
    """Build runtime context: container status, watchdog, capabilities."""
    import os
    parts = ["# Runtime Context"]

    # Watchdog status
    try:
        from .watchdog import get_watchdog
        wd = get_watchdog()
        status = wd.get_status()
        if status.get("running"):
            events = status.get("recent_events", [])
            restart_counts = status.get("restart_counts", {})
            parts.append("## Watchdog: ACTIVE")
            if restart_counts:
                parts.append(f"- Containers with restart attempts: {restart_counts}")
            if events:
                recent = events[-3:]
                for e in recent:
                    parts.append(f"- [{e.get('timestamp', '?')}] {e.get('container', '?')}: {e.get('event', '?')}")
        else:
            parts.append("## Watchdog: INACTIVE")
    except Exception:
        pass

    # Multimedia capabilities
    caps = []
    if os.getenv("OPENAI_API_KEY"):
        caps.append("Vision (OpenAI)")
    if os.getenv("GEMINI_API_KEY"):
        caps.append("Vision/Audio (Gemini)")
    caps.append("Document generation (PDF, Slides, CSV)")
    caps.append("File upload/download via Telegram")
    if caps:
        parts.append(f"## Multimedia: {', '.join(caps)}")

    return "\n".join(parts) if len(parts) > 1 else ""


CORE_RULES = """# Critical Rules

## Tool Usage
1. When the user asks you to DO something (execute, check, create, modify, delete, etc.), you MUST use a tool. NEVER just describe what you would do.
2. Call tools using the exact format: `TOOL:tool_name({"param": "value"})`
3. If a tool fails, try an alternative approach. Never give up.
4. If you need multiple steps, execute them sequentially. Show results after each step.

## Error Handling
1. If a tool returns an error, analyze it and try a different approach.
2. If a command is not found, try installing it first.
3. If you get a timeout, try a simpler version of the command.
4. After 3 consecutive failures on the same tool, use a different tool.
5. NEVER enter an infinite loop. If you're stuck, explain what happened and ask for guidance.

## Communication
1. Be direct and concise. Don't over-explain.
2. Show the actual result of actions, not just "I did it".
3. If you learn something about the user (name, preferences), remember it.
4. When you remember something, use TOOL:write_file to persist it.

## Security
1. Never expose credentials, API keys, or passwords in responses.
2. Warn about potentially dangerous operations before executing.
3. Always validate inputs before passing them to tools.

## Autonomy
1. You can work independently for extended periods.
2. If given a complex task, break it into steps and execute them.
3. Track your progress and report when done.
4. If you need more information, ask — but try to figure it out first.
"""
