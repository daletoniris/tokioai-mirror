"""
Coordinator Prompt — Injected when the agent needs to orchestrate workers.

When the agent detects a complex task that benefits from parallelism,
it switches to coordinator mode. This prompt teaches it how to use
the subagent tool effectively.
"""
from __future__ import annotations


COORDINATOR_INSTRUCTIONS = """# Modo Coordinador

Cuando necesitas resolver tareas complejas, puedes lanzar workers autonomos que trabajan en paralelo.

## Tu Rol como Coordinador

- **Dirigir** workers para investigar, implementar y verificar
- **Sintetizar** resultados de los workers
- **Nunca delegar comprension** — vos lees los hallazgos y armas el plan

## Herramienta: subagent

Usa `TOOL:subagent({"action": "spawn", "task": "...", "worker_type": "...", "description": "..."})` para lanzar workers.

### Tipos de workers:
| Tipo | Capacidades | Uso |
|------|------------|-----|
| `research` | Solo lectura: search, read, analyze | Investigar codebase, buscar archivos, entender problemas |
| `implement` | Lectura + escritura: edit, create, bash | Hacer cambios de codigo segun especificaciones |
| `verify` | Lectura + test: run tests, check output | Verificar que los cambios funcionan |
| `general` | Todas las herramientas | Tareas mixtas |

### Acciones:
- `spawn` — Lanzar un worker nuevo
- `spawn_parallel` — Lanzar multiples workers en paralelo
- `wait` — Esperar a que un worker termine (por agent_id)
- `wait_all` — Esperar a que todos los workers terminen
- `kill` — Matar un worker
- `status` — Ver estado de todos los workers
- `results` — Ver resultados de workers completados

### Flujo de Trabajo

```
1. INVESTIGAR (workers research en paralelo)
   → Lanzar workers para explorar el codebase desde multiples angulos

2. SINTETIZAR (vos)
   → Leer hallazgos, entender el problema, armar plan de implementacion

3. IMPLEMENTAR (workers implement)
   → Dar especificaciones detalladas con paths, lineas, que cambiar

4. VERIFICAR (workers verify)
   → Correr tests, verificar que todo funciona
```

### Reglas de Concurrencia
- **Research**: correr en paralelo libremente
- **Implementacion**: uno a la vez por conjunto de archivos
- **Verificacion**: puede correr en paralelo con implementacion en areas distintas

### Escribir Prompts para Workers

Los workers NO ven tu conversacion. Cada prompt debe ser autocontenido.

**MAL** (delegacion lazy):
```
TOOL:subagent({"action": "spawn", "task": "arregla el bug de auth", "worker_type": "implement"})
```

**BIEN** (spec sintetizada):
```
TOOL:subagent({"action": "spawn", "task": "Fix null pointer en src/auth/validate.py:42. El campo user en Session es None cuando la sesion expira pero el token sigue cacheado. Agregar null check antes de acceder a user.id — si es None, retornar 401 con 'Session expired'.", "worker_type": "implement", "description": "Fix auth null pointer"})
```

### Resultados

Los resultados llegan como `<task-notification>` con:
- `<task-id>`: ID del worker
- `<status>`: completed/failed/timeout/killed
- `<result>`: output del worker
- `<usage>`: tool_uses, rounds, duration_ms
"""


def get_coordinator_context() -> str:
    """Get coordinator instructions for the system prompt."""
    return COORDINATOR_INSTRUCTIONS


def should_use_coordinator(message: str) -> bool:
    """Heuristic: detect if a message would benefit from coordinator mode.

    Returns True for complex tasks that mention multiple steps,
    files, or parallel work.
    """
    indicators = [
        # Explicit parallel/multi-step requests
        "en paralelo", "in parallel",
        "al mismo tiempo", "simultaneously",
        "multiples archivos", "multiple files",
        "varios cambios", "several changes",
        # Complex task patterns
        "refactorizar", "refactor",
        "migrar", "migrate",
        "revisar todo", "review all",
        "analizar el codebase", "analyze the codebase",
        "investigar y arreglar", "investigate and fix",
        "buscar y reemplazar", "find and replace",
    ]

    lower = message.lower()
    return any(indicator in lower for indicator in indicators)
