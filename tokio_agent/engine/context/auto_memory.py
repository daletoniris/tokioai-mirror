"""
Auto-Memory Extraction — Background memory extraction after each response.

Inspired by Claude Code's extractMemories system. After each agent response,
a lightweight analysis runs to extract durable memories (user preferences,
project patterns, feedback, reference facts) and persist them.

Key design decisions:
- Runs AFTER the response is sent (non-blocking)
- Analyzes only recent messages (since last extraction)
- Writes to per-user memory via workspace
- Skips if the agent already wrote memories during the turn
- Max 1 extraction per response (no overlap)
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, List, Optional, TYPE_CHECKING

from .token_counter import estimate_tokens

if TYPE_CHECKING:
    from ..llm import BaseLLM
    from ..memory.workspace import Workspace

logger = logging.getLogger(__name__)


# How many recent messages to analyze per extraction
MESSAGES_TO_ANALYZE = 10

# Skip extraction if the response itself contained memory writes
MEMORY_WRITE_PATTERNS = [
    r'TOOL:write_file.*memory',
    r'TOOL:write_file.*\.tokio',
    r'recordar.*memoria',
    r'guardado en memoria',
]

EXTRACT_PROMPT = """Analiza los ultimos mensajes de la conversacion y extrae informacion que vale la pena recordar a largo plazo.

Tipos de memoria a extraer:
1. **Preferencias del usuario**: nombre, idioma, estilo de comunicacion, herramientas preferidas
2. **Feedback**: correcciones que el usuario hizo, cosas que pidio hacer diferente
3. **Proyecto**: patrones de codigo, arquitectura, archivos importantes, convenciones
4. **Referencia**: hechos, configuraciones, IPs, credenciales de servicios (sin secrets)

Para cada memoria encontrada, responde con el formato:
MEMORY:tipo|contenido

Ejemplo:
MEMORY:preferencia|El usuario prefiere respuestas en espanol
MEMORY:feedback|El usuario pidio no usar emojis en el codigo
MEMORY:proyecto|El proyecto usa PostgreSQL para sesiones, tabla tokio_sessions
MEMORY:referencia|El servidor usa PostgreSQL 15, puerto 5432

Reglas:
- Solo extrae informacion EXPLICITA de los mensajes, no inferencias
- No dupliques memorias que ya existen (revisa las existentes cuidadosamente)
- No extraigas informacion temporal o de una sola vez (ej: "la raspi esta apagada ahora")
- No guardes estados transitorios (ej: "el drone tiene 81% bateria", "FPS actual 16.6")
- Solo guarda HECHOS DURABLES (ej: "el drone se controla via proxy en puerto 5001")
- Si un hecho ya existe en las memorias existentes, aunque con palabras diferentes, NO lo dupliques
- Prioriza FEEDBACK (correcciones del usuario, cosas que pidio NO hacer) sobre datos tecnicos
- Si no hay nada NUEVO que recordar, responde: MEMORY:none
- Maximo 3 memorias por extraccion
"""


class AutoMemoryExtractor:
    """Extracts and persists durable memories from conversations."""

    def __init__(
        self,
        llm: "BaseLLM",
        workspace: "Workspace",
    ):
        self.llm = llm
        self.workspace = workspace
        self._cursor = 0  # Index of last processed message
        self._running = False
        self._total_extractions = 0
        self._total_memories_saved = 0

    async def extract_if_needed(
        self,
        messages: List[Dict[str, str]],
        session_id: str = "",
        last_response: str = "",
    ) -> int:
        """Run memory extraction on recent messages if appropriate.

        Args:
            messages: Full conversation history.
            session_id: Session ID for per-user isolation.
            last_response: The agent's last response (to check for memory writes).

        Returns:
            Number of memories extracted and saved.
        """
        if self._running:
            logger.debug("Memory extraction already running, skipping")
            return 0

        # Skip if the agent already wrote memories in this turn
        if self._response_has_memory_writes(last_response):
            logger.debug("Agent already wrote memories, skipping extraction")
            self._cursor = len(messages)
            return 0

        # Only analyze new messages since last extraction
        new_messages = messages[self._cursor:]
        if len(new_messages) < 2:
            # Need at least a user message + response
            return 0

        # Take the most recent messages to analyze
        to_analyze = new_messages[-MESSAGES_TO_ANALYZE:]

        self._running = True
        try:
            count = await self._run_extraction(to_analyze, session_id)
            self._cursor = len(messages)
            self._total_extractions += 1
            return count
        except Exception as e:
            logger.error(f"Memory extraction failed: {e}")
            return 0
        finally:
            self._running = False

    async def _run_extraction(
        self,
        messages: List[Dict[str, str]],
        session_id: str,
    ) -> int:
        """Run the actual extraction using the LLM."""
        # Build conversation context for the extraction agent
        conversation = []
        for msg in messages:
            conversation.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        # Get existing memories to avoid duplicates
        user_id = ""
        if session_id and session_id.startswith("telegram-"):
            user_id = session_id.replace("telegram-", "")

        existing = self.workspace.get_user_memory(user_id) if user_id else ""
        if not existing:
            existing = self.workspace.get_memory() or ""

        extract_prompt = EXTRACT_PROMPT
        if existing:
            extract_prompt += (
                f"\n\nMemorias existentes (NO duplicar):\n{existing[:2000]}"
            )

        try:
            response = await self.llm.generate(
                system_prompt="Eres un agente de extraccion de memorias. Analiza la conversacion y extrae datos durables.",
                user_prompt=extract_prompt,
                conversation=conversation,
                max_tokens=1024,
                temperature=0.1,
            )
        except Exception as e:
            logger.error(f"LLM call for memory extraction failed: {e}")
            return 0

        # Parse MEMORY: lines from response
        memories = self._parse_memories(response.text)

        if not memories:
            return 0

        # Save memories (with dedup check)
        saved = 0
        existing_text = existing.lower() if existing else ""
        for mem_type, content in memories:
            # Skip if substantially similar content already exists
            if self._is_duplicate(content, existing_text):
                logger.debug(f"Skipping duplicate memory: {content[:60]}")
                continue
            try:
                if user_id:
                    self.workspace.add_memory(
                        f"[{mem_type}] {content}",
                        user_id=user_id,
                    )
                else:
                    self.workspace.add_memory(f"[{mem_type}] {content}")
                saved += 1
                # Add to existing_text so subsequent entries in this batch
                # don't duplicate each other
                existing_text += " " + content.lower()
            except Exception as e:
                logger.error(f"Failed to save memory: {e}")

        if saved:
            self._total_memories_saved += saved
            logger.info(
                f"Memory extraction: saved {saved} memories "
                f"(total: {self._total_memories_saved})"
            )

        return saved

    @staticmethod
    def _is_duplicate(new_content: str, existing_text: str) -> bool:
        """Check if a memory entry is already covered by existing memories."""
        new_lower = new_content.lower().strip()
        if len(new_lower) < 10:
            return False

        # Extract key words (nouns, numbers, names)
        import re
        stopwords = {'el', 'la', 'los', 'las', 'un', 'una', 'de', 'del', 'en', 'que',
                     'con', 'por', 'para', 'se', 'no', 'es', 'y', 'o', 'a', 'al',
                     'tiene', 'usa', 'está', 'fue', 'son', 'puede', 'como', 'sin',
                     'más', 'ha', 'si', 'todo', 'han', 'hay', 'ser', 'solo', 'the',
                     'is', 'and', 'or', 'to', 'in', 'of', 'for', 'on', 'at', 'with'}
        words = re.findall(r'\w+', new_lower)
        key_words = [w for w in words if w not in stopwords and len(w) > 2]

        if not key_words:
            return False

        # If 70%+ of key words appear in existing text, it's a duplicate
        matches = sum(1 for w in key_words if w in existing_text)
        ratio = matches / len(key_words)
        return ratio > 0.70

    def _parse_memories(self, text: str) -> List[tuple]:
        """Parse MEMORY:type|content lines from LLM response."""
        memories = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line.startswith("MEMORY:"):
                rest = line[7:]
                if rest == "none":
                    return []
                parts = rest.split("|", 1)
                if len(parts) == 2:
                    mem_type = parts[0].strip()
                    content = parts[1].strip()
                    if content and len(content) > 5:
                        memories.append((mem_type, content))

        return memories[:3]  # Max 3 per extraction (less = less duplicates)

    def _response_has_memory_writes(self, response: str) -> bool:
        """Check if the agent's response already contains memory writes."""
        if not response:
            return False
        for pattern in MEMORY_WRITE_PATTERNS:
            if re.search(pattern, response, re.IGNORECASE):
                return True
        return False

    def get_stats(self) -> Dict:
        """Get extraction statistics."""
        return {
            "total_extractions": self._total_extractions,
            "total_memories_saved": self._total_memories_saved,
            "cursor": self._cursor,
        }
