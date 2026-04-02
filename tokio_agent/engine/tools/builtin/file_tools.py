"""
Structured File Tools — Claude Code-quality file operations for TokioAI.

Replaces the basic read_file/write_file with:
- read_file: Line-numbered output, offset/limit support, directory listing
- write_file: Creates dirs, validates paths, size info
- edit_file: Exact string replacement (like Claude Code's Edit tool)
- search_code: Regex search across files (like Claude Code's Grep tool)
- find_files: Glob pattern matching (like Claude Code's Glob tool)
- list_files: Directory listing with metadata

All operations are async and have robust error handling.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Limits
MAX_READ_LINES = 2000
MAX_FILE_SIZE = 512_000  # 512KB max read
MAX_SEARCH_RESULTS = 100
MAX_GLOB_RESULTS = 200


# ───────────────────────────────────────────────────────
# read_file — Line-numbered output with offset/limit
# ───────────────────────────────────────────────────────

async def read_file(
    path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Read a file with line numbers (cat -n style).

    Args:
        path: Absolute or relative path to the file.
        offset: Start reading from this line number (1-based). Default: 1.
        limit: Maximum number of lines to read. Default: 2000.

    Returns:
        Line-numbered file contents.
    """
    try:
        expanded = os.path.expanduser(path)
        expanded = os.path.abspath(expanded)

        if not os.path.exists(expanded):
            return f"Error: Archivo no encontrado: {path}"

        if os.path.isdir(expanded):
            return await list_files(path=expanded)

        # Check file size
        file_size = os.path.getsize(expanded)
        if file_size > MAX_FILE_SIZE:
            return (
                f"Archivo demasiado grande ({file_size:,} bytes). "
                f"Usa offset/limit para leer por partes. "
                f"Ejemplo: read_file(path='{path}', offset=1, limit=100)"
            )

        with open(expanded, "r", errors="replace") as f:
            all_lines = f.readlines()

        total_lines = len(all_lines)
        # Ensure offset/limit are ints (API may send strings)
        _offset = int(offset) if offset is not None else 1
        _limit = int(limit) if limit is not None else MAX_READ_LINES
        start = _offset - 1  # Convert to 0-based
        end = start + _limit

        # Clamp
        start = max(0, min(start, total_lines))
        end = min(end, total_lines)

        selected = all_lines[start:end]

        # Format with line numbers (cat -n style)
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            # Truncate very long lines
            if len(line) > 2000:
                line = line[:2000] + "...\n"
            numbered.append(f"{i:>6}\t{line.rstrip()}")

        result = "\n".join(numbered)

        # Add metadata header
        header = f"[{path} | {total_lines} lines | {file_size:,} bytes"
        if start > 0 or end < total_lines:
            header += f" | showing lines {start+1}-{end}"
        header += "]"

        return f"{header}\n{result}"

    except PermissionError:
        return f"Error: Sin permisos para leer: {path}"
    except Exception as e:
        return f"Error leyendo archivo: {type(e).__name__}: {e}"


# ───────────────────────────────────────────────────────
# write_file — Create/overwrite with validation
# ───────────────────────────────────────────────────────

async def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed.

    Args:
        path: Absolute or relative path.
        content: Content to write.

    Returns:
        Confirmation with file size.
    """
    try:
        expanded = os.path.expanduser(path)
        expanded = os.path.abspath(expanded)

        # Create parent directories
        parent = os.path.dirname(expanded)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Check if file exists (for the confirmation message)
        existed = os.path.exists(expanded)

        with open(expanded, "w") as f:
            f.write(content)

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        action = "sobrescrito" if existed else "creado"

        return (
            f"Archivo {action}: {path}\n"
            f"  {len(content):,} bytes, {line_count} lineas"
        )
    except Exception as e:
        return f"Error escribiendo archivo: {type(e).__name__}: {e}"


# ───────────────────────────────────────────────────────
# edit_file — Exact string replacement (Claude Code Edit)
# ───────────────────────────────────────────────────────

async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: Optional[bool] = None,
) -> str:
    """Edit a file by replacing an exact string match.

    Like Claude Code's Edit tool — performs exact string replacement.
    The old_string must be unique in the file (unless replace_all=True).

    Args:
        path: Path to the file to edit.
        old_string: The exact text to find and replace.
        new_string: The replacement text.
        replace_all: If True, replace ALL occurrences. Default: False.

    Returns:
        Confirmation with diff preview.
    """
    try:
        expanded = os.path.expanduser(path)
        expanded = os.path.abspath(expanded)

        if not os.path.exists(expanded):
            return f"Error: Archivo no encontrado: {path}"

        with open(expanded, "r", errors="replace") as f:
            content = f.read()

        if old_string == new_string:
            return "Error: old_string y new_string son identicos. No hay cambios."

        # Count occurrences
        count = content.count(old_string)

        if count == 0:
            # Try to help find the right string
            lines = content.splitlines()
            # Search for partial match
            search_term = old_string.strip().splitlines()[0].strip() if old_string.strip() else ""
            matches = []
            if search_term and len(search_term) > 5:
                for i, line in enumerate(lines, 1):
                    if search_term in line:
                        matches.append(f"  linea {i}: {line.strip()[:100]}")
                        if len(matches) >= 3:
                            break

            msg = f"Error: old_string no encontrado en {path}."
            if matches:
                msg += f"\n\nLineas similares encontradas:\n" + "\n".join(matches)
            msg += (
                "\n\nAsegurate de que old_string coincida EXACTAMENTE con el contenido "
                "del archivo, incluyendo indentacion (tabs/espacios)."
            )
            return msg

        if count > 1 and not replace_all:
            # Show where the matches are
            lines = content.splitlines()
            match_lines = []
            for i, line in enumerate(lines, 1):
                if old_string.splitlines()[0] in line:
                    match_lines.append(f"  linea {i}: {line.strip()[:100]}")

            return (
                f"Error: old_string aparece {count} veces en {path}. "
                f"Agrega mas contexto para hacerlo unico, o usa replace_all=true.\n\n"
                f"Ocurrencias:\n" + "\n".join(match_lines[:5])
            )

        # Perform replacement
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        # Write the modified content
        with open(expanded, "w") as f:
            f.write(new_content)

        # Generate a mini diff preview
        old_preview = old_string[:200].replace("\n", "\\n")
        new_preview = new_string[:200].replace("\n", "\\n")
        replacements = count if replace_all else 1

        # Find the line number of the first replacement
        pre_match = content[:content.index(old_string)]
        line_num = pre_match.count("\n") + 1

        return (
            f"Editado: {path} (linea {line_num})\n"
            f"  Reemplazos: {replacements}\n"
            f"  - {old_preview}\n"
            f"  + {new_preview}"
        )

    except Exception as e:
        return f"Error editando archivo: {type(e).__name__}: {e}"


# ───────────────────────────────────────────────────────
# search_code — Regex search across files (Claude Code Grep)
# ───────────────────────────────────────────────────────

async def search_code(
    pattern: str,
    path: Optional[str] = None,
    include: Optional[str] = None,
    context_lines: Optional[int] = None,
    max_results: Optional[int] = None,
) -> str:
    """Search for a regex pattern across files.

    Like Claude Code's Grep tool — uses ripgrep (rg) if available,
    falls back to Python regex search.

    Args:
        pattern: Regex pattern to search for.
        path: Directory or file to search in. Default: current directory.
        include: Glob pattern to filter files (e.g., "*.py", "*.ts").
        context_lines: Lines of context around matches. Default: 0.
        max_results: Maximum number of matches. Default: 100.

    Returns:
        Matching lines with file paths and line numbers.
    """
    search_path = os.path.expanduser(path or ".")
    search_path = os.path.abspath(search_path)
    ctx = context_lines or 0
    max_res = max_results or MAX_SEARCH_RESULTS

    # Try ripgrep first (much faster)
    rg_result = await _search_with_rg(pattern, search_path, include, ctx, max_res)
    if rg_result is not None:
        return rg_result

    # Fallback to Python regex
    return await _search_with_python(pattern, search_path, include, ctx, max_res)


async def _search_with_rg(
    pattern: str, path: str, include: Optional[str], ctx: int, max_res: int
) -> Optional[str]:
    """Try searching with ripgrep."""
    try:
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if ctx > 0:
            cmd.extend([f"-C{ctx}"])
        if include:
            cmd.extend([f"--glob={include}"])
        cmd.extend([f"--max-count={max_res}", pattern, path])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 2:
            # rg error (bad pattern, etc)
            return f"Error en patron regex: {stderr.decode().strip()}"

        output = stdout.decode("utf-8", errors="replace")
        if not output.strip():
            return f"No se encontraron coincidencias para '{pattern}' en {path}"

        lines = output.strip().splitlines()
        if len(lines) > max_res:
            lines = lines[:max_res]
            output = "\n".join(lines) + f"\n\n... ({len(lines)} resultados mostrados, puede haber mas)"

        return f"[Busqueda: '{pattern}' en {path}]\n\n{output.strip()}"

    except FileNotFoundError:
        # rg not installed
        return None
    except asyncio.TimeoutError:
        return "Timeout en la busqueda. Intenta con un path mas especifico."
    except Exception:
        return None


async def _search_with_python(
    pattern: str, path: str, include: Optional[str], ctx: int, max_res: int
) -> str:
    """Fallback search using Python regex."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error en patron regex: {e}"

    results = []
    files_searched = 0

    for root, dirs, files in os.walk(path):
        # Skip hidden dirs and common noise
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
            "node_modules", "__pycache__", ".git", "venv", ".venv"
        )]

        for fname in files:
            if include and not fnmatch.fnmatch(fname, include):
                continue

            fpath = os.path.join(root, fname)

            # Skip binary files
            try:
                if os.path.getsize(fpath) > 1_000_000:
                    continue
            except OSError:
                continue

            try:
                with open(fpath, "r", errors="replace") as f:
                    lines = f.readlines()
            except (PermissionError, OSError):
                continue

            files_searched += 1

            for i, line in enumerate(lines):
                if regex.search(line):
                    rel_path = os.path.relpath(fpath, path)
                    result_lines = [f"{rel_path}:{i+1}:{line.rstrip()}"]

                    # Add context
                    if ctx > 0:
                        for ci in range(max(0, i - ctx), i):
                            result_lines.insert(-1, f"{rel_path}:{ci+1}: {lines[ci].rstrip()}")
                        for ci in range(i + 1, min(len(lines), i + ctx + 1)):
                            result_lines.append(f"{rel_path}:{ci+1}: {lines[ci].rstrip()}")

                    results.extend(result_lines)
                    if len(results) >= max_res:
                        break

            if len(results) >= max_res:
                break

    if not results:
        return f"No se encontraron coincidencias para '{pattern}' en {path} ({files_searched} archivos buscados)"

    output = "\n".join(results[:max_res])
    return f"[Busqueda: '{pattern}' en {path} ({files_searched} archivos)]\n\n{output}"


# ───────────────────────────────────────────────────────
# find_files — Glob pattern matching (Claude Code Glob)
# ───────────────────────────────────────────────────────

async def find_files(
    pattern: str,
    path: Optional[str] = None,
    max_results: Optional[int] = None,
) -> str:
    """Find files matching a glob pattern.

    Like Claude Code's Glob tool — fast file pattern matching.

    Args:
        pattern: Glob pattern (e.g., "**/*.py", "src/**/*.ts").
        path: Base directory to search. Default: current directory.
        max_results: Maximum results. Default: 200.

    Returns:
        List of matching file paths sorted by modification time.
    """
    base_path = os.path.expanduser(path or ".")
    base_path = os.path.abspath(base_path)
    max_res = max_results or MAX_GLOB_RESULTS

    if not os.path.exists(base_path):
        return f"Error: Directorio no encontrado: {path}"

    try:
        p = Path(base_path)
        matches = []

        for match in p.glob(pattern):
            if match.is_file():
                try:
                    mtime = match.stat().st_mtime
                    size = match.stat().st_size
                    rel = match.relative_to(base_path)
                    matches.append((mtime, size, str(rel)))
                except OSError:
                    continue

            if len(matches) >= max_res * 2:  # Collect more for sorting
                break

        # Sort by modification time (newest first)
        matches.sort(key=lambda x: x[0], reverse=True)
        matches = matches[:max_res]

        if not matches:
            return f"No se encontraron archivos con patron '{pattern}' en {base_path}"

        lines = [f"[{len(matches)} archivos encontrados con '{pattern}' en {base_path}]\n"]
        for mtime, size, rel_path in matches:
            dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            if size >= 1_000_000:
                size_str = f"{size/1_000_000:.1f}MB"
            elif size >= 1000:
                size_str = f"{size/1000:.1f}KB"
            else:
                size_str = f"{size}B"
            lines.append(f"  {dt}  {size_str:>8}  {rel_path}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error buscando archivos: {type(e).__name__}: {e}"


# ───────────────────────────────────────────────────────
# list_files — Directory listing with metadata
# ───────────────────────────────────────────────────────

async def list_files(
    path: Optional[str] = None,
    show_hidden: Optional[bool] = None,
) -> str:
    """List files and directories with metadata.

    Args:
        path: Directory path. Default: current directory.
        show_hidden: Include hidden files (starting with .). Default: False.

    Returns:
        Formatted directory listing.
    """
    dir_path = os.path.expanduser(path or ".")
    dir_path = os.path.abspath(dir_path)
    hidden = show_hidden or False

    if not os.path.exists(dir_path):
        return f"Error: Directorio no encontrado: {path}"

    if not os.path.isdir(dir_path):
        return f"Error: No es un directorio: {path}"

    try:
        entries = []
        for name in sorted(os.listdir(dir_path)):
            if not hidden and name.startswith("."):
                continue

            full = os.path.join(dir_path, name)
            try:
                st = os.stat(full)
                is_dir = stat.S_ISDIR(st.st_mode)
                size = st.st_size
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")

                if is_dir:
                    entries.append(f"  {mtime}  {'<DIR>':>8}  {name}/")
                else:
                    if size >= 1_000_000:
                        size_str = f"{size/1_000_000:.1f}MB"
                    elif size >= 1000:
                        size_str = f"{size/1000:.1f}KB"
                    else:
                        size_str = f"{size}B"
                    entries.append(f"  {mtime}  {size_str:>8}  {name}")
            except OSError:
                entries.append(f"  {'?':>20}  {name}")

        if not entries:
            return f"Directorio vacio: {dir_path}"

        return f"[{dir_path} | {len(entries)} entradas]\n\n" + "\n".join(entries)

    except PermissionError:
        return f"Error: Sin permisos para listar: {dir_path}"
    except Exception as e:
        return f"Error listando directorio: {type(e).__name__}: {e}"
