"""
TokioAI Agent — The autonomous agent loop.

Implements: Think → Act → Observe → Learn
With robust tool parsing, error recovery, and no infinite loops.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .llm import BaseLLM, LLMResponse, create_llm
from .tools import ToolRegistry, ToolExecutor, ToolResult
from .tools.builtin.loader import load_builtin_tools
from .tools.plugins.loader import load_plugins
from .memory.workspace import Workspace
from .memory.session import SessionManager
from .context_builder import build_system_prompt
from .error_learner import ErrorLearner
from .security.prompt_guard import PromptGuard
from .security.input_sanitizer import sanitize_command, sanitize_sql
from .watchdog import get_watchdog

logger = logging.getLogger(__name__)

# Regex to extract tool calls from LLM output
# Matches: TOOL:name({"key": "value"})  or  TOOL:name()
TOOL_CALL_RE = re.compile(
    r'TOOL:(\w+)\((\{.*?\}|\(\))\)',
    re.DOTALL,
)

# Also match simpler format without braces for empty args
TOOL_CALL_SIMPLE_RE = re.compile(
    r'TOOL:(\w+)\(\)',
)


class TokioAgent:
    """The main autonomous agent."""

    MAX_TOOL_ROUNDS = 10  # Max consecutive tool-use rounds per message
    MAX_TOTAL_TIME = 600  # Max 10 minutes per message

    def __init__(
        self,
        llm: Optional[BaseLLM] = None,
        workspace: Optional[Workspace] = None,
        plugin_dirs: Optional[List[str]] = None,
        on_tool_start: Optional[Callable] = None,
        on_tool_end: Optional[Callable] = None,
        on_thinking: Optional[Callable] = None,
    ):
        # Initialize components
        self.workspace = workspace or Workspace()
        self.llm = llm or create_llm()
        self.registry = ToolRegistry()
        self.executor = ToolExecutor(self.registry)
        self.error_learner = ErrorLearner()
        self.prompt_guard = PromptGuard(strict_mode=True)
        self.session_manager = SessionManager(self.workspace)

        # Event callbacks for UI
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._on_thinking = on_thinking

        # Load all tools (builtin + plugins)
        load_builtin_tools(self.registry)
        self._load_extra_tools(plugin_dirs)

        # Watchdog
        self.watchdog = get_watchdog()

        # Stats
        self._stats = {
            "messages_processed": 0,
            "tools_executed": 0,
            "errors_recovered": 0,
            "total_tokens": 0,
        }

        logger.info(
            f"🚀 TokioAI Agent initialized: "
            f"LLM={self.llm.display_name()}, "
            f"Tools={self.registry.count()}"
        )

    def start_background_tasks(self):
        """Start background tasks (watchdog, etc.). Call once after event loop is running."""
        self.watchdog.start()

    async def process_message(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Process a user message through the Think → Act → Observe → Learn loop.

        Args:
            user_message: The user's input.
            session_id: Optional session ID for conversation continuity.
            images: Optional list of images [{"data": base64, "media_type": "image/..."}].

        Returns:
            The agent's final response.
        """
        start_time = time.monotonic()
        self._stats["messages_processed"] += 1

        # ── SECURITY: Prompt Guard ──
        guard_result = self.prompt_guard.check(user_message)
        if guard_result.blocked:
            threat_names = ", ".join(t[0] for t in guard_result.threats)
            logger.warning(f"🛡️ Input blocked: {threat_names}")
            return (
                "🛡️ Tu mensaje fue bloqueado por el sistema de seguridad. "
                f"Razón: {guard_result.threats[0][2] if guard_result.threats else 'contenido sospechoso'}. "
                "Por favor, reformula tu solicitud."
            )
        # Use sanitized input
        user_message = guard_result.sanitized_input

        # Ensure session exists
        if not session_id:
            session_id = self.session_manager.create_session()

        # Record user message
        self.session_manager.add_message(session_id, "user", user_message)

        # Build system prompt
        system_prompt = build_system_prompt(
            workspace=self.workspace,
            registry=self.registry,
            extra_instructions=self._get_extra_instructions(),
        )

        # Get conversation history (excludes the message we just added)
        full_conversation = self.session_manager.get_conversation(session_id)
        # Remove the last message (the one we just added) since it's passed as user_prompt
        # But only if there are at least 2 messages — don't lose all context
        if full_conversation and len(full_conversation) > 1:
            conversation = full_conversation[:-1]
        else:
            conversation = None

        # ── Think → Act → Observe → Learn loop ──
        accumulated_context = ""
        final_response = ""

        for round_num in range(self.MAX_TOOL_ROUNDS):
            # Check time budget
            elapsed = time.monotonic() - start_time
            if elapsed > self.MAX_TOTAL_TIME:
                final_response = (
                    "⏱️ Se alcanzó el tiempo máximo. "
                    "Aquí está lo que logré hasta ahora:\n\n"
                    + accumulated_context
                )
                break

            # Emit thinking event
            if self._on_thinking:
                self._on_thinking(round_num + 1)

            # ── THINK: Call LLM ──
            prompt = user_message
            if accumulated_context:
                prompt = (
                    f"{user_message}\n\n"
                    f"# Previous tool results:\n{accumulated_context}\n\n"
                    f"Continue with the task. If done, provide the final answer."
                )

            try:
                # Only pass images on the first round (they're part of the initial prompt)
                round_images = images if round_num == 0 else None
                response = await self.llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    conversation=conversation,
                    max_tokens=4096,
                    temperature=0.3,
                    images=round_images,
                )
                self._stats["total_tokens"] += response.input_tokens + response.output_tokens
            except Exception as e:
                logger.error(f"LLM error: {e}")
                final_response = f"Error comunicando con el LLM: {e}"
                break

            llm_text = response.text

            # ── ACT: Extract and execute tool calls ──
            tool_calls = self._extract_tool_calls(llm_text)

            if not tool_calls:
                # No tool calls — this is the final response
                final_response = llm_text
                break

            # Execute tool calls — parallel if independent, sequential if chained
            tool_results_text = ""
            if len(tool_calls) > 1 and self._are_independent(tool_calls):
                # Parallel execution
                tool_results_text = await self._execute_tools_parallel(tool_calls)
            else:
                # Sequential execution
                for tool_name, tool_args in tool_calls:
                    tool_results_text += await self._execute_single_tool(
                        tool_name, tool_args
                    )

            accumulated_context += tool_results_text

        else:
            # Exhausted all rounds
            final_response = (
                "Se alcanzó el máximo de iteraciones. "
                "Resultado parcial:\n\n" + accumulated_context
            )

        # Clean up tool call syntax from final response
        clean_response = self._clean_response(final_response)

        # Record assistant response
        self.session_manager.add_message(session_id, "assistant", clean_response)

        # Check if user shared their name
        self._detect_user_info(user_message)

        return clean_response

    def _are_independent(self, tool_calls: List[Tuple[str, Dict]]) -> bool:
        """Check if tool calls are independent (can run in parallel)."""
        names = [name for name, _ in tool_calls]
        # Different tools are likely independent
        # Same tool called multiple times with different args is also independent
        # Tools that modify state should not run in parallel
        stateful = {"bash", "write_file", "python", "host_control", "docker",
                    "task_orchestrator", "self_heal"}
        return not any(n in stateful for n in names)

    async def _execute_single_tool(self, tool_name: str, tool_args: Dict) -> str:
        """Execute a single tool call and return formatted result text."""
        if self._on_tool_start:
            self._on_tool_start(tool_name, tool_args)

        tool_args = self._sanitize_tool_args(tool_name, tool_args)

        # Adaptive timeout
        timeout = self._get_tool_timeout(tool_name)
        try:
            result = await asyncio.wait_for(
                self.executor.execute(tool_name, tool_args),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._stats["tools_executed"] += 1
            return (
                f"\n## Error in {tool_name} (timeout after {timeout}s):\n"
                f"Tool execution timed out.\n"
            )

        self._stats["tools_executed"] += 1

        if self._on_tool_end:
            self._on_tool_end(tool_name, result)

        if result.success:
            self.error_learner.reset_tool(tool_name)
            output = result.output or ""
            if len(output) > 8000:
                output = output[:8000] + "\n[TRUNCADO]"
            return f"\n## Result of {tool_name}:\n{output}\n"
        else:
            suggestion = self.error_learner.analyze_error(tool_name, result.error or "")
            if suggestion is None:
                return (
                    f"\n## Error in {tool_name} (max retries reached):\n"
                    f"{result.error}\n"
                    f"Do NOT retry this tool. Use an alternative approach.\n"
                )
            else:
                self._stats["errors_recovered"] += 1
                return (
                    f"\n## Error in {tool_name}:\n"
                    f"{result.error}\n"
                    f"Suggestion: {suggestion}\n"
                )

    async def _execute_tools_parallel(self, tool_calls: List[Tuple[str, Dict]]) -> str:
        """Execute independent tools in parallel."""
        tasks = []
        for tool_name, tool_args in tool_calls:
            tasks.append(self._execute_single_tool(tool_name, tool_args))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        text = ""
        for r in results:
            if isinstance(r, Exception):
                text += f"\n## Error: {r}\n"
            else:
                text += r
        return text

    def _get_tool_timeout(self, tool_name: str) -> float:
        """Get adaptive timeout for a tool based on its type."""
        slow_tools = {"gcp_waf_deploy", "gcp_compute", "task_orchestrator", "bash"}
        medium_tools = {"docker", "host_control", "router_control", "self_heal",
                        "document", "postgres_query"}
        if tool_name in slow_tools:
            return 300.0
        if tool_name in medium_tools:
            return 120.0
        return 60.0

    def _extract_tool_calls(
        self, text: str
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Extract tool calls from LLM output.

        Supports formats:
            TOOL:name({"key": "value"})
            TOOL:name()
        """
        calls = []

        # Try the full format first
        for match in TOOL_CALL_RE.finditer(text):
            name = match.group(1)
            args_str = match.group(2)
            try:
                if args_str == "()" or args_str == "{}":
                    args = {}
                else:
                    args = json.loads(args_str)
                calls.append((name, args))
            except json.JSONDecodeError:
                # Try to fix common JSON issues
                fixed = self._fix_json(args_str)
                if fixed is not None:
                    calls.append((name, fixed))
                else:
                    logger.warning(f"Could not parse tool args: {args_str[:100]}")

        # Also check simple format
        if not calls:
            for match in TOOL_CALL_SIMPLE_RE.finditer(text):
                name = match.group(1)
                calls.append((name, {}))

        # Deduplicate (same tool+args in same response)
        seen = set()
        unique_calls = []
        for name, args in calls:
            key = f"{name}:{json.dumps(args, sort_keys=True)}"
            if key not in seen:
                seen.add(key)
                unique_calls.append((name, args))

        return unique_calls

    def _fix_json(self, s: str) -> Optional[Dict]:
        """Attempt to fix common JSON parsing issues."""
        # Remove trailing/leading whitespace
        s = s.strip()

        # Try wrapping in braces
        if not s.startswith("{"):
            s = "{" + s + "}"

        # Fix single quotes
        s = s.replace("'", '"')

        # Fix trailing commas
        s = re.sub(r',\s*}', '}', s)
        s = re.sub(r',\s*]', ']', s)

        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    def _clean_response(self, text: str) -> str:
        """Remove tool call syntax from the final response."""
        # Remove TOOL: lines
        cleaned = TOOL_CALL_RE.sub("", text)
        cleaned = TOOL_CALL_SIMPLE_RE.sub("", cleaned)

        # Clean up excessive whitespace
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def _sanitize_tool_args(
        self, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply security sanitization to tool arguments."""
        if tool_name == "bash" and "command" in args:
            is_safe, sanitized, warning = sanitize_command(args["command"])
            if not is_safe:
                # Replace the command with an echo of the warning
                args["command"] = f"echo '{warning}'"
                logger.warning(f"🛡️ Blocked bash command: {warning}")
            elif warning:
                logger.info(f"🛡️ Bash warning: {warning}")

        elif tool_name == "postgres_query" and "query" in args:
            is_safe, sanitized, warning = sanitize_sql(args["query"])
            if not is_safe:
                args["query"] = f"SELECT '{warning}' as security_block"
                logger.warning(f"🛡️ Blocked SQL: {warning}")
            elif warning:
                logger.info(f"🛡️ SQL warning: {warning}")

        return args

    def _detect_user_info(self, message: str) -> None:
        """Detect if the user shared personal info to remember."""
        lower = message.lower()

        # Detect name patterns
        name_patterns = [
            r"(?:me llamo|mi nombre es|soy|llamame|llámame)\s+(\w+)",
            r"(?:my name is|i'm|call me)\s+(\w+)",
        ]
        for pattern in name_patterns:
            m = re.search(pattern, lower)
            if m:
                name = m.group(1).capitalize()
                self.workspace.set_preference("user_name", name)
                self.workspace.add_memory(f"El usuario se llama {name}")
                logger.info(f"👤 Detected user name: {name}")
                break

    def _get_extra_instructions(self) -> List[str]:
        """Get extra instructions from skills, error learner, etc."""
        instructions = []

        # Error learner context
        error_ctx = self.error_learner.get_context_for_prompt()
        if error_ctx:
            instructions.append(error_ctx)

        return instructions

    def _load_extra_tools(self, plugin_dirs: Optional[List[str]]) -> None:
        """Load plugin tools."""
        dirs = plugin_dirs or []

        # Check for v1.8 tools
        v18_dir = "/home/tokio/tokioai-v1.8/tokio-cli/engine/tools"
        import os
        if os.path.isdir(v18_dir):
            dirs.append(v18_dir)

        if dirs:
            load_plugins(self.registry, dirs)

    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return {
            **self._stats,
            "llm": self.llm.display_name(),
            "tools_count": self.registry.count(),
            "tools": self.registry.list_names(),
        }
