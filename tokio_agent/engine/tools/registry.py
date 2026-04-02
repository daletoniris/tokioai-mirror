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
        """Generate a compact description of all tools for the LLM prompt.

        Note: With native tool use, the API handles tool descriptions.
        This is only used as supplementary context.
        """
        if not self._tools:
            return "No tools available."

        lines = [f"You have {len(self._tools)} tools available. Use them freely to accomplish tasks.\n"]

        for cat, tools in sorted(self.list_by_category().items()):
            lines.append(f"## {cat}")
            for t in tools:
                params_str = ", ".join(t.parameters.keys()) if t.parameters else "(none)"
                lines.append(f"- **{t.name}**({params_str}): {t.description}")
            lines.append("")

        return "\n".join(lines)

    def to_anthropic_tools(self) -> list:
        """Export tools as Anthropic API tool definitions.

        Returns list of dicts compatible with the Anthropic tools parameter.
        """
        tools = []
        for t in self._tools.values():
            # Build JSON schema from parameter descriptions
            properties = {}
            required = []
            for param_name, param_desc in t.parameters.items():
                # Detect type hints in description
                param_type = "string"
                desc_lower = param_desc.lower()
                if "true|false" in desc_lower or "boolean" in desc_lower:
                    param_type = "boolean"
                elif param_name == "tasks" or (param_desc.startswith("[") and "list" in desc_lower):
                    param_type = "array"
                elif param_name == "params" or "dict" in desc_lower or "objeto" in desc_lower:
                    # Parameters like "params" in iot_control, gcp_waf, etc. are objects
                    param_type = "object"

                prop: dict = {"type": param_type, "description": param_desc}
                if param_type == "array":
                    prop["items"] = {"type": "object"}
                properties[param_name] = prop

                # Mark as required if not described as optional
                if "optional" not in param_desc.lower() and "default" not in param_desc.lower():
                    required.append(param_name)

            tool_def = {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                },
            }
            if required:
                tool_def["input_schema"]["required"] = required

            tools.append(tool_def)

        return tools

    def to_openai_tools(self) -> list:
        """Export tools as OpenAI API tool definitions.

        OpenAI format wraps each tool in {"type": "function", "function": {...}}.
        """
        anthropic_tools = self.to_anthropic_tools()
        openai_tools = []
        for t in anthropic_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            })
        return openai_tools
