#!/usr/bin/env python3
"""
TokioAI Ops CLI — Claude-powered operations tool.

A Claude Code-like CLI specialized for TokioAI infrastructure.
Diagnoses problems, fixes code, controls all systems, manages deployments.

Usage:
    python3 -m tokio_cli "fix the entity crash"
    python3 -m tokio_cli "restart all services on raspi"
    python3 -m tokio_cli "check why HA is not working"
    python3 -m tokio_cli   # interactive mode

Architecture knowledge is built-in — it knows every component, IP, port,
file path, and how everything connects.
"""
from __future__ import annotations

import json
import os
import readline
import subprocess
import sys
import textwrap
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "teco-sdb-irt-4f83")
VERTEX_REGION = os.getenv("VERTEX_REGION", "global")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 8192

# SSH keys
SSH_RASPI = os.path.expanduser("~/.ssh/id_rsa_raspberry")
SSH_GCP = os.path.expanduser("~/.ssh/google_compute_engine")

# Hosts — configure via env vars or .env file
RASPI_IP = os.getenv("RASPI_IP", "")
RASPI_TS = os.getenv("RASPI_TAILSCALE_IP", "")
RASPI_USER = os.getenv("RASPI_SSH_USER", "mrmoz")
GCP_IP = os.getenv("GCP_SSH_HOST", "")
GCP_USER = os.getenv("GCP_SSH_USER", "osboxes")
ROUTER_IP = os.getenv("ROUTER_IP", "")

# History file
HISTORY_FILE = os.path.expanduser("~/.tokio_ops_history")

SYSTEM_PROMPT = """You are TokioAI Ops — an autonomous operations CLI for the TokioAI infrastructure.
You have FULL access to execute commands on all systems. You diagnose, fix, deploy, and manage everything.
You are concise, direct, and technical. You act like Claude Code but specialized for TokioAI.

## Architecture

Configure hosts via environment variables: RASPI_IP, RASPI_TAILSCALE_IP, GCP_SSH_HOST, ROUTER_IP.

### Raspberry Pi 5
- **SSH**: ssh -i ~/.ssh/id_rsa_raspberry user@$RASPI_IP (LAN) or user@$RASPI_TAILSCALE_IP (Tailscale)
- **Entity UI**: runs as `python3 -m tokio_raspi --api`
- **Entity API**: port 5000 (Flask)
- **Drone proxy**: port 5001 (systemd: tokio-drone-proxy)
- **Home Assistant**: Docker container, port 8123
- **Hailo-8L**: AI accelerator for vision (/dev/hailo0)

### GCP VM
- **SSH**: ssh -i ~/.ssh/google_compute_engine user@$GCP_SSH_HOST
- **TokioAI Agent**: Docker container tokio-agent
- **Telegram Bot**: Docker container tokio-telegram
- **WAF Stack**: 7 containers (postgres, kafka, nginx, etc)
- **Tools**: bind mount, edit on disk and restart container

### Router
- **SSH**: ssh root@$ROUTER_IP

## Key Files
- tokio_raspi/main.py — entity UI, all panels, face rendering, vision processing
- tokio_raspi/ai_brain.py — Claude vision analysis (real AI thoughts)
- tokio_raspi/wifi_defense.py — WiFi attack detection (scapy)
- tokio_raspi/health_monitor.py — BLE smartwatch (gatttool)
- tokio_raspi/vision_filter.py — Claude teaches Hailo (false positive filter)
- tokio_raspi/thought_log.py — persistent thought storage
- tokio_raspi/drone_safety_proxy.py — Tello drone control
- tokio_raspi/api_server.py — Flask API for remote control
- tokio_raspi/tokio_face.py — animated face rendering (pygame)
- tokio_raspi/coffee_esphome.py — Philips coffee machine via HA/ESPHome
- tokio_raspi/autostart.sh — autostart script for Raspi boot

## Critical Rules
- NEVER kill drone proxy when restarting entity (use pkill -f 'tokio_raspi.__main__')
- Entity runs from /home/mrmoz/tokio_raspi/ NOT /home/mrmoz/tokioai-v2/
- Always deploy to BOTH /home/mrmoz/tokio_raspi/ AND /home/mrmoz/tokioai-v2/tokio_raspi/
- Always clear __pycache__ after deploying new code
- wlan1 needs rmmod rtl8xxxu + modprobe rtl8xxxu after reboot for monitor mode
- gatttool processes must be killed before entity restart
- Port 5000 must be freed before entity restart
- docker compose restart does NOT reload .env — must use --force-recreate
- Code is BAKED into Docker image — edit on disk, then docker compose build + recreate

## Common Operations
- Restart entity: ssh raspi "pkill -f 'tokio_raspi.__main__'; sleep 2; sudo fuser -k 5000/tcp; cd /home/mrmoz && nohup python3 -u -m tokio_raspi --api > /tmp/tokio_entity.log 2>&1 &"
- Check entity log: ssh raspi "tail -50 /tmp/tokio_entity.log"
- Check entity API: ssh raspi "curl -s http://127.0.0.1:5000/status"
- Deploy to Raspi: scp file to /home/mrmoz/tokio_raspi/ AND /home/mrmoz/tokioai-v2/tokio_raspi/, clear __pycache__
- Deploy to GCP: scp to /tmp/, sudo cp to /opt/tokioai-v2/, docker compose recreate
- Check HA: ssh raspi "docker ps | grep homeassistant"
- Restart HA: ssh raspi "docker restart homeassistant"
- WiFi defense status: ssh raspi "curl -s http://127.0.0.1:5000/wifi/status"
- Health monitor: ssh raspi "curl -s http://127.0.0.1:5000/health"

## Your Approach
1. When user describes a problem: diagnose first (read logs, check status)
2. Identify root cause
3. Fix it (edit code, restart service, change config)
4. Verify the fix works
5. Explain concisely what you found and did

Be direct. Act autonomously. Fix things. You ARE TokioAI's operations brain."""

# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "execute_local",
        "description": "Execute a command on the local dev machine. Use for git, file ops, local builds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "execute_raspi",
        "description": "Execute a command on the Raspberry Pi via SSH. Uses LAN IP, falls back to Tailscale.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run on Raspi as mrmoz"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "execute_gcp",
        "description": "Execute a command on the GCP VM via SSH. For Docker, agent, WAF operations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run on GCP as osboxes"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "execute_router",
        "description": "Execute a command on the GL.iNet router via SSH (root).",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to run on router as root"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from any system. Prefix with raspi: or gcp: for remote files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Use raspi:/path or gcp:/path for remote"},
                "lines": {"type": "integer", "description": "Max lines to read (default all, use for large files)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Prefix with raspi: or gcp: for remote.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Use raspi:/path or gcp:/path for remote"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace a specific string in a file. Like sed but safer — verifies uniqueness.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path. Use raspi:/path or gcp:/path for remote"},
                "old_text": {"type": "string", "description": "Exact text to find and replace (must be unique in file)"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for text patterns across files. Like grep -rn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                "path": {"type": "string", "description": "Directory to search in (default: repo root). Use raspi:/path or gcp:/path for remote."},
                "glob": {"type": "string", "description": "File pattern filter (e.g., '*.py')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "api_call",
        "description": "Call a TokioAI API endpoint on the Raspi (port 5000).",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "DELETE"]},
                "path": {"type": "string", "description": "API path like /status or /wifi/status"},
                "data": {"type": "object", "description": "JSON body for POST requests"},
            },
            "required": ["method", "path"],
        },
    },
    {
        "name": "deploy",
        "description": "Deploy a local file to Raspi and/or GCP. Handles SCP + cache clear + restart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string", "description": "Local file path to deploy"},
                "target": {"type": "string", "enum": ["raspi", "gcp", "both"], "description": "Where to deploy"},
                "restart": {"type": "boolean", "description": "Restart the service after deploy"},
            },
            "required": ["local_path", "target"],
        },
    },
    {
        "name": "diagnose",
        "description": "Run a full diagnostic check on a system. Returns process list, disk, memory, docker, services, logs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["raspi", "gcp", "local", "all"], "description": "System to diagnose"},
                "focus": {"type": "string", "description": "Optional: specific focus area (entity, docker, wifi, health, drone, ha, network)"},
            },
            "required": ["target"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _run_cmd(cmd: str, timeout: int = 30) -> str:
    """Run a local command and return output."""
    timeout = min(max(timeout, 5), 120)
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = r.stdout + r.stderr
        return output.strip()[:8000]
    except subprocess.TimeoutExpired:
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"


def _ssh_cmd(host: str, key: str, user: str, cmd: str, timeout: int = 30) -> str:
    """Run a command via SSH."""
    timeout = min(max(timeout, 5), 120)
    # Escape double quotes in command
    escaped = cmd.replace('"', '\\"')
    ssh = f'ssh -i {key} -o ConnectTimeout=5 -o StrictHostKeyChecking=no {user}@{host} "{escaped}"'
    return _run_cmd(ssh, timeout)


def _raspi_cmd(cmd: str, timeout: int = 30) -> str:
    """Run command on Raspi — tries LAN first, then Tailscale."""
    result = _ssh_cmd(RASPI_IP, SSH_RASPI, RASPI_USER, cmd, timeout)
    if "Connection refused" in result or "No route" in result or "Connection timed out" in result:
        result = _ssh_cmd(RASPI_TS, SSH_RASPI, RASPI_USER, cmd, timeout)
    return result


def _gcp_cmd(cmd: str, timeout: int = 30) -> str:
    """Run command on GCP."""
    return _ssh_cmd(GCP_IP, SSH_GCP, GCP_USER, cmd, timeout)


def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool and return the result."""
    timeout = input_data.get("timeout", 30)

    if name == "execute_local":
        return _run_cmd(input_data["command"], timeout)

    elif name == "execute_raspi":
        return _raspi_cmd(input_data["command"], timeout)

    elif name == "execute_gcp":
        return _gcp_cmd(input_data["command"], timeout)

    elif name == "execute_router":
        # Router SSH via Raspi (key is on Raspi)
        cmd = input_data["command"]
        return _raspi_cmd(f'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{ROUTER_IP} "{cmd}"')

    elif name == "read_file":
        path = input_data["path"]
        max_lines = input_data.get("lines", 0)
        tail = f" | head -n {max_lines}" if max_lines else ""

        if path.startswith("raspi:"):
            return _raspi_cmd(f"cat {path[6:]}{tail}")
        elif path.startswith("gcp:"):
            return _gcp_cmd(f"cat {path[4:]}{tail}")
        else:
            try:
                with open(path, "r") as f:
                    content = f.read()
                if max_lines:
                    content = "\n".join(content.split("\n")[:max_lines])
                return content[:16000]
            except Exception as e:
                return f"ERROR: {e}"

    elif name == "write_file":
        path = input_data["path"]
        content = input_data["content"]
        if path.startswith("raspi:"):
            remote_path = path[6:]
            tmp = f"/tmp/tokio_deploy_{int(time.time())}"
            with open(tmp, "w") as f:
                f.write(content)
            result = _run_cmd(f"scp -i {SSH_RASPI} {tmp} {RASPI_USER}@{RASPI_IP}:{remote_path}")
            os.unlink(tmp)
            return result or f"Written to raspi:{remote_path}"
        elif path.startswith("gcp:"):
            remote_path = path[4:]
            tmp = f"/tmp/tokio_deploy_{int(time.time())}"
            with open(tmp, "w") as f:
                f.write(content)
            _run_cmd(f"scp -i {SSH_GCP} {tmp} {GCP_USER}@{GCP_IP}:/tmp/_deploy_tmp")
            result = _gcp_cmd(f"sudo cp /tmp/_deploy_tmp {remote_path}")
            os.unlink(tmp)
            return result or f"Written to gcp:{remote_path}"
        else:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                return f"Written to {path}"
            except Exception as e:
                return f"ERROR: {e}"

    elif name == "edit_file":
        path = input_data["path"]
        old_text = input_data["old_text"]
        new_text = input_data["new_text"]

        if path.startswith("raspi:") or path.startswith("gcp:"):
            # Remote edit: read, replace, write back
            prefix = "raspi:" if path.startswith("raspi:") else "gcp:"
            remote_path = path[len(prefix):]
            content = execute_tool("read_file", {"path": path})
            if content.startswith("ERROR"):
                return content
            count = content.count(old_text)
            if count == 0:
                return f"ERROR: old_text not found in {path}"
            if count > 1:
                return f"ERROR: old_text appears {count} times — must be unique. Provide more context."
            new_content = content.replace(old_text, new_text, 1)
            return execute_tool("write_file", {"path": path, "content": new_content})
        else:
            try:
                with open(path, "r") as f:
                    content = f.read()
                count = content.count(old_text)
                if count == 0:
                    return f"ERROR: old_text not found in {path}"
                if count > 1:
                    return f"ERROR: old_text appears {count} times — must be unique."
                new_content = content.replace(old_text, new_text, 1)
                with open(path, "w") as f:
                    f.write(new_content)
                return f"Edited {path} (replaced 1 occurrence)"
            except Exception as e:
                return f"ERROR: {e}"

    elif name == "search_files":
        pattern = input_data["pattern"]
        path = input_data.get("path", "/home/osboxes/tokioai-v2")
        glob_filter = input_data.get("glob", "")
        include = f"--include='{glob_filter}'" if glob_filter else "--include='*.py'"

        if path.startswith("raspi:"):
            remote_path = path[6:] or "/home/mrmoz/tokio_raspi"
            return _raspi_cmd(f"grep -rn {include} '{pattern}' {remote_path} | head -30")
        elif path.startswith("gcp:"):
            remote_path = path[4:] or "/opt/tokioai-v2"
            return _gcp_cmd(f"grep -rn {include} '{pattern}' {remote_path} | head -30")
        else:
            return _run_cmd(f"grep -rn {include} '{pattern}' {path} | head -50")

    elif name == "api_call":
        method = input_data["method"]
        path = input_data["path"]
        data = input_data.get("data", {})
        if method == "GET":
            cmd = f"curl -s --max-time 10 http://127.0.0.1:5000{path}"
        elif method == "POST":
            json_str = json.dumps(data).replace('"', '\\\\\\"')
            cmd = f'curl -s --max-time 10 -X POST -H "Content-Type: application/json" -d \\"{json_str}\\" http://127.0.0.1:5000{path}'
        elif method == "DELETE":
            cmd = f"curl -s --max-time 10 -X DELETE http://127.0.0.1:5000{path}"
        else:
            return f"Unknown method: {method}"
        return _raspi_cmd(cmd)

    elif name == "deploy":
        local_path = input_data["local_path"]
        target = input_data["target"]
        restart = input_data.get("restart", False)
        filename = os.path.basename(local_path)
        results = []

        if target in ("raspi", "both"):
            # Deploy to BOTH Raspi directories
            _run_cmd(f"scp -i {SSH_RASPI} {local_path} {RASPI_USER}@{RASPI_IP}:/home/mrmoz/tokio_raspi/{filename}")
            _run_cmd(f"scp -i {SSH_RASPI} {local_path} {RASPI_USER}@{RASPI_IP}:/home/mrmoz/tokioai-v2/tokio_raspi/{filename}")
            _raspi_cmd("rm -rf /home/mrmoz/tokio_raspi/__pycache__ /home/mrmoz/tokioai-v2/tokio_raspi/__pycache__")
            results.append(f"Raspi: deployed {filename}")
            if restart:
                _raspi_cmd("pkill -f 'tokio_raspi.__main__'; sleep 2; sudo fuser -k 5000/tcp 2>/dev/null; cd /home/mrmoz && nohup python3 -u -m tokio_raspi --api > /tmp/tokio_entity.log 2>&1 &")
                results.append("Raspi: entity relaunched")

        if target in ("gcp", "both"):
            _run_cmd(f"scp -i {SSH_GCP} {local_path} {GCP_USER}@{GCP_IP}:/tmp/{filename}")
            gcp_path = f"/opt/tokioai-v2/tokio_agent/engine/tools/builtin/{filename}"
            _gcp_cmd(f"sudo cp /tmp/{filename} {gcp_path}")
            results.append(f"GCP: deployed {filename}")
            if restart:
                _gcp_cmd("cd /opt/tokioai-v2 && sudo docker compose -f docker-compose.cloud.yml restart tokio-cli")
                results.append("GCP: agent restarted")

        return "\n".join(results) or "Deploy completed"

    elif name == "diagnose":
        target = input_data["target"]
        focus = input_data.get("focus", "")
        results = []

        if target in ("raspi", "all"):
            results.append("=== RASPBERRY PI ===")
            if not focus or focus == "entity":
                results.append(_raspi_cmd("ps aux | grep -E 'tokio|python' | grep -v grep"))
                results.append(_raspi_cmd("curl -s --max-time 3 http://127.0.0.1:5000/status 2>/dev/null || echo 'Entity API: unreachable'"))
            if not focus or focus == "docker" or focus == "ha":
                results.append(_raspi_cmd("docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null"))
            if not focus or focus == "wifi":
                results.append(_raspi_cmd("ip link show wlan1 2>/dev/null | head -2"))
                results.append(_raspi_cmd("curl -s --max-time 3 http://127.0.0.1:5000/wifi/status 2>/dev/null || echo 'WiFi defense API: unreachable'"))
            if not focus or focus == "health":
                results.append(_raspi_cmd("curl -s --max-time 3 http://127.0.0.1:5000/health 2>/dev/null || echo 'Health API: unreachable'"))
            if not focus or focus == "drone":
                results.append(_raspi_cmd("systemctl is-active tokio-drone-proxy"))
                results.append(_raspi_cmd("curl -s --max-time 3 http://127.0.0.1:5001/health 2>/dev/null || echo 'Drone proxy: unreachable'"))
            if not focus or focus == "network":
                results.append(_raspi_cmd("ip addr show eth0 | grep inet"))
                results.append(_raspi_cmd("tailscale status --peers=false 2>/dev/null"))
            if not focus:
                results.append(_raspi_cmd("df -h / | tail -1"))
                results.append(_raspi_cmd("free -h | head -2"))
                results.append(_raspi_cmd("vcgencmd get_throttled 2>/dev/null"))
                results.append(_raspi_cmd("tail -10 /tmp/tokio_entity.log 2>/dev/null"))

        if target in ("gcp", "all"):
            results.append("\n=== GCP VM ===")
            if not focus or focus == "docker":
                results.append(_gcp_cmd("docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null"))
            if not focus:
                results.append(_gcp_cmd("df -h / | tail -1"))
                results.append(_gcp_cmd("free -h | head -2"))
                results.append(_gcp_cmd("docker logs tokio-agent --tail 10 2>/dev/null"))

        if target in ("local", "all"):
            results.append("\n=== LOCAL DEV ===")
            results.append(_run_cmd("df -h / | tail -1"))
            results.append(_run_cmd("free -h | head -2"))
            if not focus:
                results.append(_run_cmd("tailscale status --peers=false 2>/dev/null"))

        return "\n".join(r for r in results if r)

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

def init_client():
    """Initialize Anthropic client (Vertex AI or direct)."""
    sa_paths = [
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
        os.path.expanduser("~/.config/gcloud/application_default_credentials.json"),
        "/home/osboxes/tokioai-v2/vertex-credentials.json",
    ]
    for sa_path in sa_paths:
        if sa_path and os.path.isfile(sa_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path
            break

    try:
        from anthropic import AnthropicVertex
        return AnthropicVertex(region=VERTEX_REGION, project_id=VERTEX_PROJECT)
    except Exception:
        pass

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)

    print("\033[31mERROR: No API credentials found\033[0m")
    print("Set GOOGLE_APPLICATION_CREDENTIALS or ANTHROPIC_API_KEY")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

C_RESET = "\033[0m"
C_CYAN = "\033[36m"
C_DIM = "\033[90m"
C_BOLD = "\033[1m"
C_MAGENTA = "\033[35m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BLUE = "\033[34m"

TOOL_ICONS = {
    "execute_local": "⚡",
    "execute_raspi": "🍓",
    "execute_gcp": "☁️",
    "execute_router": "📡",
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "🔧",
    "search_files": "🔍",
    "api_call": "🌐",
    "deploy": "🚀",
    "diagnose": "🩺",
}


def print_tool_call(block):
    """Print a tool call in a compact, readable format."""
    icon = TOOL_ICONS.get(block.name, "🔧")
    name = block.name
    inp = block.input

    detail = ""
    if name in ("execute_local", "execute_raspi", "execute_gcp", "execute_router"):
        cmd = inp.get("command", "")
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        detail = f"{C_DIM}{cmd}{C_RESET}"
    elif name in ("read_file", "write_file", "edit_file"):
        detail = f"{C_DIM}{inp.get('path', '')}{C_RESET}"
    elif name == "search_files":
        detail = f"{C_DIM}'{inp.get('pattern', '')}' in {inp.get('path', 'repo')}{C_RESET}"
    elif name == "deploy":
        detail = f"{C_DIM}{inp.get('local_path', '')} -> {inp.get('target', '')}{C_RESET}"
    elif name == "api_call":
        detail = f"{C_DIM}{inp.get('method', '')} {inp.get('path', '')}{C_RESET}"
    elif name == "diagnose":
        detail = f"{C_DIM}{inp.get('target', '')} {inp.get('focus', '')}{C_RESET}"

    print(f"  {icon} {C_CYAN}{name}{C_RESET} {detail}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class TokioOps:
    """TokioAI Operations CLI."""

    def __init__(self):
        self._client = init_client()
        self._messages: list[dict] = []
        self._max_turns = 30
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def chat(self, user_input: str) -> str:
        """Process a user request with tool use."""
        self._messages.append({"role": "user", "content": user_input})

        for turn in range(self._max_turns):
            try:
                response = self._client.messages.create(
                    model=VERTEX_MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=self._messages,
                )
            except Exception as e:
                error_msg = f"API Error: {e}"
                print(f"\n{C_RED}{error_msg}{C_RESET}")
                return error_msg

            # Track token usage
            if hasattr(response, "usage"):
                self._total_input_tokens += getattr(response.usage, "input_tokens", 0)
                self._total_output_tokens += getattr(response.usage, "output_tokens", 0)

            if response.stop_reason == "tool_use":
                assistant_content = response.content
                self._messages.append({"role": "assistant", "content": assistant_content})

                # Print any text blocks before tools
                for block in assistant_content:
                    if hasattr(block, "text") and block.text.strip():
                        print(f"\n{block.text}")

                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        print_tool_call(block)
                        result = execute_tool(block.name, block.input)

                        # Show truncated result for visibility
                        result_preview = result[:200].replace("\n", " ")
                        if len(result) > 200:
                            result_preview += "..."
                        print(f"    {C_DIM}→ {result_preview}{C_RESET}")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self._messages.append({"role": "user", "content": tool_results})
            else:
                text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        text += block.text
                self._messages.append({"role": "assistant", "content": text})
                return text

        return "Max tool turns reached. The operation may be incomplete."

    def token_usage(self) -> str:
        """Return token usage summary."""
        return f"Tokens: {self._total_input_tokens:,} in / {self._total_output_tokens:,} out"

    def reset(self):
        """Clear conversation history."""
        self._messages.clear()
        print(f"{C_DIM}Conversation reset.{C_RESET}")

    def compact(self):
        """Keep only the last 4 messages to save context."""
        if len(self._messages) > 4:
            self._messages = self._messages[-4:]
            print(f"{C_DIM}Context compacted to last 4 messages.{C_RESET}")


def _load_history():
    """Load readline history."""
    try:
        if os.path.exists(HISTORY_FILE):
            readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass


def _save_history():
    """Save readline history."""
    try:
        readline.set_history_length(500)
        readline.write_history_file(HISTORY_FILE)
    except Exception:
        pass


def main():
    ops = TokioOps()
    _load_history()

    # Single command mode
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"{C_BOLD}{C_MAGENTA}TokioAI Ops{C_RESET} > {query}\n")
        response = ops.chat(query)
        print(f"\n{response}")
        print(f"\n{C_DIM}{ops.token_usage()}{C_RESET}")
        _save_history()
        return

    # Interactive mode
    print(f"""{C_BOLD}{C_MAGENTA}
{'=' * 56}
  TokioAI Ops — Autonomous Infrastructure CLI
  Powered by Claude | Model: {VERTEX_MODEL}
{'=' * 56}{C_RESET}

{C_DIM}Commands: exit, reset, compact, tokens, diagnose [target]{C_RESET}
""")

    while True:
        try:
            user_input = input(f"{C_BOLD}{C_GREEN}tokio>{C_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}Bye.{C_RESET}")
            break

        if not user_input:
            continue

        _save_history()

        if user_input.lower() in ("exit", "quit", "q"):
            print(f"{C_DIM}Bye.{C_RESET}")
            break
        if user_input.lower() == "reset":
            ops.reset()
            continue
        if user_input.lower() == "compact":
            ops.compact()
            continue
        if user_input.lower() == "tokens":
            print(f"{C_DIM}{ops.token_usage()}{C_RESET}")
            continue

        # Quick shortcuts
        if user_input.lower().startswith("diagnose"):
            parts = user_input.split()
            target = parts[1] if len(parts) > 1 else "all"
            focus = parts[2] if len(parts) > 2 else ""
            user_input = f"Run a full diagnostic on {target}" + (f" focusing on {focus}" if focus else "")

        t0 = time.time()
        response = ops.chat(user_input)
        elapsed = time.time() - t0
        print(f"\n{response}")
        print(f"\n{C_DIM}[{elapsed:.1f}s | {ops.token_usage()}]{C_RESET}\n")

    _save_history()


if __name__ == "__main__":
    main()
