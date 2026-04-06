"""
Error Learner — Learns from tool failures and adapts behavior.

Improvements over v1:
- Finite retry budget (max 3 retries per tool per error type)
- Error pattern database with known fixes
- No infinite loops — explicit bail-out after max attempts
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ErrorPattern:
    """A known error pattern with suggested fix."""
    pattern: str  # Substring to match in error message
    fix_hint: str  # Suggestion for the LLM
    auto_fix_cmd: Optional[str] = None  # Optional auto-fix command


# Known error patterns and their fixes
KNOWN_PATTERNS: List[ErrorPattern] = [
    ErrorPattern(
        "command not found",
        "El comando no está instalado. Intenta instalarlo con apt-get.",
        "apt-get update -qq && apt-get install -y -qq {tool}",
    ),
    ErrorPattern(
        "No such file or directory",
        "El archivo o directorio no existe. Verifica la ruta.",
    ),
    ErrorPattern(
        "Permission denied",
        "Sin permisos. Intenta con sudo o verifica los permisos del archivo.",
    ),
    ErrorPattern(
        "Connection refused",
        "Conexión rechazada. El servicio no está corriendo o el puerto es incorrecto.",
    ),
    ErrorPattern(
        "Name or service not known",
        "No se puede resolver el hostname. Verifica la conectividad de red.",
    ),
    ErrorPattern(
        "timeout",
        "La operación excedió el tiempo. Intenta con un timeout más largo o simplifica el comando.",
    ),
    ErrorPattern(
        "out of memory",
        "Sin memoria suficiente. Intenta con datos más pequeños o libera memoria.",
    ),
    ErrorPattern(
        "syntax error",
        "Error de sintaxis. Revisa el comando o código.",
    ),
    ErrorPattern(
        "ECONNREFUSED",
        "Servicio no accesible. Verifica que esté corriendo y el puerto sea correcto.",
    ),
    ErrorPattern(
        "ModuleNotFoundError",
        "Módulo Python no instalado. Instálalo con pip install.",
        "pip install {module}",
    ),
    ErrorPattern(
        "FileExistsError",
        "El archivo o directorio ya existe. Usa un nombre diferente o elimínalo primero.",
    ),
    ErrorPattern(
        "disk space",
        "Disco lleno. Libera espacio eliminando archivos temporales o logs antiguos.",
        "df -h && du -sh /tmp/* | sort -hr | head -10",
    ),
    ErrorPattern(
        "rate limit",
        "Rate limit alcanzado. Espera unos segundos antes de reintentar.",
    ),
    ErrorPattern(
        "authentication failed",
        "Autenticación fallida. Verifica credenciales, tokens o claves SSH.",
    ),
    ErrorPattern(
        "port already in use",
        "Puerto en uso. Encuentra el proceso con lsof y libéralo o usa otro puerto.",
        "lsof -i :{port}",
    ),
    ErrorPattern(
        "container not found",
        "Container Docker no encontrado. Verifica el nombre con 'docker ps -a'.",
    ),
]


class ErrorLearner:
    """Tracks errors and provides learning-based suggestions."""

    MAX_RETRIES_PER_ERROR = 3

    def __init__(self):
        # tool_name -> error_type -> retry_count
        self._retry_counts: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        # tool_name -> list of (error, suggestion) pairs
        self._learned: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

    def analyze_error(
        self,
        tool_name: str,
        error_msg: str,
    ) -> Optional[str]:
        """Analyze an error and return a suggestion.

        Returns None if max retries exceeded (should bail out).
        """
        error_type = self._classify_error(error_msg)

        # Check retry budget
        count = self._retry_counts[tool_name][error_type]
        if count >= self.MAX_RETRIES_PER_ERROR:
            logger.warning(
                f"🛑 Max retries ({self.MAX_RETRIES_PER_ERROR}) reached "
                f"for {tool_name}/{error_type}"
            )
            return None  # Signal to bail out

        self._retry_counts[tool_name][error_type] = count + 1

        # Find matching pattern
        for pattern in KNOWN_PATTERNS:
            if pattern.pattern.lower() in error_msg.lower():
                self._learned[tool_name].append((error_msg, pattern.fix_hint))
                return pattern.fix_hint

        # Generic suggestion
        return (
            f"Error en '{tool_name}': {error_msg[:200]}. "
            f"Intenta un enfoque diferente."
        )

    def should_retry(self, tool_name: str, error_msg: str) -> bool:
        """Check if we should retry this tool for this error."""
        error_type = self._classify_error(error_msg)
        return self._retry_counts[tool_name][error_type] < self.MAX_RETRIES_PER_ERROR

    def reset_tool(self, tool_name: str) -> None:
        """Reset retry counts for a tool (e.g., after success)."""
        self._retry_counts[tool_name].clear()

    def get_context_for_prompt(self) -> str:
        """Get learned errors as context for the LLM prompt."""
        if not self._learned:
            return ""

        lines = ["# Recent Error Learnings"]
        for tool, errors in self._learned.items():
            for error, fix in errors[-3:]:  # Last 3 per tool
                short_error = error[:100]
                lines.append(f"- {tool}: '{short_error}' → {fix}")
        return "\n".join(lines)

    def _classify_error(self, error_msg: str) -> str:
        """Classify an error into a type for deduplication."""
        lower = error_msg.lower()
        for pattern in KNOWN_PATTERNS:
            if pattern.pattern.lower() in lower:
                return pattern.pattern
        return "unknown"
