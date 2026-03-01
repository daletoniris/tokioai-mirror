"""
Network tools — curl, wget wrappers and HTTP helpers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


async def curl(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str] = None,
    timeout: int = 60,
) -> str:
    """Execute an HTTP request using curl.

    Args:
        url: Target URL.
        method: HTTP method (GET, POST, PUT, DELETE, etc.).
        headers: Optional dict of HTTP headers.
        data: Optional request body.
        timeout: Request timeout in seconds.

    Returns:
        Response body or error message.
    """
    cmd_parts = ["curl", "-s", "-S", "-w", r"\n[HTTP %{http_code}]", "-X", method.upper()]

    if headers:
        for k, v in headers.items():
            cmd_parts.extend(["-H", f"{k}: {v}"])

    if data:
        cmd_parts.extend(["-d", data])

    cmd_parts.extend(["--max-time", str(timeout)])
    cmd_parts.append(url)

    # Build shell command with proper quoting
    import shlex
    shell_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

    try:
        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 10
        )

        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0 and not output:
            return f"curl error: {err}"

        result = output
        if err:
            result += f"\n[stderr]: {err}"
        return result

    except asyncio.TimeoutError:
        return f"⏱️ Timeout: curl excedió {timeout}s para {url}"
    except Exception as e:
        return f"Error en curl: {type(e).__name__}: {e}"


async def wget(url: str, output_path: Optional[str] = None) -> str:
    """Download a file using wget.

    Args:
        url: URL to download.
        output_path: Optional local path to save the file.

    Returns:
        Status message.
    """
    cmd = ["wget", "-q"]
    if output_path:
        cmd.extend(["-O", output_path])
    cmd.append(url)

    import shlex
    shell_cmd = " ".join(shlex.quote(p) for p in cmd)

    try:
        proc = await asyncio.create_subprocess_shell(
            shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )

        if proc.returncode == 0:
            if output_path:
                return f"✅ Descargado a {output_path}"
            return stdout.decode("utf-8", errors="replace") or "✅ Descarga completada"
        return f"wget error: {stderr.decode('utf-8', errors='replace')}"

    except asyncio.TimeoutError:
        return "⏱️ Timeout: wget excedió 120s."
    except Exception as e:
        return f"Error en wget: {type(e).__name__}: {e}"
