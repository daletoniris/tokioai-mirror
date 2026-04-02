"""
Skills System — Slash-command skills for TokioAI.

Skills are high-level prompts triggered by /commands that expand into
detailed instructions for the agent. Inspired by Claude Code's bundled skills.

Example: /status -> expands into a prompt that makes the agent check all systems.
"""

from .registry import SkillRegistry, Skill, get_skill_registry

__all__ = ["SkillRegistry", "Skill", "get_skill_registry"]
