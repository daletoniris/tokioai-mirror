"""
System tools — bash, python, file operations.

These are the core tools that make Tokio able to interact with the OS.
All execution is async to avoid blocking the event loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Commands considered dangerous — require explicit --force flag
DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "format c:",
    "dd if=/dev/zero",
    "mkfs",
    "> /dev/sda",
]


async def bash(command: str) -> str:
    """Execute a bash command asynchronously.

    Supports: curl, wget, git, ssh, any shell command.
    Auto-installs missing tools on Debian/Ubuntu systems.

    Args:
        command: The shell command to execute.

    Returns:
        Combined stdout + stderr output.
    """
    if not command or not command.strip():
        return "Error: Comando vacío"

    # Safety check for dangerous commands
    cmd_lower = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern in cmd_lower and "--force" not in cmd_lower:
            return (
                f"⚠️ Comando peligroso detectado: '{pattern}'. "
                f"Si realmente necesitas ejecutarlo, agrega --force al final."
            )

    # Adaptive timeout
    timeout = _estimate_timeout(command)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        output = stdout.decode("utf-8", errors="replace")
        err_output = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # Check if a tool is missing and try to install it
            if _is_missing_tool_error(err_output):
                tool_name = _extract_missing_tool(err_output)
                if tool_name:
                    install_result = await _try_install(tool_name)
                    if install_result:
                        # Retry the original command
                        proc2 = await asyncio.create_subprocess_shell(
                            command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            stdin=asyncio.subprocess.DEVNULL,
                        )
                        stdout2, stderr2 = await asyncio.wait_for(
                            proc2.communicate(), timeout=timeout
                        )
                        if proc2.returncode == 0:
                            return (
                                f"🔧 Se instaló '{tool_name}' automáticamente.\n\n"
                                + stdout2.decode("utf-8", errors="replace")
                            )

            combined = output
            if err_output:
                combined += f"\n[stderr]: {err_output}"
            combined += f"\n[exit code: {proc.returncode}]"
            return combined

        result = output
        if err_output:
            result += f"\n[stderr]: {err_output}"
        return result or "(comando ejecutado sin salida)"

    except asyncio.TimeoutError:
        # Kill the subprocess on timeout
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"Timeout after {timeout}s. Divide la tarea en pasos mas pequenos."
    except asyncio.CancelledError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return "Cancelado por el usuario."
    except Exception as e:
        return f"Error ejecutando bash: {type(e).__name__}: {e}"


async def python_exec(code: str) -> str:
    """Execute Python code in a temporary file.

    Args:
        code: Python source code to execute.

    Returns:
        stdout + stderr output.
    """
    if not code or not code.strip():
        return "Error: Código vacío"

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(code)
            tmp_path = f.name

        proc = await asyncio.create_subprocess_exec(
            "python3", tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )

        os.unlink(tmp_path)

        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return f"Python error (exit {proc.returncode}):\n{err}\n{output}"

        result = output
        if err:
            result += f"\n[stderr]: {err}"
        return result or "(ejecutado sin salida)"

    except asyncio.TimeoutError:
        return "⏱️ Timeout: el código Python excedió 120s."
    except Exception as e:
        return f"Error ejecutando Python: {type(e).__name__}: {e}"


async def read_file(path: str, lines: Optional[int] = None) -> str:
    """Read a file from the filesystem.

    Args:
        path: Absolute or relative path to the file.
        lines: Optional max number of lines to read (from the start).

    Returns:
        File contents as string.
    """
    try:
        expanded = os.path.expanduser(path)
        if not os.path.exists(expanded):
            return f"Error: Archivo no encontrado: {path}"
        if os.path.isdir(expanded):
            entries = os.listdir(expanded)[:50]
            return f"Directorio ({len(entries)} entradas):\n" + "\n".join(entries)

        with open(expanded, "r", errors="replace") as f:
            if lines:
                content = "".join(f.readline() for _ in range(lines))
            else:
                content = f.read()

        # Limit output to 50KB to avoid flooding
        if len(content) > 50_000:
            content = content[:50_000] + f"\n\n... (truncado, {len(content)} bytes total)"
        return content

    except Exception as e:
        return f"Error leyendo archivo: {type(e).__name__}: {e}"


async def write_file(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: Absolute or relative path.
        content: Content to write.

    Returns:
        Confirmation message.
    """
    try:
        expanded = os.path.expanduser(path)
        os.makedirs(os.path.dirname(expanded) or ".", exist_ok=True)
        with open(expanded, "w") as f:
            f.write(content)
        return f"✅ Archivo escrito: {path} ({len(content)} bytes)"
    except Exception as e:
        return f"Error escribiendo archivo: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_timeout(command: str) -> int:
    """Estimate a reasonable timeout for a shell command."""
    simple = ["echo", "ls", "cat", "pwd", "whoami", "date", "uptime", "uname", "id"]
    medium = ["curl", "wget", "grep", "find", "ps", "df", "free", "du"]

    first_word = command.strip().split()[0] if command.strip() else ""
    if first_word in simple:
        return 15
    if first_word in medium or "|" in command:
        return 60
    # Long-running: compilation, docker, apt, etc.
    return 300


def _is_missing_tool_error(stderr: str) -> bool:
    patterns = ["command not found", "not found", "no such file", "not installed"]
    lower = stderr.lower()
    return any(p in lower for p in patterns)


def _extract_missing_tool(stderr: str) -> Optional[str]:
    # "bash: jq: command not found" → "jq"
    m = re.search(r"bash:\s*(\w+):\s*command not found", stderr, re.IGNORECASE)
    if m:
        return m.group(1)
    # "/usr/bin/env: 'xxx': No such file or directory"
    m = re.search(r"No such file or directory.*?'(\w+)'", stderr)
    if m:
        return m.group(1)
    return None


TOOL_PACKAGES = {
    "crontab": "cron", "curl": "curl", "wget": "wget",
    "ssh": "openssh-client", "git": "git", "jq": "jq",
    "vim": "vim", "nano": "nano", "htop": "htop",
    "netstat": "net-tools", "ifconfig": "net-tools",
    "nmap": "nmap", "zip": "zip", "unzip": "unzip",
    "tar": "tar", "rsync": "rsync", "tmux": "tmux",
}


async def _try_install(tool_name: str) -> bool:
    """Try to auto-install a missing tool."""
    pkg = TOOL_PACKAGES.get(tool_name, tool_name)
    try:
        proc = await asyncio.create_subprocess_shell(
            f"apt-get update -qq && apt-get install -y -qq {pkg}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=120)
        return proc.returncode == 0
    except Exception:
        return False
