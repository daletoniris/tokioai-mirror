"""
Compact Prompts — Templates for conversation summarization.

Adapted from Claude Code's compaction system. The agent uses these prompts
to generate a detailed summary when the context window is getting full.
The summary replaces the conversation history, freeing tokens while
preserving all essential context.
"""
from __future__ import annotations


# ─── Full compaction prompt (summarize entire conversation) ───

COMPACT_PROMPT = """Tu tarea es crear un resumen detallado de la conversacion hasta ahora, prestando atencion a las solicitudes explicitas del usuario y tus acciones previas.
Este resumen debe capturar detalles tecnicos, patrones de codigo y decisiones arquitecturales esenciales para continuar el trabajo sin perder contexto.

Antes de dar tu resumen final, organiza tu analisis en un bloque <analysis> para asegurar que cubres todos los puntos necesarios. En tu analisis:

1. Analiza cronologicamente cada mensaje y seccion de la conversacion. Para cada seccion identifica:
   - Las solicitudes e intenciones explicitas del usuario
   - Tu enfoque para abordar las solicitudes
   - Decisiones clave, conceptos tecnicos y patrones de codigo
   - Detalles especificos: nombres de archivos, snippets de codigo, firmas de funciones, ediciones
   - Errores que encontraste y como los solucionaste
   - Feedback especifico del usuario, especialmente si te pidio hacer algo diferente
2. Verifica la precision tecnica y completitud.

Tu resumen debe incluir las siguientes secciones:

1. Solicitud Principal: Captura todas las solicitudes e intenciones explicitas del usuario en detalle.
2. Conceptos Tecnicos: Lista los conceptos, tecnologias y frameworks discutidos.
3. Archivos y Codigo: Enumera archivos examinados, modificados o creados. Incluye snippets de codigo donde sea relevante.
4. Errores y Soluciones: Lista errores encontrados y como se solucionaron.
5. Mensajes del Usuario: Lista TODOS los mensajes del usuario que no son resultados de tools.
6. Tareas Pendientes: Tareas pendientes que se pidieron explicitamente.
7. Trabajo Actual: Describe precisamente en que se estaba trabajando antes de este resumen.
8. Proximo Paso: El siguiente paso directamente alineado con la solicitud mas reciente del usuario.

Formato:

<analysis>
[Tu proceso de analisis]
</analysis>

<summary>
1. Solicitud Principal:
   [Descripcion detallada]

2. Conceptos Tecnicos:
   - [Concepto 1]
   - [Concepto 2]

3. Archivos y Codigo:
   - [Archivo 1]
     - [Resumen y snippets relevantes]

4. Errores y Soluciones:
   - [Error]: [Solucion]

5. Mensajes del Usuario:
   - [Mensaje 1]
   - [Mensaje 2]

6. Tareas Pendientes:
   - [Tarea 1]

7. Trabajo Actual:
   [Descripcion precisa]

8. Proximo Paso:
   [Siguiente paso]
</summary>

Proporciona tu resumen basado en la conversacion hasta ahora, siguiendo esta estructura con precision y minuciosidad."""


# ─── Partial compaction prompt (summarize old messages, keep recent) ───

PARTIAL_COMPACT_PROMPT = """Tu tarea es crear un resumen detallado de la porcion MAS ANTIGUA de la conversacion — los mensajes que preceden a los mensajes recientes que se conservaran intactos. Los mensajes recientes NO necesitan ser resumidos.

Analiza los mensajes antiguos cronologicamente en un bloque <analysis>, luego proporciona el resumen en un bloque <summary> con las mismas 8 secciones del formato estandar.

Enfocate en preservar contexto que los mensajes recientes necesitaran para ser entendidos."""


def get_compact_prompt(custom_instructions: str = "") -> str:
    """Get the full compaction prompt, optionally with custom instructions."""
    prompt = COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nInstrucciones adicionales:\n{custom_instructions}"
    return prompt


def get_partial_compact_prompt(custom_instructions: str = "") -> str:
    """Get the partial compaction prompt."""
    prompt = PARTIAL_COMPACT_PROMPT
    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nInstrucciones adicionales:\n{custom_instructions}"
    return prompt


def format_compact_summary(summary: str) -> str:
    """Format the compact summary by stripping the analysis scratchpad.

    The <analysis> block improves summary quality but has no informational
    value once the summary is written — strip it to save tokens.
    """
    import re

    formatted = summary

    # Strip analysis section
    formatted = re.sub(r'<analysis>[\s\S]*?</analysis>', '', formatted)

    # Extract and format summary section
    match = re.search(r'<summary>([\s\S]*?)</summary>', formatted)
    if match:
        content = match.group(1).strip()
        formatted = f"Resumen de la conversacion anterior:\n{content}"
    else:
        # No tags found — use as-is (the LLM didn't follow format)
        formatted = f"Resumen de la conversacion anterior:\n{formatted.strip()}"

    # Clean up excessive whitespace
    formatted = re.sub(r'\n\n+', '\n\n', formatted)
    return formatted.strip()


def build_continuation_message(summary: str, recent_preserved: bool = False) -> str:
    """Build the user message that replaces compacted history.

    This is what the agent sees after compaction — a summary of what
    happened before, so it can continue seamlessly.
    """
    formatted = format_compact_summary(summary)

    msg = (
        "Esta sesion continua de una conversacion anterior que fue compactada "
        "para liberar espacio de contexto. El resumen a continuacion cubre "
        "la porcion anterior de la conversacion.\n\n"
        f"{formatted}"
    )

    if recent_preserved:
        msg += "\n\nLos mensajes recientes se conservan intactos a continuacion."

    msg += (
        "\n\nContinua la conversacion donde quedo sin hacerle preguntas "
        "al usuario. Retoma directamente — no reconozcas el resumen, "
        "no recapitules lo que estaba pasando. Continua la tarea como "
        "si la interrupcion nunca hubiera pasado."
    )

    return msg
