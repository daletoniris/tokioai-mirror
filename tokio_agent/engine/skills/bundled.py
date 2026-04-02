"""
Bundled Skills — Skills that ship with TokioAI.

Each skill is a /command that expands into a detailed prompt for the agent.
"""
from __future__ import annotations

import logging

from .registry import get_skill_registry

logger = logging.getLogger(__name__)


def register_bundled_skills() -> None:
    """Register all bundled skills."""
    registry = get_skill_registry()

    # ─── /status — Check all systems ───
    registry.register(
        name="status",
        description="Verificar el estado de todos los sistemas (GCP, Raspi, WAF, Entity)",
        aliases=["estado", "st"],
        get_prompt=lambda args: f"""# Status Check

Ejecuta una verificacion completa del estado de todos los sistemas de TokioAI.
{f'Enfoque especifico: {args}' if args else ''}

## Pasos:

1. **GCP Agent**: Verifica que los containers estan corriendo
   - Usa SSH al servidor GCP con docker ps

2. **Raspi Entity**: Verifica que la Entity esta viva
   - TOOL:raspi_vision({{"action": "status"}})

3. **WAF Dashboard**: Verifica el dashboard
   - Verifica el health endpoint del dashboard WAF

4. **Tailscale Mesh**: Verifica conectividad
   - TOOL:bash({{"command": "tailscale status --json | python3 -c 'import sys,json; d=json.load(sys.stdin); [print(f\"  {{k}}: {{v.get(\\\"Online\\\",False)}}\") for k,v in d.get(\"Peer\",{{}}).items()]'"}})

5. **Context Window**: Reporta uso del contexto actual

Reporta un resumen conciso del estado de cada sistema.""",
    )

    # ─── /compact — Force conversation compaction ───
    registry.register(
        name="compact",
        description="Forzar compactacion de la conversacion para liberar contexto",
        aliases=["compactar"],
        get_prompt=lambda args: """# Compactacion Manual

El usuario solicita compactar la conversacion manualmente.
Esto resumira los mensajes antiguos para liberar espacio en el contexto.

Ejecuta la compactacion y reporta:
- Cuantos mensajes habia antes
- Cuantos quedaron despues
- Porcentaje de contexto liberado""",
    )

    # ─── /deploy — Deploy to GCP ───
    registry.register(
        name="deploy",
        description="Desplegar cambios al GCP (sync de codigo + restart containers)",
        aliases=["desplegar"],
        get_prompt=lambda args: f"""# Deploy a GCP

Despliega los cambios mas recientes al servidor GCP.
{f'Archivos especificos: {args}' if args else 'Todos los archivos modificados.'}

## Pasos:

1. **Identificar cambios**: Revisa que archivos fueron modificados recientemente
2. **Sync a GCP**: Copia los archivos al servidor via SCP
   - Para archivos del agent: scp al servidor GCP bajo /opt/tokioai-v2/
   - Para archivos de la Raspi: scp a la Raspi bajo /home/mrmoz/tokio_raspi/
3. **Restart containers**: Si se modificaron archivos del agent
   - SSH al servidor GCP y ejecutar docker-compose restart tokio-agent
4. **Verificar**: Confirma que el servicio esta corriendo despues del restart

Reporta que se desplegó y que esta funcionando.""",
    )

    # ─── /simplify — Code review (inspired by Claude Code) ───
    registry.register(
        name="simplify",
        description="Revisar codigo cambiado: reuso, calidad y eficiencia",
        aliases=["revisar", "review"],
        get_prompt=lambda args: f"""# Simplify: Revision de Codigo

Revisa todos los archivos modificados buscando problemas de reuso, calidad y eficiencia.
{f'Enfoque adicional: {args}' if args else ''}

## Fase 1: Identificar Cambios

Corre `git diff` (o `git diff HEAD` si hay cambios staged) para ver que cambio.

## Fase 2: Revision

Para cada cambio, revisa:

### Reuso
- Busca utilidades existentes que podrian reemplazar codigo nuevo
- Identifica funciones que duplican funcionalidad existente
- Busca logica inline que podria usar un utilitario existente

### Calidad
- Estado redundante o duplicado
- Copy-paste con variaciones menores
- Abstracciones con leaks
- Comentarios innecesarios (que explican QUE en vez de POR QUE)

### Eficiencia
- Trabajo innecesario: computaciones redundantes, lecturas duplicadas
- Concurrencia perdida: operaciones independientes corriendo secuencialmente
- Falta de limpieza de recursos

## Fase 3: Corregir

Corrige cada problema directamente. Si es un falso positivo, notalo y continua.
Al terminar, resume brevemente que se corrigio.""",
    )

    # ─── /remember — Save something to memory ───
    registry.register(
        name="remember",
        description="Guardar algo en la memoria persistente",
        aliases=["recordar", "acordate"],
        get_prompt=lambda args: f"""# Guardar en Memoria

El usuario quiere que recuerdes: {args}

Guarda esta informacion en la memoria persistente del usuario usando TOOL:write_file
para persistirla en el workspace de memoria. Confirma que fue guardado.""",
    )

    # ─── /forget — Remove something from memory ───
    registry.register(
        name="forget",
        description="Eliminar algo de la memoria persistente",
        aliases=["olvidar", "olvida"],
        get_prompt=lambda args: f"""# Eliminar de Memoria

El usuario quiere que olvides: {args}

Busca y elimina la informacion relevante de la memoria persistente.
Confirma que fue eliminado.""",
    )

    # ─── /help — List all skills ───
    registry.register(
        name="help",
        description="Mostrar todos los comandos disponibles",
        aliases=["ayuda", "h"],
        get_prompt=lambda args: "Muestra la lista de todos los /comandos disponibles con sus descripciones.",
    )

    # ─── /context — Show context window usage ───
    registry.register(
        name="context",
        description="Mostrar uso del contexto y estadisticas de memoria",
        aliases=["ctx"],
        get_prompt=lambda args: """# Uso del Contexto

Reporta las estadisticas actuales del contexto:
- Tokens usados / tokens disponibles
- Porcentaje de uso
- Numero de mensajes en la sesion
- Compactaciones realizadas
- Memorias extraidas

Usa los datos del auto-compactor y auto-memory para generar el reporte.""",
    )

    logger.info(f"Registered {len(registry._skills)} bundled skills")
