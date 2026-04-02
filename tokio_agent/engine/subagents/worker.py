"""
SubAgent Worker — An autonomous worker that runs its own agent loop.

Each worker gets:
- Its own system prompt (role-specific)
- A task prompt (what to do)
- Access to a subset of tools
- Its own Think→Act→Observe loop (max 15 rounds)
- A result that's reported back to the coordinator

Workers are cheap to spawn and run in parallel for independent tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import BaseLLM
    from ..tools import ToolRegistry, ToolExecutor

logger = logging.getLogger(__name__)

# Regex for tool calls (shared with main agent)
TOOL_CALL_RE = re.compile(r'TOOL:(\w+)\((\{.*?\}|\(\))\)', re.DOTALL)
TOOL_CALL_SIMPLE_RE = re.compile(r'TOOL:(\w+)\(\)')


class WorkerType(str, Enum):
    """Types of workers with different capabilities."""
    RESEARCH = "research"        # Read-only: search, read, analyze
    IMPLEMENT = "implement"      # Read-write: edit, create, execute
    VERIFY = "verify"           # Read + test: run tests, check output
    GENERAL = "general"         # All tools available


# Tool restrictions per worker type
WORKER_TOOL_RESTRICTIONS = {
    WorkerType.RESEARCH: {
        "allowed": {
            "bash", "read_file", "search_code", "list_files",
            "postgres_query", "raspi_vision", "gcp_status",
            "web_search", "web_fetch", "weather", "ha_status",
        },
        "blocked": {
            "write_file", "docker", "host_control", "self_heal",
            "gcp_waf_deploy", "router_control",
        },
    },
    WorkerType.VERIFY: {
        "allowed": {
            "bash", "read_file", "search_code", "list_files",
            "postgres_query", "python",
        },
        "blocked": {
            "write_file", "docker", "host_control", "self_heal",
            "gcp_waf_deploy", "router_control",
        },
    },
    WorkerType.IMPLEMENT: {
        "allowed": None,  # All tools
        "blocked": {
            "self_heal", "gcp_waf_deploy", "router_control",
        },
    },
    WorkerType.GENERAL: {
        "allowed": None,  # All tools
        "blocked": set(),
    },
}


@dataclass
class SubAgentResult:
    """Result from a completed subagent."""
    agent_id: str
    worker_type: str
    description: str
    status: str  # "completed", "failed", "timeout", "killed"
    result: str  # The worker's final text output
    error: Optional[str] = None
    duration_ms: int = 0
    tool_uses: int = 0
    rounds: int = 0


class SubAgent:
    """An autonomous worker agent."""

    MAX_ROUNDS = 15
    MAX_TIME = 300  # 5 minutes per worker

    def __init__(
        self,
        agent_id: str,
        llm: "BaseLLM",
        registry: "ToolRegistry",
        executor: "ToolExecutor",
        worker_type: WorkerType = WorkerType.GENERAL,
        description: str = "",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ):
        self.agent_id = agent_id
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self.worker_type = worker_type
        self.description = description
        self._on_progress = on_progress
        self._killed = False
        self._tool_uses = 0
        self._rounds = 0

    def kill(self) -> None:
        """Signal this worker to stop."""
        self._killed = True

    async def run(self, task_prompt: str, context: str = "") -> SubAgentResult:
        """Run the worker's autonomous loop.

        Args:
            task_prompt: What this worker should do.
            context: Optional additional context (e.g., from coordinator).

        Returns:
            SubAgentResult with the worker's output.
        """
        start_time = time.monotonic()

        system_prompt = self._build_system_prompt()
        full_prompt = self._build_task_prompt(task_prompt, context)

        accumulated_context = ""
        final_response = ""

        for round_num in range(self.MAX_ROUNDS):
            if self._killed:
                return SubAgentResult(
                    agent_id=self.agent_id,
                    worker_type=self.worker_type.value,
                    description=self.description,
                    status="killed",
                    result=accumulated_context or "Worker was stopped.",
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                    tool_uses=self._tool_uses,
                    rounds=round_num,
                )

            elapsed = time.monotonic() - start_time
            if elapsed > self.MAX_TIME:
                return SubAgentResult(
                    agent_id=self.agent_id,
                    worker_type=self.worker_type.value,
                    description=self.description,
                    status="timeout",
                    result=accumulated_context or "Worker timed out.",
                    duration_ms=int(elapsed * 1000),
                    tool_uses=self._tool_uses,
                    rounds=round_num,
                )

            # Build prompt for this round
            prompt = full_prompt
            if accumulated_context:
                prompt = (
                    f"{full_prompt}\n\n"
                    f"# Previous tool results:\n{accumulated_context}\n\n"
                    f"Continue with the task. If done, provide your final report."
                )

            try:
                response = await self.llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    max_tokens=4096,
                    temperature=0.2,
                )
            except Exception as e:
                logger.error(f"Worker {self.agent_id} LLM error: {e}")
                return SubAgentResult(
                    agent_id=self.agent_id,
                    worker_type=self.worker_type.value,
                    description=self.description,
                    status="failed",
                    result=accumulated_context,
                    error=str(e),
                    duration_ms=int((time.monotonic() - start_time) * 1000),
                    tool_uses=self._tool_uses,
                    rounds=round_num,
                )

            llm_text = response.text
            self._rounds = round_num + 1

            # Report progress
            if self._on_progress:
                self._on_progress(self.agent_id, f"Round {round_num + 1}")

            # Extract tool calls
            tool_calls = self._extract_tool_calls(llm_text)

            if not tool_calls:
                # No tool calls — this is the final response
                final_response = llm_text
                break

            # Filter tool calls by worker type restrictions
            tool_calls = self._filter_tool_calls(tool_calls)

            # Execute tools
            for tool_name, tool_args in tool_calls:
                try:
                    result = await asyncio.wait_for(
                        self.executor.execute(tool_name, tool_args),
                        timeout=60.0,
                    )
                    self._tool_uses += 1

                    if result.success:
                        output = result.output or ""
                        if len(output) > 6000:
                            output = output[:6000] + "\n[TRUNCADO]"
                        accumulated_context += f"\n## {tool_name}:\n{output}\n"
                    else:
                        accumulated_context += (
                            f"\n## Error in {tool_name}:\n{result.error}\n"
                        )
                except asyncio.TimeoutError:
                    self._tool_uses += 1
                    accumulated_context += (
                        f"\n## {tool_name}: timeout\n"
                    )
                except Exception as e:
                    accumulated_context += (
                        f"\n## {tool_name}: error: {e}\n"
                    )
        else:
            final_response = accumulated_context or "Max rounds reached."

        # Clean tool call syntax from response
        clean = TOOL_CALL_RE.sub("", final_response)
        clean = TOOL_CALL_SIMPLE_RE.sub("", clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

        duration_ms = int((time.monotonic() - start_time) * 1000)

        return SubAgentResult(
            agent_id=self.agent_id,
            worker_type=self.worker_type.value,
            description=self.description,
            status="completed",
            result=clean,
            duration_ms=duration_ms,
            tool_uses=self._tool_uses,
            rounds=self._rounds,
        )

    def _build_system_prompt(self) -> str:
        """Build system prompt based on worker type."""
        role_prompts = {
            WorkerType.RESEARCH: (
                "Eres un worker de INVESTIGACION de TokioAI. "
                "Tu trabajo es buscar, leer y analizar informacion. "
                "NO modifiques archivos. Solo reporta hallazgos."
            ),
            WorkerType.IMPLEMENT: (
                "Eres un worker de IMPLEMENTACION de TokioAI. "
                "Tu trabajo es hacer cambios en el codigo segun las "
                "especificaciones que recibas. Edita archivos, crea "
                "codigo, ejecuta comandos necesarios."
            ),
            WorkerType.VERIFY: (
                "Eres un worker de VERIFICACION de TokioAI. "
                "Tu trabajo es probar que el codigo funciona. "
                "Ejecuta tests, verifica outputs, reporta problemas."
            ),
            WorkerType.GENERAL: (
                "Eres un worker autonomo de TokioAI. "
                "Ejecuta la tarea asignada usando las herramientas disponibles."
            ),
        }

        # Build available tools description for this worker
        restrictions = WORKER_TOOL_RESTRICTIONS.get(self.worker_type, {})
        allowed = restrictions.get("allowed")
        blocked = restrictions.get("blocked", set())

        tool_names = self.registry.list_names()
        if allowed is not None:
            available = [n for n in tool_names if n in allowed]
        else:
            available = [n for n in tool_names if n not in blocked]

        tools_desc = ", ".join(available) if available else "todas las herramientas"

        return f"""{role_prompts.get(self.worker_type, role_prompts[WorkerType.GENERAL])}

# Herramientas disponibles: {tools_desc}

# Reglas del worker
1. Ejecuta la tarea directamente con herramientas. NO preguntes, NO sugiereas.
2. Usa el formato TOOL:nombre({{"param": "valor"}}) para llamar herramientas.
3. Cuando termines, da un reporte conciso de lo que hiciste/encontraste.
4. Mantene tu reporte bajo 500 palabras. Se factual y conciso.
5. Si algo falla, intenta un enfoque alternativo antes de reportar error.
6. NO hagas trabajo fuera del scope de tu tarea asignada.
"""

    def _build_task_prompt(self, task: str, context: str) -> str:
        """Build the full task prompt."""
        prompt = f"# Tarea Asignada\n\n{task}"
        if context:
            prompt += f"\n\n# Contexto Adicional\n\n{context}"
        return prompt

    def _extract_tool_calls(self, text: str) -> List[tuple]:
        """Extract tool calls from LLM output."""
        calls = []

        for match in TOOL_CALL_RE.finditer(text):
            name = match.group(1)
            args_str = match.group(2)
            try:
                if args_str in ("()", "{}"):
                    args = {}
                else:
                    args = json.loads(args_str)
                calls.append((name, args))
            except json.JSONDecodeError:
                pass

        if not calls:
            for match in TOOL_CALL_SIMPLE_RE.finditer(text):
                calls.append((match.group(1), {}))

        return calls

    def _filter_tool_calls(
        self, calls: List[tuple]
    ) -> List[tuple]:
        """Filter tool calls based on worker type restrictions."""
        restrictions = WORKER_TOOL_RESTRICTIONS.get(self.worker_type, {})
        allowed = restrictions.get("allowed")
        blocked = restrictions.get("blocked", set())

        filtered = []
        for name, args in calls:
            if allowed is not None and name not in allowed:
                logger.info(
                    f"Worker {self.agent_id}: blocked tool {name} "
                    f"(not in {self.worker_type.value} allowlist)"
                )
                continue
            if name in blocked:
                logger.info(
                    f"Worker {self.agent_id}: blocked tool {name} "
                    f"(in {self.worker_type.value} blocklist)"
                )
                continue
            filtered.append((name, args))

        return filtered
