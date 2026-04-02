"""
SubAgent Manager — Orchestrates worker lifecycle.

Tracks running, completed, and failed workers. Provides methods to:
- Spawn workers (parallel or sequential)
- Kill running workers
- Collect results
- Format results as task notifications for the coordinator
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from .worker import SubAgent, SubAgentResult, WorkerType

if TYPE_CHECKING:
    from ..llm import BaseLLM
    from ..tools import ToolRegistry, ToolExecutor

logger = logging.getLogger(__name__)

# Maximum concurrent workers
MAX_CONCURRENT_WORKERS = 4


class SubAgentManager:
    """Manages the lifecycle of subagent workers."""

    def __init__(
        self,
        llm: "BaseLLM",
        registry: "ToolRegistry",
        executor: "ToolExecutor",
        on_progress: Optional[Callable[[str, str], None]] = None,
    ):
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self._on_progress = on_progress

        # Track workers
        self._workers: Dict[str, SubAgent] = {}
        self._results: Dict[str, SubAgentResult] = {}
        self._running: Dict[str, asyncio.Task] = {}

        # Concurrency control
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_WORKERS)

        # Stats
        self._total_spawned = 0
        self._total_completed = 0
        self._total_failed = 0

    def spawn(
        self,
        task: str,
        worker_type: str = "general",
        description: str = "",
        context: str = "",
    ) -> str:
        """Spawn a new worker and return its agent ID.

        The worker runs in the background. Use get_result() or
        wait_for() to collect its output.

        Args:
            task: The task prompt for the worker.
            worker_type: One of "research", "implement", "verify", "general".
            description: Short description (3-5 words) for tracking.
            context: Optional additional context.

        Returns:
            The worker's agent ID.
        """
        agent_id = f"worker-{uuid.uuid4().hex[:8]}"

        try:
            wtype = WorkerType(worker_type)
        except ValueError:
            wtype = WorkerType.GENERAL

        worker = SubAgent(
            agent_id=agent_id,
            llm=self.llm,
            registry=self.registry,
            executor=self.executor,
            worker_type=wtype,
            description=description or task[:50],
            on_progress=self._on_progress,
        )

        self._workers[agent_id] = worker
        self._total_spawned += 1

        # Launch as background task with concurrency control
        async_task = asyncio.ensure_future(
            self._run_with_semaphore(worker, task, context)
        )
        self._running[agent_id] = async_task

        logger.info(
            f"Spawned worker {agent_id} [{wtype.value}]: {description or task[:50]}"
        )

        return agent_id

    async def _run_with_semaphore(
        self, worker: SubAgent, task: str, context: str
    ) -> None:
        """Run a worker with concurrency control."""
        async with self._semaphore:
            try:
                result = await worker.run(task, context)
                self._results[worker.agent_id] = result
                if result.status == "completed":
                    self._total_completed += 1
                else:
                    self._total_failed += 1
                logger.info(
                    f"Worker {worker.agent_id} {result.status} "
                    f"({result.duration_ms}ms, {result.tool_uses} tools, "
                    f"{result.rounds} rounds)"
                )
            except Exception as e:
                logger.error(f"Worker {worker.agent_id} crashed: {e}")
                self._results[worker.agent_id] = SubAgentResult(
                    agent_id=worker.agent_id,
                    worker_type=worker.worker_type.value,
                    description=worker.description,
                    status="failed",
                    result="",
                    error=str(e),
                )
                self._total_failed += 1
            finally:
                self._running.pop(worker.agent_id, None)

    async def spawn_parallel(
        self,
        tasks: List[Dict],
    ) -> List[str]:
        """Spawn multiple workers in parallel.

        Args:
            tasks: List of dicts with keys: task, worker_type, description, context.

        Returns:
            List of agent IDs.
        """
        agent_ids = []
        for t in tasks:
            aid = self.spawn(
                task=t["task"],
                worker_type=t.get("worker_type", "general"),
                description=t.get("description", ""),
                context=t.get("context", ""),
            )
            agent_ids.append(aid)
        return agent_ids

    async def wait_for(
        self, agent_id: str, timeout: float = 300.0
    ) -> Optional[SubAgentResult]:
        """Wait for a specific worker to complete.

        Args:
            agent_id: The worker's agent ID.
            timeout: Maximum wait time in seconds.

        Returns:
            SubAgentResult or None if timeout/not found.
        """
        task = self._running.get(agent_id)
        if task is None:
            # Already completed
            return self._results.get(agent_id)

        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for worker {agent_id}")
            return None

        return self._results.get(agent_id)

    async def wait_all(
        self, agent_ids: Optional[List[str]] = None, timeout: float = 600.0
    ) -> List[SubAgentResult]:
        """Wait for multiple workers to complete.

        Args:
            agent_ids: Specific IDs to wait for (None = all running).
            timeout: Maximum total wait time.

        Returns:
            List of SubAgentResults.
        """
        if agent_ids is None:
            ids = list(self._running.keys())
        else:
            ids = agent_ids

        results = []
        tasks_to_wait = []

        for aid in ids:
            task = self._running.get(aid)
            if task:
                tasks_to_wait.append((aid, task))
            elif aid in self._results:
                results.append(self._results[aid])

        if tasks_to_wait:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *[t for _, t in tasks_to_wait],
                        return_exceptions=True,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for workers")

            # Collect results
            for aid, _ in tasks_to_wait:
                if aid in self._results:
                    results.append(self._results[aid])

        return results

    def kill(self, agent_id: str) -> bool:
        """Kill a running worker.

        Args:
            agent_id: The worker to kill.

        Returns:
            True if the worker was found and killed.
        """
        worker = self._workers.get(agent_id)
        if worker:
            worker.kill()
            task = self._running.get(agent_id)
            if task:
                task.cancel()
            logger.info(f"Killed worker {agent_id}")
            return True
        return False

    def kill_all(self) -> int:
        """Kill all running workers. Returns count of workers killed."""
        count = 0
        for aid in list(self._running.keys()):
            if self.kill(aid):
                count += 1
        return count

    def get_result(self, agent_id: str) -> Optional[SubAgentResult]:
        """Get result for a completed worker."""
        return self._results.get(agent_id)

    def is_running(self, agent_id: str) -> bool:
        """Check if a worker is still running."""
        return agent_id in self._running

    def format_notification(self, result: SubAgentResult) -> str:
        """Format a worker result as a task notification (Claude Code style).

        This is injected into the main agent's context so it can
        process worker results.
        """
        notification = (
            f"<task-notification>\n"
            f"<task-id>{result.agent_id}</task-id>\n"
            f"<status>{result.status}</status>\n"
            f"<description>{result.description}</description>\n"
            f"<worker-type>{result.worker_type}</worker-type>\n"
        )

        if result.result:
            # Truncate very long results
            text = result.result
            if len(text) > 4000:
                text = text[:4000] + "\n[TRUNCADO]"
            notification += f"<result>{text}</result>\n"

        if result.error:
            notification += f"<error>{result.error}</error>\n"

        notification += (
            f"<usage>\n"
            f"  <tool_uses>{result.tool_uses}</tool_uses>\n"
            f"  <rounds>{result.rounds}</rounds>\n"
            f"  <duration_ms>{result.duration_ms}</duration_ms>\n"
            f"</usage>\n"
            f"</task-notification>"
        )

        return notification

    def format_all_results(self, results: List[SubAgentResult]) -> str:
        """Format multiple worker results for the coordinator."""
        if not results:
            return "No hay resultados de workers."

        parts = []
        for r in results:
            parts.append(self.format_notification(r))

        return "\n\n".join(parts)

    def get_status(self) -> Dict:
        """Get manager status."""
        return {
            "running": len(self._running),
            "completed": self._total_completed,
            "failed": self._total_failed,
            "total_spawned": self._total_spawned,
            "running_ids": list(self._running.keys()),
        }
