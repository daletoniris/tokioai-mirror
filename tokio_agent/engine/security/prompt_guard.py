"""
Prompt Guard — Lightweight WAF for LLM prompts.

Detects and blocks prompt injection attacks before they reach the LLM.
Three severity levels:
  - BLOCK: Immediately reject the input
  - WARN: Allow but flag for review
  - CLEAN: Sanitize and pass through

Patterns based on known prompt injection techniques:
  - Role override ("ignore previous instructions")
  - System prompt extraction ("repeat your system prompt")
  - Delimiter injection (```system, [INST], etc.)
  - Encoding attacks (base64-encoded instructions)
  - Tool abuse ("call TOOL: with rm -rf /")
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class Severity(Enum):
    BLOCK = "block"
    WARN = "warn"
    CLEAN = "clean"


@dataclass
class ThreatPattern:
    """A pattern that indicates a potential prompt injection."""
    name: str
    pattern: str  # regex pattern
    severity: Severity
    description: str


# ── Known injection patterns ──
THREAT_PATTERNS: List[ThreatPattern] = [
    # Role override attacks
    ThreatPattern(
        "role_override",
        r"(?i)(ignore|forget|disregard|override)\s+(all\s+)?(previous|prior|above|earlier|your)\s+(instructions?|rules?|prompt|context|guidelines)",
        Severity.BLOCK,
        "Intento de sobreescribir instrucciones del sistema",
    ),
    ThreatPattern(
        "new_instructions",
        r"(?i)(new\s+instructions?|from\s+now\s+on|starting\s+now|henceforth)\s*[:.]?\s*(you\s+are|you\s+must|always|never)",
        Severity.BLOCK,
        "Intento de inyectar nuevas instrucciones",
    ),
    ThreatPattern(
        "pretend_role",
        r"(?i)(pretend|act\s+as\s+if|imagine|roleplay|you\s+are\s+now)\s+(you\s+are\s+)?(a\s+)?(different|new|evil|unrestricted|jailbroken|DAN)",
        Severity.BLOCK,
        "Intento de cambiar la identidad del agente",
    ),

    # System prompt extraction
    ThreatPattern(
        "prompt_extraction",
        r"(?i)(show|reveal|display|print|output|repeat|tell\s+me|give\s+me)\s+(me\s+)?(your\s+)?(full\s+|entire\s+|complete\s+)?(system\s+prompt|initial\s+prompt|instructions|system\s+message|hidden\s+prompt)",
        Severity.BLOCK,
        "Intento de extraer el system prompt",
    ),
    ThreatPattern(
        "prompt_leak",
        r"(?i)what\s+(are|were)\s+your\s+(original|initial|system|hidden)\s+(instructions?|prompt|rules)",
        Severity.WARN,
        "Posible intento de extraer instrucciones",
    ),

    # Delimiter injection
    ThreatPattern(
        "delimiter_injection",
        r"(?i)(\[INST\]|\[/INST\]|<<SYS>>|<\|system\|>|<\|user\|>|<\|assistant\|>|```system|<\|im_start\|>)",
        Severity.BLOCK,
        "Inyección de delimitadores de prompt",
    ),

    # Dangerous tool abuse via prompt
    ThreatPattern(
        "tool_injection",
        r'TOOL:\w+\(\{[^}]*(?:rm\s+-rf|mkfs|dd\s+if=|format\s+c|>\s*/dev/sd|chmod\s+777\s+/|wget\s+.*\|\s*(?:bash|sh))',
        Severity.BLOCK,
        "Intento de inyectar comandos peligrosos via tool",
    ),

    # Social engineering
    ThreatPattern(
        "developer_mode",
        r"(?i)(developer\s+mode|debug\s+mode|admin\s+mode|god\s+mode|sudo\s+mode|maintenance\s+mode)\s*(enabled|activated|on)",
        Severity.BLOCK,
        "Intento de activar modo privilegiado ficticio",
    ),

    # Encoding attacks
    ThreatPattern(
        "base64_injection",
        r"(?i)(decode|execute|run|eval)\s+(this\s+)?base64\s*[:=]?\s*[A-Za-z0-9+/=]{20,}",
        Severity.WARN,
        "Posible ataque con payload codificado en base64",
    ),

    # Data exfiltration
    ThreatPattern(
        "exfiltration",
        r"(?i)(send|post|upload|exfiltrate|transmit)\s+(all\s+)?(data|files?|credentials?|keys?|passwords?|secrets?)\s+(to|at|via)\s+(https?://|ftp://)",
        Severity.BLOCK,
        "Intento de exfiltración de datos",
    ),
]


@dataclass
class GuardResult:
    """Result of prompt guard analysis."""
    is_safe: bool
    threats: List[Tuple[str, Severity, str]]  # (name, severity, description)
    sanitized_input: str
    blocked: bool


class PromptGuard:
    """Analyzes user input for prompt injection attacks."""

    def __init__(self, strict_mode: bool = True):
        """
        Args:
            strict_mode: If True, WARN-level threats are also blocked.
        """
        self.strict_mode = strict_mode
        self._compiled = [
            (tp, re.compile(tp.pattern)) for tp in THREAT_PATTERNS
        ]
        self._stats = {"checked": 0, "blocked": 0, "warned": 0}

    def check(self, user_input: str) -> GuardResult:
        """Check user input for prompt injection.

        Args:
            user_input: Raw user input string.

        Returns:
            GuardResult with safety assessment.
        """
        self._stats["checked"] += 1
        threats: List[Tuple[str, Severity, str]] = []

        for tp, compiled_re in self._compiled:
            if compiled_re.search(user_input):
                threats.append((tp.name, tp.severity, tp.description))

        # Also check for hidden unicode/zero-width chars
        if self._has_hidden_chars(user_input):
            threats.append((
                "hidden_chars",
                Severity.WARN,
                "Input contiene caracteres ocultos/zero-width",
            ))

        # Determine if we should block
        has_block = any(s == Severity.BLOCK for _, s, _ in threats)
        has_warn = any(s == Severity.WARN for _, s, _ in threats)

        blocked = has_block or (self.strict_mode and has_warn)

        if blocked:
            self._stats["blocked"] += 1
            for name, sev, desc in threats:
                logger.warning(f"🛡️ Prompt Guard [{sev.value}]: {name} — {desc}")

        if has_warn and not blocked:
            self._stats["warned"] += 1

        # Sanitize: remove known injection delimiters
        sanitized = self._sanitize(user_input)

        return GuardResult(
            is_safe=not blocked,
            threats=threats,
            sanitized_input=sanitized,
            blocked=blocked,
        )

    def get_stats(self) -> dict:
        return dict(self._stats)

    @staticmethod
    def _has_hidden_chars(text: str) -> bool:
        """Check for zero-width and other invisible unicode characters."""
        hidden = {
            '\u200b',  # zero-width space
            '\u200c',  # zero-width non-joiner
            '\u200d',  # zero-width joiner
            '\u2060',  # word joiner
            '\ufeff',  # zero-width no-break space (BOM)
            '\u00ad',  # soft hyphen
            '\u200e',  # left-to-right mark
            '\u200f',  # right-to-left mark
        }
        return any(c in hidden for c in text)

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove known injection delimiters and hidden characters."""
        # Remove common delimiters
        sanitized = re.sub(
            r'(\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>|<\|system\|>|<\|user\|>|<\|assistant\|>|<\|im_start\|>|<\|im_end\|>)',
            '',
            text,
        )
        # Remove zero-width characters
        for c in ['\u200b', '\u200c', '\u200d', '\u2060', '\ufeff', '\u00ad']:
            sanitized = sanitized.replace(c, '')
        return sanitized
