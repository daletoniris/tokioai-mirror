"""
Subagent Tool — Allows the main agent to spawn and manage workers.

This is the bridge between the main agent's tool system and the
subagent manager. The main agent calls TOOL:subagent({...}) and this
tool handles spawning, waiting, killing, and collecting results.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import SubAgentManager

logger = logging.getLogger(__name__)


async def execute_subagent_tool(
    manager: "SubAgentManager",
    args: Dict[str, Any],
) -> str:
    """Execute a subagent tool call.

    Args:
        manager: The SubAgentManager instance.
        args: Tool arguments with "action" and action-specific params.

    Returns:
        Result string.
    """
    action = args.get("action", "spawn")

    if action == "spawn":
        return _handle_spawn(manager, args)

    elif action == "spawn_parallel":
        return await _handle_spawn_parallel(manager, args)

    elif action == "wait":
        return await _handle_wait(manager, args)

    elif action == "wait_all":
        return await _handle_wait_all(manager, args)

    elif action == "kill":
        return _handle_kill(manager, args)

    elif action == "status":
        return _handle_status(manager)

    elif action == "results":
        return _handle_results(manager, args)

    else:
        return f"Error: accion desconocida '{action}'. Acciones validas: spawn, spawn_parallel, wait, wait_all, kill, status, results"


def _handle_spawn(manager: "SubAgentManager", args: Dict) -> str:
    """Spawn a single worker."""
    task = args.get("task", "")
    if not task:
        return "Error: 'task' es requerido para spawn."

    agent_id = manager.spawn(
        task=task,
        worker_type=args.get("worker_type", "general"),
        description=args.get("description", ""),
        context=args.get("context", ""),
    )

    return (
        f"Worker lanzado exitosamente.\n"
        f"  ID: {agent_id}\n"
        f"  Tipo: {args.get('worker_type', 'general')}\n"
        f"  Descripcion: {args.get('description', task[:60])}\n\n"
        f"Usa TOOL:subagent({{\"action\": \"wait\", \"agent_id\": \"{agent_id}\"}}) "
        f"para esperar su resultado."
    )


async def _handle_spawn_parallel(manager: "SubAgentManager", args: Dict) -> str:
    """Spawn multiple workers in parallel."""
    tasks = args.get("tasks", [])
    if not tasks:
        return "Error: 'tasks' es requerido (lista de {{task, worker_type, description}})."

    agent_ids = await manager.spawn_parallel(tasks)

    lines = [f"Lanzados {len(agent_ids)} workers en paralelo:\n"]
    for i, aid in enumerate(agent_ids):
        t = tasks[i] if i < len(tasks) else {}
        lines.append(
            f"  {i+1}. {aid} [{t.get('worker_type', 'general')}] "
            f"— {t.get('description', t.get('task', '')[:40])}"
        )

    lines.append(
        f"\nUsa TOOL:subagent({{\"action\": \"wait_all\"}}) "
        f"para esperar todos los resultados."
    )

    return "\n".join(lines)


async def _handle_wait(manager: "SubAgentManager", args: Dict) -> str:
    """Wait for a specific worker."""
    agent_id = args.get("agent_id", "")
    if not agent_id:
        return "Error: 'agent_id' es requerido."

    timeout = float(args.get("timeout", 300))
    result = await manager.wait_for(agent_id, timeout=timeout)

    if result is None:
        if manager.is_running(agent_id):
            return f"Worker {agent_id} aun corriendo (timeout despues de {timeout}s)."
        return f"Worker {agent_id} no encontrado."

    return manager.format_notification(result)


async def _handle_wait_all(manager: "SubAgentManager", args: Dict) -> str:
    """Wait for all (or specified) workers."""
    agent_ids = args.get("agent_ids")
    timeout = float(args.get("timeout", 600))

    results = await manager.wait_all(agent_ids, timeout=timeout)
    return manager.format_all_results(results)


def _handle_kill(manager: "SubAgentManager", args: Dict) -> str:
    """Kill a running worker."""
    agent_id = args.get("agent_id", "")
    if agent_id == "all":
        count = manager.kill_all()
        return f"Matados {count} workers."

    if not agent_id:
        return "Error: 'agent_id' es requerido (o 'all' para matar todos)."

    if manager.kill(agent_id):
        return f"Worker {agent_id} detenido."
    return f"Worker {agent_id} no encontrado o ya terminó."


def _handle_status(manager: "SubAgentManager") -> str:
    """Get status of all workers."""
    status = manager.get_status()

    lines = [
        "# Estado de Workers\n",
        f"  Corriendo: {status['running']}",
        f"  Completados: {status['completed']}",
        f"  Fallidos: {status['failed']}",
        f"  Total lanzados: {status['total_spawned']}",
    ]

    if status['running_ids']:
        lines.append(f"\n  Workers activos: {', '.join(status['running_ids'])}")

    return "\n".join(lines)


def _handle_results(manager: "SubAgentManager", args: Dict) -> str:
    """Get results of completed workers."""
    agent_id = args.get("agent_id")

    if agent_id:
        result = manager.get_result(agent_id)
        if result:
            return manager.format_notification(result)
        return f"No hay resultado para worker {agent_id}."

    # Return all available results
    results = [r for r in manager._results.values()]
    if not results:
        return "No hay resultados disponibles aun."

    return manager.format_all_results(results)
