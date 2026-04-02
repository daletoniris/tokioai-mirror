"""
Skill Registry — Central registry for all slash-command skills.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Singleton registry
_registry: Optional["SkillRegistry"] = None


@dataclass
class Skill:
    """A registered skill."""
    name: str
    description: str
    get_prompt: Callable[[str], str]  # Takes optional args, returns full prompt
    aliases: List[str] = field(default_factory=list)
    hidden: bool = False  # Hidden skills don't show in /help


class SkillRegistry:
    """Registry for slash-command skills."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._aliases: Dict[str, str] = {}

    def register(
        self,
        name: str,
        description: str,
        get_prompt: Callable[[str], str],
        aliases: Optional[List[str]] = None,
        hidden: bool = False,
    ) -> None:
        """Register a new skill."""
        skill = Skill(
            name=name,
            description=description,
            get_prompt=get_prompt,
            aliases=aliases or [],
            hidden=hidden,
        )
        self._skills[name] = skill

        # Register aliases
        for alias in skill.aliases:
            self._aliases[alias] = name

        logger.info(f"Skill registered: /{name} — {description}")

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name or alias."""
        # Direct match
        if name in self._skills:
            return self._skills[name]
        # Alias match
        if name in self._aliases:
            return self._skills.get(self._aliases[name])
        return None

    def expand(self, command: str) -> Optional[str]:
        """Expand a /command into its full prompt.

        Args:
            command: The full command string (e.g., "/status all" or "/deploy gcp")

        Returns:
            Expanded prompt string, or None if no matching skill.
        """
        parts = command.strip().split(None, 1)
        if not parts:
            return None

        skill_name = parts[0].lstrip("/")
        args = parts[1] if len(parts) > 1 else ""

        skill = self.get(skill_name)
        if not skill:
            return None

        return skill.get_prompt(args)

    def list_skills(self, include_hidden: bool = False) -> List[Dict[str, str]]:
        """List all registered skills."""
        skills = []
        for name, skill in sorted(self._skills.items()):
            if skill.hidden and not include_hidden:
                continue
            skills.append({
                "name": f"/{name}",
                "description": skill.description,
                "aliases": [f"/{a}" for a in skill.aliases],
            })
        return skills

    def format_help(self) -> str:
        """Format skills list for display."""
        skills = self.list_skills()
        if not skills:
            return "No hay skills registrados."

        lines = ["**Skills disponibles:**\n"]
        for s in skills:
            aliases = f" ({', '.join(s['aliases'])})" if s['aliases'] else ""
            lines.append(f"  `{s['name']}`{aliases} — {s['description']}")
        return "\n".join(lines)

    def is_skill_command(self, message: str) -> bool:
        """Check if a message starts with a known /command."""
        if not message.startswith("/"):
            return False
        parts = message.strip().split(None, 1)
        name = parts[0].lstrip("/")
        return self.get(name) is not None


def get_skill_registry() -> SkillRegistry:
    """Get or create the singleton skill registry."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
