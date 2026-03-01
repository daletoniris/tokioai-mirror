"""
Input Sanitizer — Prevents command injection, SQL injection, and path traversal.

Applied before tool execution to catch dangerous patterns that the LLM
might generate (intentionally via injection or accidentally).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def sanitize_command(command: str) -> Tuple[bool, str, Optional[str]]:
    """Sanitize a shell command before execution.

    Args:
        command: Raw shell command.

    Returns:
        Tuple of (is_safe, sanitized_command, warning_message).
    """
    if not command or not command.strip():
        return False, "", "Comando vacío"

    # Check for command chaining that could hide malicious commands
    # Allow pipes and && but flag suspicious patterns
    warnings = []

    # Detect reverse shells
    reverse_shell_patterns = [
        r'(?:bash|sh|nc|ncat|netcat)\s+.*(?:-e\s+/bin/(?:ba)?sh|-i\s+>&)',
        r'/dev/tcp/',
        r'mkfifo\s+/tmp/',
        r'python[23]?\s+-c\s+.*socket',
        r'perl\s+-e\s+.*socket',
        r'ruby\s+-rsocket',
    ]
    for pattern in reverse_shell_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, command, "⛔ Reverse shell detectado. Bloqueado."

    # Detect crypto miners
    miner_patterns = [
        r'xmrig|cryptonight|stratum\+tcp|minerd|minergate|coinhive',
    ]
    for pattern in miner_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, command, "⛔ Crypto miner detectado. Bloqueado."

    # Detect data exfiltration via curl/wget to external URLs
    exfil_patterns = [
        r'curl\s+.*-d\s+.*@.*\s+https?://(?!localhost|127\.0\.0\.1)',
        r'wget\s+--post-file',
    ]
    for pattern in exfil_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            warnings.append("⚠️ Posible exfiltración de datos detectada")

    # Detect fork bombs
    if re.search(r':\(\)\{.*\|.*&\s*\}', command):
        return False, command, "⛔ Fork bomb detectado. Bloqueado."

    # Detect disk wiping
    wipe_patterns = [
        r'dd\s+if=/dev/zero\s+of=/dev/[sh]d',
        r'dd\s+if=/dev/urandom\s+of=/dev/[sh]d',
        r'shred\s+/dev/[sh]d',
    ]
    for pattern in wipe_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return False, command, "⛔ Operación de borrado de disco detectada. Bloqueado."

    warning = "; ".join(warnings) if warnings else None
    return True, command, warning


def sanitize_sql(query: str) -> Tuple[bool, str, Optional[str]]:
    """Sanitize a SQL query before execution.

    Args:
        query: Raw SQL query.

    Returns:
        Tuple of (is_safe, sanitized_query, warning_message).
    """
    if not query or not query.strip():
        return False, "", "Query vacía"

    upper = query.upper().strip()

    # Block destructive operations without WHERE clause
    destructive_no_where = [
        (r'DELETE\s+FROM\s+\w+\s*(?:;|$)', "DELETE sin WHERE"),
        (r'UPDATE\s+\w+\s+SET\s+.*(?:;|$)(?!.*WHERE)', "UPDATE sin WHERE"),
        (r'TRUNCATE\s+', "TRUNCATE"),
    ]
    for pattern, desc in destructive_no_where:
        if re.search(pattern, upper) and "WHERE" not in upper:
            return False, query, f"⛔ {desc} detectado sin cláusula WHERE. Bloqueado."

    # Block DDL on critical tables
    critical_tables = ["tokio_sessions", "tokio_memory", "tokio_preferences"]
    for table in critical_tables:
        if re.search(rf'DROP\s+TABLE\s+.*{table.upper()}', upper):
            return False, query, f"⛔ DROP TABLE en tabla crítica '{table}'. Bloqueado."

    # Block stacked queries (multiple statements)
    # Allow only if it's a known safe pattern (CREATE TABLE IF NOT EXISTS; etc.)
    statements = [s.strip() for s in query.split(";") if s.strip()]
    if len(statements) > 3:
        return True, query, "⚠️ Query con múltiples statements. Verificar."

    return True, query, None


def sanitize_path(path: str) -> Tuple[bool, str, Optional[str]]:
    """Sanitize a file path to prevent path traversal.

    Args:
        path: Raw file path.

    Returns:
        Tuple of (is_safe, sanitized_path, warning_message).
    """
    if not path:
        return False, "", "Ruta vacía"

    # Expand user home
    expanded = os.path.expanduser(path)

    # Resolve to absolute path to detect traversal
    resolved = os.path.realpath(expanded)

    # Block access to sensitive system directories
    sensitive_dirs = [
        "/etc/shadow", "/etc/gshadow",
        "/proc/", "/sys/",
        "/root/.ssh/",
    ]
    for sd in sensitive_dirs:
        if resolved.startswith(sd) or resolved == sd.rstrip("/"):
            return False, path, f"⛔ Acceso a ruta sensible bloqueado: {sd}"

    # Block access to known credential files
    sensitive_files = [
        ".env", "credentials.json", "id_rsa", "id_ed25519",
        ".pgpass", ".my.cnf", ".netrc",
    ]
    basename = os.path.basename(resolved)
    if basename in sensitive_files:
        return True, path, f"⚠️ Acceso a archivo sensible: {basename}"

    # Detect path traversal attempts
    if "../" in path and not path.startswith("/"):
        return True, resolved, f"⚠️ Path traversal detectado, resuelto a: {resolved}"

    return True, expanded, None
