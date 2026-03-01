"""
Plugin loader — Discovers and loads tool plugins from directories.

Plugins are Python files that define:
  - PLUGIN_NAME: str  (unique name)
  - PLUGIN_DESCRIPTION: str
  - PLUGIN_CATEGORY: str
  - register(registry: ToolRegistry) -> int  (registers tools, returns count)

Plugins can also be loaded from the old v1.8 format (individual tool files).
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import List, Optional

from ..registry import ToolRegistry

logger = logging.getLogger(__name__)


def load_plugins(
    registry: ToolRegistry,
    plugin_dirs: Optional[List[str]] = None,
) -> int:
    """Load all plugins from the given directories.

    Args:
        registry: Tool registry to register into.
        plugin_dirs: List of directory paths to scan.

    Returns:
        Total number of tools registered from plugins.
    """
    if plugin_dirs is None:
        plugin_dirs = []

    # Add default plugin directory
    default_dir = str(Path(__file__).parent)
    if default_dir not in plugin_dirs:
        plugin_dirs.insert(0, default_dir)

    total = 0
    for dir_path in plugin_dirs:
        p = Path(dir_path)
        if not p.is_dir():
            logger.debug(f"Plugin dir not found: {dir_path}")
            continue

        for py_file in sorted(p.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "loader.py":
                continue
            try:
                count = _load_plugin_file(registry, py_file)
                total += count
            except Exception as e:
                logger.warning(f"⚠️ Error cargando plugin {py_file.name}: {e}")

    if total:
        logger.info(f"🔌 {total} tools cargadas desde plugins")
    return total


def _load_plugin_file(registry: ToolRegistry, path: Path) -> int:
    """Load a single plugin file."""
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if not spec or not spec.loader:
        return 0

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # New-style plugin: has register() function
    if hasattr(module, "register"):
        count = module.register(registry)
        name = getattr(module, "PLUGIN_NAME", path.stem)
        logger.info(f"✅ Plugin '{name}': {count} tools")
        return count

    # Legacy-style: has execute() function (v1.8 compat)
    if hasattr(module, "execute"):
        name = getattr(module, "TOOL_NAME", path.stem)
        desc = getattr(module, "DESCRIPTION", f"Tool: {name}")
        cat = getattr(module, "CATEGORY", "Plugin")
        params_list = getattr(module, "PARAMETERS", [])
        # Convert list to dict
        params = {p: "" for p in params_list} if isinstance(params_list, list) else params_list

        registry.register(
            name=name,
            description=desc,
            category=cat,
            parameters=params,
            executor=module.execute,
            source="plugin",
        )
        logger.info(f"✅ Legacy plugin '{name}'")
        return 1

    logger.debug(f"Plugin {path.name} has no register() or execute()")
    return 0


def load_v18_tools(
    registry: ToolRegistry,
    v18_tools_dir: str = "/home/tokio/tokioai-v1.8/tokio-cli/engine/tools",
) -> int:
    """Load tools from v1.8 directory for backwards compatibility.

    This scans the old tools directory and registers any tools that
    have a standard function signature.

    Args:
        registry: Tool registry.
        v18_tools_dir: Path to v1.8 tools directory.

    Returns:
        Number of tools loaded.
    """
    p = Path(v18_tools_dir)
    if not p.is_dir():
        logger.debug(f"v1.8 tools dir not found: {v18_tools_dir}")
        return 0

    total = 0
    for py_file in sorted(p.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            count = _load_plugin_file(registry, py_file)
            total += count
        except Exception as e:
            logger.debug(f"Could not load v1.8 tool {py_file.name}: {e}")

    if total:
        logger.info(f"📦 {total} tools cargadas desde v1.8")
    return total
