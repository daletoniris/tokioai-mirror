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

    # Extract user_id from session_id (e.g., "telegram-123456" -> "123456")
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
        memory_text = _build_smart_memory(memory)
        if memory_text:
            sections.append(memory_text)

    # 4. Available tools — just a count, details go via native tools param
    sections.append(
        f"# Tools\nYou have {registry.count()} tools available via native tool use. "
        f"Use them freely to accomplish tasks. Key tools: bash, read_file, write_file, "
        f"edit_file, search_code, find_files, docker, ssh (via bash)."
    )

    # 5. Runtime context (container status, capabilities)
    runtime_ctx = _build_runtime_context()
    if runtime_ctx:
        sections.append(runtime_ctx)

    # 5b. Entity & self-healing context
    entity_ctx = _build_entity_context()
    if entity_ctx:
        sections.append(entity_ctx)

    # 6. Core behavioral rules
    sections.append(CORE_RULES)

    # 7. Extra instructions (skills, etc.)
    if extra_instructions:
        for instr in extra_instructions:
            sections.append(instr)

    return "\n\n---\n\n".join(sections)


def _build_smart_memory(memory: str, max_lines: int = 60) -> str:
    """Build categorized memory, prioritizing by type and recency.

    Instead of just taking the last 30 lines, we:
    1. Categorize entries by type (feedback, proyecto, referencia, preferencia)
    2. Allocate budget per category (feedback > proyecto > referencia)
    3. Always include recent entries regardless of category
    """
    import re
    lines = [l.strip() for l in memory.strip().splitlines() if l.strip().startswith("- [")]
    if not lines:
        return ""

    # Categorize
    categories = {
        "feedback": [],
        "preferencia": [],
        "proyecto": [],
        "referencia": [],
        "other": [],
    }
    for line in lines:
        match = re.match(r'- \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*\[(\w+)\]', line)
        cat = match.group(1) if match else "other"
        if cat not in categories:
            cat = "other"
        categories[cat].append(line)

    # Budget allocation (feedback and preferences are most important)
    recent_budget = min(15, max_lines // 4)  # Always include recent
    remaining = max_lines - recent_budget

    budgets = {
        "feedback": int(remaining * 0.30),      # 30% — corrections, user preferences about behavior
        "preferencia": int(remaining * 0.15),    # 15% — user info
        "proyecto": int(remaining * 0.35),       # 35% — project structure
        "referencia": int(remaining * 0.20),     # 20% — facts, IPs, configs
    }

    selected = set()

    # 1. Always include the most recent entries (any category)
    for line in lines[-recent_budget:]:
        selected.add(line)

    # 2. Fill each category from newest to oldest
    for cat, budget in budgets.items():
        cat_entries = categories.get(cat, [])
        for entry in reversed(cat_entries):
            if len(selected) >= max_lines:
                break
            if entry not in selected and budget > 0:
                selected.add(entry)
                budget -= 1

    # 3. Maintain original order
    result = [l for l in lines if l in selected]

    return "# General Memory\n" + "\n".join(result)


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


def _build_entity_context() -> str:
    """Build context about the Entity, self-healing, and security state."""
    parts = []

    try:
        from .entity_sync import security_dashboard, check_entity_alive
        summary = security_dashboard.get_summary()
        if summary.get("blocked_today", 0) > 0:
            parts.append(f"## Security: {summary['blocked_today']} attacks blocked today")
            by_type = summary.get("by_type", {})
            if by_type:
                parts.append(f"- Types: {by_type}")
    except Exception:
        pass

    try:
        from .self_healing import SelfHealingEngine
        # We can't access the agent's instance here, but we note the capability
        parts.append(
            "## Self-Healing: ACTIVE\n"
            "- You monitor Entity, HA, BLE Health, Drone Proxy automatically\n"
            "- If a service is down, you auto-repair it (SSH restart, docker restart)\n"
            "- Use raspi_vision(action='status') to check Entity health"
        )
    except Exception:
        pass

    parts.append(
        "## Intelligent Reasoning\n"
        "- When reporting temperature: met.no gives OUTDOOR temp, not indoor.\n"
        "  If HA shows 11C from met.no, say 'Afuera hay 11C' not 'La casa esta a 11C'.\n"
        "- Cross-reference data from multiple sources before reporting.\n"
        "- If a sensor shows 'unavailable', diagnose why (HA restart, BLE disconnect, etc).\n"
        "- You ARE the Entity. The Raspi face is YOUR face. The camera is YOUR eye.\n"
        "  When someone asks 'que ves?', check your camera. You are one being.\n"
        "## Health Data Access\n"
        "- Health data (HR, BP, SpO2) comes from the Raspi smartwatch via BLE.\n"
        "- ALWAYS use raspi_vision(action='health') for health reports — it returns full multi-day history.\n"
        "- NEVER try to access a local SQLite health database — it does NOT exist.\n"
        "- One call to raspi_vision(action='health') gives you everything: current readings, today stats, 7-day daily stats, assessment.\n"
        "## AI Brain Corrections\n"
        "- When the user asks you to CORRECT something you say/see (e.g. 'no digas Nigeria',\n"
        "  'el cartel dice X', 'no tengo bigote'), use raspi_vision(action='ai_correct',\n"
        "  params={'correction': '...'}) to teach the AI Brain. Do NOT call 'see' or 'look'\n"
        "  repeatedly — that just reads the camera, it doesn't fix the problem.\n"
        "- To teach a fact: raspi_vision(action='ai_teach', params={'key': '...', 'value': '...'})\n"
        "- To remove a wrong observation: raspi_vision(action='ai_forget', params={'key': '...'})\n"
        "- ONE correction call is enough. Never loop on vision tools when the user wants a correction."
    )

    if parts:
        return "# Entity Awareness\n\n" + "\n".join(parts)
    return ""


CORE_RULES = """# Rules
1. When asked to DO something, use tools. Never just describe — act.
2. If a tool fails, try alternatives. Never give up. Never loop on the same error.
3. Be direct and concise. Show results, not explanations.
4. Never expose credentials or passwords.
5. Break complex tasks into steps and execute them autonomously.
6. LARGE FILES: NEVER use write_file for files longer than 50 lines. Instead use bash with cat and heredoc:
   bash({"command": "cat > /path/file.html << 'ENDOFFILE'\n<content here>\nENDOFFILE"})
   For very large files (200+ lines), split into multiple bash calls appending with >>:
   bash({"command": "cat > file.html << 'EOF'\n<part1>\nEOF"})
   bash({"command": "cat >> file.html << 'EOF'\n<part2>\nEOF"})
   This prevents response truncation and tool argument errors.
"""
