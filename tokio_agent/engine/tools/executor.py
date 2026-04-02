"""
Tool Executor — Robust async execution with timeouts, retries, and error capture.

Key improvements over v1:
- All tool execution is truly async (no blocking subprocess.run)
- Adaptive timeouts based on tool type
- Structured error capture with suggestions
- Circuit breaker for repeatedly failing tools
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    success: bool
    output: str
    error: Optional[str] = None
    execution_time: float = 0.0
    args: Optional[Dict] = field(default=None, repr=False)


class ToolExecutor:
    """Executes tools from the registry with robust error handling."""

    # Tools that can take a long time
    SLOW_TOOLS = {"bash", "python", "docker", "gcp_waf", "host_control", "subagent"}
    DEFAULT_TIMEOUT = 60
    SLOW_TIMEOUT = 300

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._failure_counts: Dict[str, int] = defaultdict(int)
        self._circuit_open: Dict[str, float] = {}  # tool -> timestamp when opened

    async def execute(
        self,
        tool_name: str,
        args: Dict[str, Any],
        timeout: Optional[int] = None,
    ) -> ToolResult:
        """Execute a tool by name with the given arguments.

        Args:
            tool_name: Name of the registered tool.
            args: Dictionary of arguments to pass.
            timeout: Override timeout in seconds.

        Returns:
            ToolResult with success status, output, and timing.
        """
        start = time.monotonic()

        # Check circuit breaker
        if self._is_circuit_open(tool_name):
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=(
                    f"Tool '{tool_name}' está temporalmente deshabilitada "
                    f"por fallos repetidos. Intenta con otra herramienta."
                ),
                args=args,
            )

        # Look up tool
        tool_def = self.registry.get(tool_name)
        if not tool_def:
            available = ", ".join(self.registry.list_names()[:15])
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=(
                    f"Tool '{tool_name}' no encontrada. "
                    f"Disponibles: {available}"
                ),
                args=args,
            )

        # Determine timeout
        if timeout is None:
            timeout = (
                self.SLOW_TIMEOUT
                if tool_name in self.SLOW_TOOLS
                else self.DEFAULT_TIMEOUT
            )

        try:
            # Execute — handle both sync and async executors
            executor = tool_def.executor
            if inspect.iscoroutinefunction(executor):
                raw_result = await asyncio.wait_for(
                    executor(**args), timeout=timeout
                )
            else:
                raw_result = await asyncio.wait_for(
                    asyncio.to_thread(executor, **args),
                    timeout=timeout,
                )

            elapsed = time.monotonic() - start
            output = str(raw_result) if raw_result is not None else ""

            # Reset failure count on success
            self._failure_counts[tool_name] = 0

            return ToolResult(
                tool_name=tool_name,
                success=True,
                output=output,
                execution_time=elapsed,
                args=args,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            self._record_failure(tool_name)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=(
                    f"Timeout: '{tool_name}' excedió {timeout}s. "
                    f"Intenta con un comando más simple o divide la tarea."
                ),
                execution_time=elapsed,
                args=args,
            )

        except TypeError as e:
            # Wrong arguments passed to the tool
            elapsed = time.monotonic() - start
            expected = list(tool_def.parameters.keys())
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=(
                    f"Argumentos incorrectos para '{tool_name}': {e}. "
                    f"Parámetros esperados: {expected}"
                ),
                execution_time=elapsed,
                args=args,
            )

        except Exception as e:
            elapsed = time.monotonic() - start
            self._record_failure(tool_name)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output="",
                error=f"Error ejecutando '{tool_name}': {type(e).__name__}: {e}",
                execution_time=elapsed,
                args=args,
            )

    async def execute_many(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[ToolResult]:
        """Execute multiple tool calls sequentially.

        Args:
            calls: List of {"name": str, "args": dict}

        Returns:
            List of ToolResult in the same order.
        """
        results = []
        for call in calls:
            result = await self.execute(call["name"], call.get("args", {}))
            results.append(result)
        return results

    def _record_failure(self, tool_name: str) -> None:
        self._failure_counts[tool_name] += 1
        if self._failure_counts[tool_name] >= 5:
            # Open circuit breaker for 60 seconds
            self._circuit_open[tool_name] = time.monotonic()
            logger.warning(
                f"🔴 Circuit breaker opened for '{tool_name}' "
                f"after {self._failure_counts[tool_name]} failures"
            )

    def _is_circuit_open(self, tool_name: str) -> bool:
        opened_at = self._circuit_open.get(tool_name)
        if opened_at is None:
            return False
        # Auto-close after 60 seconds
        if time.monotonic() - opened_at > 60:
            del self._circuit_open[tool_name]
            self._failure_counts[tool_name] = 0
            return False
        return True
