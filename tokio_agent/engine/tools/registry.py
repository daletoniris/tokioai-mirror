"""
Tool Registry — Central catalog of all available tools.

Tools are registered with metadata and an async-compatible executor function.
Supports base tools, plugins, and dynamically generated tools.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """Definition of a registered tool."""
    name: str
    description: str
    category: str
    parameters: Dict[str, str]  # param_name -> description
    executor: Callable[..., Any]
    source: str = "builtin"  # builtin | plugin | generated
    examples: Optional[List[str]] = field(default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": self.parameters,
            "source": self.source,
            "examples": self.examples or [],
        }


class ToolRegistry:
    """Central registry for all tools."""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        category: str,
        parameters: Dict[str, str],
        executor: Callable[..., Any],
        source: str = "builtin",
        examples: Optional[List[str]] = None,
    ) -> None:
        """Register a tool. Overwrites if name already exists."""
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            category=category,
            parameters=parameters,
            executor=executor,
            source=source,
            examples=examples,
        )
        logger.debug(f"Registered tool: {name} [{category}]")

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_all(self) -> List[ToolDef]:
        return list(self._tools.values())

    def list_names(self) -> List[str]:
        return list(self._tools.keys())

    def list_by_category(self) -> Dict[str, List[ToolDef]]:
        cats: Dict[str, List[ToolDef]] = {}
        for tool in self._tools.values():
            cats.setdefault(tool.category, []).append(tool)
        return cats

    def count(self) -> int:
        return len(self._tools)

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def describe_for_prompt(self) -> str:
        """Generate a compact description of all tools for the LLM prompt."""
        if not self._tools:
            return "No tools available."

        lines = [f"You have {len(self._tools)} tools available:\n"]

        for cat, tools in sorted(self.list_by_category().items()):
            lines.append(f"## {cat}")
            for t in tools:
                params_str = ", ".join(t.parameters.keys()) if t.parameters else "(none)"
                lines.append(f"- **{t.name}**({params_str}): {t.description}")
                if t.examples:
                    for ex in t.examples[:2]:
                        lines.append(f"  Example: `{ex}`")
            lines.append("")

        lines.append(
            '## How to call tools\n'
            'Respond with one or more lines:\n'
            '```\n'
            'TOOL:tool_name({"param": "value"})\n'
            '```\n'
            'For tools with no params: `TOOL:tool_name()`\n'
            'You can call multiple tools. Results will be provided after execution.'
        )
        return "\n".join(lines)
