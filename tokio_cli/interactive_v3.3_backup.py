#!/usr/bin/env python3
"""
TokioAI Interactive CLI v3.3 — Pro Terminal Interface.

Features:
- Rich markdown rendering (headers, bold, code blocks, tables, lists)
- Gradient banner with system info
- Styled tool calls with icons & progress
- Status bar with token/tool/time/cost tracking
- Streaming responses (token by token)
- Escape to cancel running requests
- readline history & session persistence
- 14 slash commands (instant, no LLM)
- Tab completion for commands, tools, slash commands
- Multi-line input with backslash continuation
- Cross-platform: Linux, macOS, Windows

Usage:
    python3 -m tokio_cli                    # interactive mode
    python3 -m tokio_cli "fix the bug"      # single command
    python3 -m tokio_cli --session mysession # resume session
    python3 -m tokio_cli --unlimited        # no round/time limits
    python3 -m tokio_cli --persistent       # keeps working until 'stop'
    python3 -m tokio_cli --max-rounds 100   # custom round limit
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import signal
import sys
import time
import warnings
import json as _json
import subprocess as _sp
from typing import Optional

_IS_WINDOWS = platform.system() == "Windows"

# Cross-platform readline
if _IS_WINDOWS:
    try:
        import pyreadline3 as readline  # type: ignore
    except ImportError:
        import readline  # type: ignore  # fallback
else:
    import readline

# Cross-platform terminal modules
if _IS_WINDOWS:
    import msvcrt
    select = None  # type: ignore
    termios = None  # type: ignore
    tty = None  # type: ignore
else:
    import select
    import termios
    import tty

# Suppress asyncio child process reap warnings (cosmetic, happens on cancel)
warnings.filterwarnings("ignore", message="child process pid.*exit status already read")
logging.getLogger("asyncio").setLevel(logging.ERROR)

# ─── ANSI Colors ─────────────────────────────────────

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_ITALIC = "\033[3m"
C_UNDERLINE = "\033[4m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"
C_GRAY = "\033[90m"
C_BRIGHT_RED = "\033[91m"
C_BRIGHT_GREEN = "\033[92m"
C_BRIGHT_YELLOW = "\033[93m"
C_BRIGHT_BLUE = "\033[94m"
C_BRIGHT_MAGENTA = "\033[95m"
C_BRIGHT_CYAN = "\033[96m"
C_BRIGHT_WHITE = "\033[97m"
C_CLEAR_LINE = "\033[2K\033[G"

# Background colors
C_BG_GRAY = "\033[48;5;236m"
C_BG_DARK = "\033[48;5;233m"
C_BG_BLUE = "\033[48;5;24m"
C_BG_GREEN = "\033[48;5;22m"
C_BG_RED = "\033[48;5;52m"

# 256-color for gradients
def c256(n: int) -> str:
    return f"\033[38;5;{n}m"

# ─── Terminal Width ──────────────────────────────────

def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


# ─── History & Session Persistence ───────────────────

HISTORY_FILE = os.path.expanduser("~/.tokio_cli_history")
SESSION_FILE = os.path.expanduser("~/.tokio_cli_session.json")

# ─── Tab Completion ─────────────────────────────────

_CLI_COMMANDS = [
    "exit", "quit", "help", "reset", "stats", "tools", "model", "clear",
    "unlimited", "persistent", "stop", "config",
    "/status", "/waf", "/health", "/drone", "/threats", "/entity",
    "/sitrep", "/see", "/containers", "/wifi", "/coffee", "/logs", "/ha", "/picar",
]

_TOOL_NAMES: list[str] = []  # populated after agent creation


def _completer(text: str, state: int):
    """Tab completion for CLI commands and tool names."""
    candidates = _CLI_COMMANDS + _TOOL_NAMES
    matches = [c for c in candidates if c.lower().startswith(text.lower())]
    return matches[state] if state < len(matches) else None


def _load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            readline.read_history_file(HISTORY_FILE)
        # Tab completion
        readline.set_completer(_completer)
        readline.parse_and_bind('tab: complete')
        readline.set_completer_delims(' \t\n')
        readline.parse_and_bind('set horizontal-scroll-mode off')
        readline.parse_and_bind('set enable-bracketed-paste on')
        readline.parse_and_bind('set bell-style none')
        readline.parse_and_bind('set show-all-if-ambiguous on')
    except Exception:
        pass


def _save_history():
    try:
        readline.set_history_length(1000)
        readline.write_history_file(HISTORY_FILE)
    except Exception:
        pass


def _save_session_state(sid: str, last_messages: list):
    """Save session state to disk so it persists across CLI restarts."""
    try:
        recent = last_messages[-20:] if last_messages else []
        state = {
            "session_id": sid,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": recent,
        }
        with open(SESSION_FILE, "w") as f:
            _json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _load_session_state():
    """Load previous session state from disk."""
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r") as f:
                state = _json.load(f)
            ts = state.get("timestamp", "")
            if ts:
                from datetime import datetime, timedelta
                saved = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if datetime.now() - saved > timedelta(days=7):
                    return None
            return state
    except Exception:
        pass
    return None


# ─── Markdown Renderer ──────────────────────────────

class MarkdownRenderer:
    """Renders markdown-like text with ANSI colors for terminal."""

    _BOLD = re.compile(r'\*\*(.+?)\*\*')
    _ITALIC = re.compile(r'(?<!\*)\*([^*]+?)\*(?!\*)')
    _INLINE_CODE = re.compile(r'`([^`\n]+?)`')
    _LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    _STRIKETHROUGH = re.compile(r'~~(.+?)~~')

    @classmethod
    def render(cls, text: str) -> str:
        """Render a complete markdown text block with ANSI styling."""
        lines = text.split('\n')
        result = []
        in_code_block = False
        code_lang = ""

        for line in lines:
            # Code block toggle
            if line.strip().startswith('```'):
                if not in_code_block:
                    in_code_block = True
                    code_lang = line.strip()[3:].strip()
                    lang_label = f" {code_lang}" if code_lang else ""
                    result.append(f"  {C_DIM}┌─{lang_label}{'─' * max(1, 40 - len(lang_label))}{C_RESET}")
                else:
                    in_code_block = False
                    result.append(f"  {C_DIM}└{'─' * 42}{C_RESET}")
                continue

            if in_code_block:
                result.append(f"  {C_DIM}│{C_RESET} {C_BRIGHT_GREEN}{line}{C_RESET}")
                continue

            # Headers
            if line.startswith('### '):
                result.append(f"  {C_BOLD}{C_CYAN}   {line[4:]}{C_RESET}")
                continue
            if line.startswith('## '):
                result.append(f"  {C_BOLD}{C_BRIGHT_CYAN}  {line[3:]}{C_RESET}")
                continue
            if line.startswith('# '):
                result.append(f"  {C_BOLD}{C_BRIGHT_WHITE}━━ {line[2:]} ━━{C_RESET}")
                continue

            # Horizontal rule
            if re.match(r'^-{3,}$', line.strip()) or re.match(r'^\*{3,}$', line.strip()):
                w = min(_term_width() - 4, 60)
                result.append(f"  {C_DIM}{'─' * w}{C_RESET}")
                continue

            # Unordered list
            m = re.match(r'^(\s*)([-*+])\s+(.+)', line)
            if m:
                indent, _, content = m.groups()
                depth = len(indent) // 2
                bullets = ['●', '○', '▸', '▹']
                bullet = bullets[depth % len(bullets)]
                color = [C_BRIGHT_CYAN, C_CYAN, C_BLUE, C_DIM][depth % 4]
                rendered = cls._render_inline(content)
                result.append(f"  {' ' * (depth * 2)}{color}{bullet}{C_RESET} {rendered}")
                continue

            # Ordered list
            m = re.match(r'^(\s*)(\d+)\.\s+(.+)', line)
            if m:
                indent, num, content = m.groups()
                rendered = cls._render_inline(content)
                result.append(f"  {indent}{C_BRIGHT_CYAN}{num}.{C_RESET} {rendered}")
                continue

            # Checkbox list
            m = re.match(r'^(\s*)[-*]\s+\[([ xX])\]\s+(.+)', line)
            if m:
                indent, check, content = m.groups()
                icon = f"{C_BRIGHT_GREEN}✓{C_RESET}" if check.lower() == 'x' else f"{C_DIM}○{C_RESET}"
                rendered = cls._render_inline(content)
                result.append(f"  {indent}{icon} {rendered}")
                continue

            # Blockquote
            if line.startswith('> '):
                rendered = cls._render_inline(line[2:])
                result.append(f"  {C_DIM}▌{C_RESET} {C_ITALIC}{rendered}{C_RESET}")
                continue

            # Table row
            if '|' in line and not line.strip().startswith('```'):
                cells = [c.strip() for c in line.strip().strip('|').split('|')]
                if all(re.match(r'^[-:]+$', c) for c in cells if c):
                    continue  # separator row
                w = min(_term_width() - 4, 80)
                # Determine available width per cell
                num_cells = len(cells)
                if num_cells > 0:
                    cell_width = max(8, (w - num_cells * 3) // num_cells)
                    formatted = []
                    for c in cells:
                        rendered = cls._render_inline(c)
                        formatted.append(rendered)
                    result.append(f"  {C_DIM}│{C_RESET} " + f" {C_DIM}│{C_RESET} ".join(formatted) + f" {C_DIM}│{C_RESET}")
                continue

            # Regular text
            rendered = cls._render_inline(line)
            result.append(rendered)

        return '\n'.join(result)

    @classmethod
    def _render_inline(cls, text: str) -> str:
        """Apply inline markdown formatting."""
        text = cls._LINK.sub(f'{C_UNDERLINE}{C_BRIGHT_BLUE}\\1{C_RESET}{C_DIM} (\\2){C_RESET}', text)
        text = cls._BOLD.sub(f'{C_BOLD}\\1{C_RESET}', text)
        text = cls._ITALIC.sub(f'{C_ITALIC}\\1{C_RESET}', text)
        text = cls._INLINE_CODE.sub(f'{C_BG_GRAY}{C_BRIGHT_GREEN} \\1 {C_RESET}', text)
        text = cls._STRIKETHROUGH.sub(f'{C_DIM}\\1{C_RESET}', text)
        return text


# ─── Spinner ─────────────────────────────────────────

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Spinner:
    """Async spinner that shows activity while waiting."""

    def __init__(self, message: str = "thinking"):
        self._message = message
        self._task: Optional[asyncio.Task] = None
        self._active = False
        self._start_time = 0.0

    def start(self, message: Optional[str] = None):
        if message:
            self._message = message
        self._active = True
        self._start_time = time.time()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.ensure_future(self._spin())

    def stop(self):
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        sys.stdout.write(C_CLEAR_LINE)
        sys.stdout.flush()

    def update(self, message: str):
        self._message = message

    async def _spin(self):
        i = 0
        try:
            while self._active:
                frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                elapsed = time.time() - self._start_time
                timer = f" {C_GRAY}({elapsed:.0f}s){C_RESET}" if elapsed >= 2 else ""
                sys.stdout.write(f"{C_CLEAR_LINE}  {C_BRIGHT_CYAN}{frame}{C_RESET} {C_GRAY}{self._message}{C_RESET}{timer}")
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass


# ─── Key Detection ───────────────────────────────────

def _check_escape() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        if _IS_WINDOWS:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                return ch == b'\x1b'
        else:
            rlist, _, _ = select.select([sys.stdin], [], [], 0)
            if rlist:
                ch = os.read(sys.stdin.fileno(), 1)
                return ch == b'\x1b'
    except (OSError, IOError, ValueError):
        # fd closed or invalid — don't crash
        pass
    except Exception:
        pass
    return False


def _flush_stdin():
    if not sys.stdin.isatty():
        return
    try:
        if _IS_WINDOWS:
            while msvcrt.kbhit():
                msvcrt.getch()
        else:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


# ─── Sensitive Data Masking ──────────────────────────

_SENSITIVE_PATTERNS = [
    (re.compile(r'github_pat_[A-Za-z0-9_]{20,}'), '[GITHUB_TOKEN]'),
    (re.compile(r'ghp_[A-Za-z0-9]{36,}'), '[GITHUB_TOKEN]'),
    (re.compile(r'gho_[A-Za-z0-9]{36,}'), '[GITHUB_TOKEN]'),
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), '[API_KEY]'),
    (re.compile(r'AIza[A-Za-z0-9_-]{35}'), '[GOOGLE_API_KEY]'),
    (re.compile(r'AKIA[A-Z0-9]{16}'), '[AWS_KEY]'),
    (re.compile(r'TELEGRAM_BOT_TOKEN=\S+'), 'TELEGRAM_BOT_TOKEN=[REDACTED]'),
    (re.compile(r'POSTGRES_PASSWORD=\S+'), 'POSTGRES_PASSWORD=[REDACTED]'),
    (re.compile(r'ANTHROPIC_API_KEY=\S+'), 'ANTHROPIC_API_KEY=[REDACTED]'),
    (re.compile(r'OPENAI_API_KEY=\S+'), 'OPENAI_API_KEY=[REDACTED]'),
    (re.compile(r'GOOGLE_API_KEY=\S+'), 'GOOGLE_API_KEY=[REDACTED]'),
    (re.compile(r'TOKEN=\S+'), 'TOKEN=[REDACTED]'),
    (re.compile(r'password["\s:=]+\S+', re.IGNORECASE), 'password=[REDACTED]'),
]


def _mask_sensitive(text: str) -> str:
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ─── Tool Icons (Emoji) ─────────────────────────────

TOOL_ICONS = {
    # Core
    "bash": "⚡", "python": "🐍", "read_file": "📖",
    "write_file": "✏️", "edit_file": "📝", "search_code": "🔍",
    "find_files": "📂", "list_files": "📁", "subagent": "🤖",
    # Infrastructure
    "docker": "🐳", "raspi_vision": "👁️", "postgres_query": "🗃️",
    "curl": "🌐", "wget": "⬇️", "gcp_compute": "☁️",
    "gcp_waf": "🛡️", "gcp_waf_deploy": "🚀", "gcp_waf_destroy": "💥",
    "host_control": "🖥️", "router_control": "📡", "infra": "🏗️",
    # IoT & Devices
    "coffee": "☕", "drone": "🚁", "iot_control": "🏠",
    # Security
    "security": "🔐", "prompt_guard": "🛡️", "self_heal": "💊",
    # Networking & DNS
    "cloudflare": "🌩️", "hostinger": "🌍", "tunnel": "🔗", "tenant": "🏢",
    # Documents & Data
    "document": "📄", "calendar": "📅",
    # System
    "user_preferences": "⚙️", "task_orchestrator": "📋",
}


def _format_tool_start(name: str, args: dict, tool_num: int) -> str:
    """Format tool start line with rich visual styling."""
    icon = TOOL_ICONS.get(name, "🔧")
    detail = ""

    if "command" in args:
        cmd = _mask_sensitive(str(args["command"])[:100])
        detail = f" {C_GRAY}{cmd}{C_RESET}"
    elif "path" in args:
        detail = f" {C_GRAY}{args['path']}{C_RESET}"
    elif "pattern" in args and "include" not in args:
        detail = f" {C_GRAY}'{args['pattern']}'{C_RESET}"
    elif "pattern" in args and "include" in args:
        detail = f" {C_GRAY}'{args['pattern']}' in {args['include']}{C_RESET}"
    elif "action" in args:
        extra = ""
        if "params" in args and isinstance(args["params"], dict):
            p = args["params"]
            hints = [f"{k}={v}" for k, v in list(p.items())[:2] if isinstance(v, (str, int, float, bool))]
            if hints:
                extra = f" ({', '.join(hints)})"
        detail = f" {C_GRAY}{args['action']}{extra}{C_RESET}"
    elif "query" in args:
        q = _mask_sensitive(str(args["query"])[:80])
        detail = f" {C_GRAY}{q}{C_RESET}"
    elif "url" in args:
        detail = f" {C_GRAY}{args['url'][:80]}{C_RESET}"
    elif "code" in args:
        lines = str(args["code"]).count('\n') + 1
        detail = f" {C_GRAY}({lines} lines){C_RESET}"

    return f"  {icon} {C_BOLD}{C_BRIGHT_CYAN}{name}{C_RESET}{detail}"


def _format_tool_result(name: str, output: str, success: bool = True) -> str:
    """Format tool result with smart truncation."""
    if not output or not output.strip():
        return f"    {C_BRIGHT_GREEN}✓{C_RESET} {C_DIM}(empty){C_RESET}"

    out = output.strip()
    lines = out.split('\n')
    line_count = len(lines)

    # Count chars
    char_count = len(out)

    # Status icon
    icon = f"{C_BRIGHT_GREEN}✓{C_RESET}" if success else f"{C_BRIGHT_RED}✗{C_RESET}"

    # Smart summary
    if char_count < 200 and line_count <= 3:
        preview = out.replace('\n', ' ↵ ')[:150]
        return f"    {icon} {C_DIM}{preview}{C_RESET}"
    else:
        # Show first and last line with count
        first = lines[0][:80]
        size = f"{char_count:,} chars" if char_count < 10000 else f"{char_count/1000:.0f}K chars"
        return f"    {icon} {C_DIM}{first}{'…' if len(lines[0]) > 80 else ''} ({line_count} lines, {size}){C_RESET}"


# ─── Slash Commands (instant, no LLM) ────────────────

# Load host config from env (same as tokio_ops)
_RASPI_IP = os.getenv("RASPI_IP", "192.168.8.161")
_RASPI_TS = os.getenv("RASPI_TAILSCALE_IP", "100.100.80.12")
_GCP_IP = os.getenv("GCP_SSH_HOST", "35.225.133.230")
_SSH_RASPI = os.path.expanduser("~/.ssh/id_rsa_raspberry")
_SSH_GCP = os.path.expanduser("~/.ssh/google_compute_engine")


def _quick_curl(url: str, timeout: int = 5) -> Optional[dict]:
    """Quick HTTP GET returning JSON or None."""
    try:
        r = _sp.run(["curl", "-s", "--connect-timeout", str(timeout), url],
                     capture_output=True, text=True, timeout=timeout + 2)
        if r.returncode == 0 and r.stdout.strip():
            return _json.loads(r.stdout)
    except Exception:
        pass
    return None


def _quick_ssh(host: str, key: str, user: str, cmd: str, timeout: int = 8) -> Optional[str]:
    """Quick SSH command returning stdout or None."""
    try:
        r = _sp.run(
            ["ssh", "-i", key, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
             f"{user}@{host}", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _check_raspi_reachable() -> bool:
    """Quick check if Raspi responds."""
    try:
        r = _sp.run(["curl", "-s", "--connect-timeout", "2", f"http://{_RASPI_TS}:5000/status"],
                     capture_output=True, text=True, timeout=4)
        return r.returncode == 0 and r.stdout.strip().startswith('{')
    except Exception:
        return False


def _slash_status():
    """Quick system status."""
    print(f"\n  {C_BOLD}🛡️ TokioAI Quick Status{C_RESET}\n")

    # Entity
    data = _quick_curl(f"http://{_RASPI_TS}:5000/status")
    if data:
        v = data.get("vision", {})
        fps = v.get('fps', '?')
        hailo = '✅' if v.get('hailo_available', v.get('hailo_active')) else '❌'
        persons = data.get('persons_detected', 0)
        emotion = data.get('emotion', '?')
        sec = '🔒' if data.get('security_connected') else '🔓'
        print(f"  👁️  Entity:  {C_BRIGHT_GREEN}✅{C_RESET} {fps} FPS | Hailo {hailo} | {persons} persons | {emotion} {sec}")
    else:
        print(f"  👁️  Entity:  {C_BRIGHT_RED}❌ offline{C_RESET}")

    # Drone
    data = _quick_curl(f"http://{_RASPI_TS}:5001/drone/status")
    if data:
        fly = "✈️ FLYING" if data.get("is_flying") else "🔋 grounded"
        print(f"  🚁 Drone:   {C_BRIGHT_GREEN}✅{C_RESET} {fly} | safety={data.get('safety_level','?')} | bat={data.get('battery','?')}%")
    else:
        print(f"  🚁 Drone:   {C_DIM}proxy off{C_RESET}")

    # GCP Agent
    out = _quick_ssh(_GCP_IP, _SSH_GCP, "osboxes", "curl -s http://127.0.0.1:8000/health 2>/dev/null")
    if out:
        print(f"  ☁️  GCP:     {C_BRIGHT_GREEN}✅{C_RESET} agent healthy")
    else:
        print(f"  ☁️  GCP:     {C_BRIGHT_RED}❌ unreachable{C_RESET}")

    # GCP Containers
    out = _quick_ssh(_GCP_IP, _SSH_GCP, "osboxes", "sudo docker ps --format '{{.Names}}' 2>/dev/null | wc -l")
    if out:
        print(f"  🐳 Docker:  {C_BRIGHT_GREEN}✅{C_RESET} {out.strip()} containers running")

    # Home Assistant
    data = _quick_curl(f"http://{_RASPI_TS}:5000/status")
    if data and data.get('ha_connected'):
        print(f"  🏠 HA:      {C_BRIGHT_GREEN}✅{C_RESET} connected")
    elif data:
        print(f"  🏠 HA:      {C_BRIGHT_YELLOW}⚠️ disconnected{C_RESET}")

    print()


def _slash_waf():
    """Quick WAF stats."""
    print(f"\n  {C_BOLD}🔥 WAF Defense{C_RESET}\n")
    out = _quick_ssh(_GCP_IP, _SSH_GCP, "osboxes",
        'curl -s -X POST http://127.0.0.1:8000/api/auth/login -H "Content-Type: application/json" '
        '-d \'{"username":"admin","password":"REDACTED_PASSWORD"}\' 2>/dev/null')
    if not out:
        print(f"  {C_BRIGHT_RED}❌ WAF API unreachable{C_RESET}\n")
        return
    try:
        token = _json.loads(out).get("token", "")
    except Exception:
        print(f"  {C_BRIGHT_RED}❌ WAF auth failed{C_RESET}\n")
        return
    out = _quick_ssh(_GCP_IP, _SSH_GCP, "osboxes",
        f'curl -s http://127.0.0.1:8000/api/summary -H "Authorization: Bearer {token}" 2>/dev/null')
    if not out:
        print(f"  {C_BRIGHT_RED}❌ WAF summary failed{C_RESET}\n")
        return
    try:
        d = _json.loads(out)
        total = d.get('total', 0)
        blocked = d.get('blocked', 0)
        block_rate = (blocked / total * 100) if total > 0 else 0
        print(f"  Total attacks:   {C_BOLD}{total:,}{C_RESET}")
        print(f"  Blocked:         {C_BOLD}{blocked:,}{C_RESET} ({block_rate:.1f}%)")
        print(f"  Active IP bans:  {C_BOLD}{d.get('active_blocks', 0)}{C_RESET}")
        print(f"  Unique IPs:      {d.get('unique_ips', 0):,}")
        print(f"  {C_BRIGHT_RED}Critical{C_RESET}:        {d.get('critical', 0):,}")
        print(f"  {C_BRIGHT_YELLOW}High{C_RESET}:            {d.get('high', 0):,}")
        print(f"  Medium:          {d.get('medium', 0):,}")
        print(f"  Low:             {d.get('low', 0):,}")
    except Exception:
        print(f"  {C_BRIGHT_RED}❌ Parse error{C_RESET}")
    print()


def _slash_health():
    """Quick health vitals."""
    print(f"\n  {C_BOLD}❤️ Health Vitals{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/health/status")
    if not data:
        print(f"  {C_BRIGHT_RED}❌ Health monitor offline{C_RESET}\n")
        return
    hr = data.get("heart_rate", "?")
    spo2 = data.get("spo2", "?")
    bp_sys = data.get("bp_sys", data.get("blood_pressure", {}).get("systolic", 0))
    bp_dia = data.get("bp_dia", data.get("blood_pressure", {}).get("diastolic", 0))
    connected = data.get("connected", False)
    steps = data.get("steps", 0)
    calories = data.get("calories", 0)
    battery = data.get("battery", "?")

    status = f"{C_BRIGHT_GREEN}✅ connected{C_RESET}" if connected else f"{C_BRIGHT_YELLOW}⏳ waiting{C_RESET}"
    print(f"  Watch:       {status} ({data.get('watch', '?')}) 🔋{battery}%")
    hr_color = C_BRIGHT_RED if isinstance(hr, (int, float)) and (hr > 120 or hr < 45) else C_BOLD
    hr_str = str(hr) if hr else "waiting..."
    print(f"  Heart Rate:  {hr_color}{hr_str}{C_RESET} bpm")
    spo2_color = C_BRIGHT_RED if isinstance(spo2, (int, float)) and spo2 < 92 else C_BOLD
    spo2_str = str(spo2) if spo2 else "waiting..."
    print(f"  SpO2:        {spo2_color}{spo2_str}{C_RESET}%")
    if bp_sys and bp_dia:
        bp_color = C_BRIGHT_RED if bp_sys > 140 else C_BOLD
        print(f"  Blood Press: {bp_color}{bp_sys}/{bp_dia}{C_RESET} mmHg")
    print(f"  Steps:       {steps:,} | Calories: {calories}")
    print()


def _slash_threats():
    """Quick threat level."""
    print(f"\n  {C_BOLD}⚠️ Threat Level{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/threat/status")
    if not data:
        print(f"  {C_DIM}Threat engine not responding{C_RESET}\n")
        return
    defcon = data.get("level", data.get("defcon", "?"))
    colors = {"1": C_BRIGHT_RED, "2": C_BRIGHT_RED, "3": C_BRIGHT_YELLOW, "4": C_BRIGHT_BLUE, "5": C_BRIGHT_GREEN}
    c = colors.get(str(defcon), C_DIM)
    level_name = data.get("level_name", "?")
    print(f"  DEFCON:  {c}{C_BOLD}{defcon}{C_RESET} — {level_name}")
    print(f"  Score:   {data.get('overall_score', data.get('score', '?'))}")

    # Correlations
    corr = data.get("active_correlations", [])
    if corr:
        print(f"  {C_BRIGHT_RED}⚡ Active Correlations:{C_RESET}")
        for cr in corr[:3]:
            print(f"    → {cr}")

    vecs = data.get("vectors", data.get("threat_vectors", {}))
    if vecs:
        print(f"  Vectors:")
        vec_icons = {"waf": "🛡️", "wifi": "📡", "ble": "📱", "vision": "👁️"}
        for v, info in vecs.items():
            icon = vec_icons.get(v, "•")
            name = info.get("name", v)
            score = info.get("score", "?")
            evts = info.get("events_1h", 0)
            detail = info.get("last_detail", "")
            line = f"    {icon} {name}: score {score}"
            if evts:
                line += f" ({evts} events/1h)"
            if detail:
                line += f" — {detail[:50]}"
            print(line)
    print()


def _slash_drone():
    """Quick drone status."""
    print(f"\n  {C_BOLD}🚁 Drone Status{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5001/drone/status")
    if not data:
        print(f"  {C_DIM}Drone proxy offline — Raspi may be down{C_RESET}\n")
        return
    connected = data.get("connected", False)
    flying = data.get("is_flying", False)
    bat = data.get("battery", "?")
    safety = data.get("safety_level", "?")
    geo = data.get("geofence", {})
    wifi = data.get("wifi_connected", False)

    if flying:
        print(f"  Status:    {C_BRIGHT_GREEN}✈️ FLYING{C_RESET}")
    elif connected:
        print(f"  Status:    {C_BRIGHT_GREEN}✅ connected{C_RESET}")
    elif wifi:
        print(f"  Status:    {C_BRIGHT_YELLOW}📶 WiFi connected, SDK pending{C_RESET}")
    else:
        print(f"  Status:    {C_DIM}❌ disconnected{C_RESET}")
    print(f"  Battery:   {bat}%")
    print(f"  Safety:    {safety}")
    if geo:
        print(f"  Geofence:  {geo.get('max_height', '?')}cm H × {geo.get('max_distance', '?')}cm D")
    fpv = data.get("fpv", {})
    if fpv:
        print(f"  FPV:       {'✅ active' if fpv.get('active') else '❌ off'}")
    print()


def _slash_containers():
    """Quick GCP container list."""
    print(f"\n  {C_BOLD}🐳 GCP Containers{C_RESET}\n")
    out = _quick_ssh(_GCP_IP, _SSH_GCP, "osboxes",
        "sudo docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null")
    if not out:
        print(f"  {C_BRIGHT_RED}❌ Cannot reach GCP{C_RESET}\n")
        return
    for line in out.strip().split('\n'):
        parts = line.split('|')
        if len(parts) >= 2:
            name = parts[0]
            status = parts[1]
            icon = "🟢" if "Up" in status else "🔴"
            # Clean up status
            status_clean = status.replace("(healthy)", f"{C_BRIGHT_GREEN}(healthy){C_RESET}")
            print(f"  {icon} {C_BRIGHT_CYAN}{name:<28}{C_RESET} {status_clean}")
    print()


def _slash_entity():
    """Quick entity status."""
    print(f"\n  {C_BOLD}👁️ Entity Status{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/status")
    if not data:
        print(f"  {C_BRIGHT_RED}❌ Entity offline{C_RESET}\n")
        return
    v = data.get("vision", {})
    print(f"  Camera:    {'✅' if v.get('camera_open', v.get('camera_ok')) else '❌'}")
    print(f"  Hailo:     {'✅ AI accelerated' if v.get('hailo_available', v.get('hailo_active')) else '❌ CPU only'}")
    print(f"  FPS:       {C_BOLD}{v.get('fps', '?')}{C_RESET}")
    print(f"  Persons:   {data.get('persons_detected', '?')}")
    print(f"  Emotion:   {data.get('emotion', '?')}")
    print(f"  Security:  {'✅ connected' if data.get('security_connected') else '❌ disconnected'}")
    print(f"  HA:        {'✅ connected' if data.get('ha_connected') else '❌ disconnected'}")
    ai = data.get("ai_brain", {})
    if ai:
        print(f"  AI Brain:  {'✅' if ai.get('active') else '❌'} | {ai.get('observations', 0)} observations")
    stand = data.get("stand_mode", {})
    if stand and stand.get("active"):
        print(f"  Stand:     {C_BRIGHT_YELLOW}✅ ACTIVE{C_RESET} | {stand.get('visitors', 0)} visitors")
    print()


def _slash_wifi():
    """Quick WiFi defense status."""
    print(f"\n  {C_BOLD}📡 WiFi Defense{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/wifi/status")
    if not data:
        print(f"  {C_DIM}WiFi defense not responding{C_RESET}\n")
        return
    monitoring = data.get("monitoring", False)
    counter = data.get("counter_deauth", data.get("counter_deauth_enabled", False))
    deauths = data.get("deauth_count", data.get("deauths_detected", 0))
    evil = data.get("evil_twins", data.get("evil_twins_detected", 0))
    channel = data.get("current_channel", "?")
    interface = data.get("interface", data.get("monitor_interface", "?"))

    print(f"  Monitoring:      {'✅ active' if monitoring else '❌ off'}")
    print(f"  Interface:       {interface}")
    print(f"  Channel:         {channel}")
    print(f"  Counter-deauth:  {'✅ ON' if counter else '❌ OFF'}")
    print(f"  Deauths found:   {C_BRIGHT_RED if deauths > 0 else C_DIM}{deauths}{C_RESET}")
    print(f"  Evil twins:      {C_BRIGHT_RED if evil > 0 else C_DIM}{evil}{C_RESET}")
    attacks = data.get("recent_attacks", [])
    if attacks:
        print(f"\n  Recent attacks:")
        for a in attacks[:5]:
            ts = a.get("time", a.get("timestamp", "?"))
            atype = a.get("type", "?")
            print(f"    {C_DIM}{ts}{C_RESET} {C_BRIGHT_RED}{atype}{C_RESET}")
    print()


def _slash_coffee():
    """Quick coffee machine status."""
    print(f"\n  {C_BOLD}☕ Coffee Machine{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/coffee/status")
    if not data:
        print(f"  {C_DIM}Coffee API not available{C_RESET}\n")
        return
    status = data.get("status", "unknown")
    ready = data.get("ready", False)
    last = data.get("last_brew", {})
    print(f"  Status:     {'✅ ready' if ready else f'⏳ {status}'}")
    if last:
        print(f"  Last brew:  {last.get('recipe', '?')} at {last.get('time', '?')}")
    recipes = data.get("recipes", [])
    if recipes:
        print(f"  Recipes:    {', '.join(recipes[:6])}")
    print()


def _slash_picar():
    """Quick PiCar-X robot status."""
    print(f"\n  {C_BOLD}🤖 PiCar-X Robot{C_RESET}\n")
    data = _quick_curl("http://192.168.8.107:5002/status")
    if not data:
        print(f"  {C_DIM}PiCar-X proxy not available (Raspi off?){C_RESET}\n")
        return
    hw = data.get("initialized", False)
    moving = data.get("moving", False)
    direction = data.get("direction", "stopped")
    speed = data.get("speed", 0)
    mode = data.get("autonomous_mode")
    ultra = data.get("ultrasonic_cm", -1)
    gs = data.get("grayscale", [])
    batt = data.get("battery_v", -1)
    pan = data.get("cam_pan", 0)
    tilt = data.get("cam_tilt", 0)
    cmds = data.get("commands", 0)
    print(f"  Hardware:   {'✅ online' if hw else '❌ not initialized'}")
    if moving:
        print(f"  Moving:     {C_BRIGHT_GREEN}🟢 {direction} @ {speed}%{C_RESET}")
    else:
        print(f"  Moving:     ⚪ stopped")
    if mode:
        print(f"  Auto mode:  {C_BRIGHT_YELLOW}{mode}{C_RESET}")
    print(f"  Camera:     pan={pan}° tilt={tilt}°")
    print(f"  Ultrasonic: {ultra} cm")
    print(f"  Grayscale:  {gs}")
    print(f"  Battery:    {batt}V")
    print(f"  Commands:   {cmds}")
    print()


def _slash_ha():
    """Quick Home Assistant status."""
    print(f"\n  {C_BOLD}🏠 Home Assistant{C_RESET}\n")
    # Try via Entity API
    data = _quick_curl(f"http://{_RASPI_TS}:5000/status")
    if data:
        ha = data.get("ha_connected", False)
        print(f"  Connected:  {'✅' if ha else '❌'}")
    
    # Try direct HA API
    ha_data = _quick_curl(f"http://{_RASPI_TS}:8123/api/", timeout=3)
    if ha_data:
        print(f"  HA API:     {C_BRIGHT_GREEN}✅ responding{C_RESET}")
    else:
        # Check if Docker container is running
        out = _quick_ssh(_RASPI_TS, _SSH_RASPI, "mrmoz", "docker ps --filter name=homeassistant --format '{{.Status}}' 2>/dev/null", timeout=5)
        if out:
            print(f"  Container:  {out}")
        else:
            print(f"  HA API:     {C_BRIGHT_RED}❌ not responding{C_RESET}")
    print()


def _slash_logs():
    """Quick logs from Entity."""
    print(f"\n  {C_BOLD}📋 Recent Entity Logs{C_RESET}\n")
    out = _quick_ssh(_RASPI_TS, _SSH_RASPI, "mrmoz",
        "journalctl -u tokio-entity --no-pager -n 15 --output=short 2>/dev/null || tail -15 /tmp/tokio_entity.log 2>/dev/null",
        timeout=8)
    if not out:
        print(f"  {C_DIM}No logs available{C_RESET}\n")
        return
    for line in out.strip().split('\n')[-15:]:
        # Color errors/warnings
        if 'error' in line.lower() or 'exception' in line.lower():
            print(f"  {C_BRIGHT_RED}{line[:120]}{C_RESET}")
        elif 'warning' in line.lower():
            print(f"  {C_BRIGHT_YELLOW}{line[:120]}{C_RESET}")
        else:
            print(f"  {C_DIM}{line[:120]}{C_RESET}")
    print()


def _slash_see():
    """Quick snapshot from Entity camera."""
    print(f"\n  {C_BOLD}📸 Camera Snapshot{C_RESET}\n")
    data = _quick_curl(f"http://{_RASPI_TS}:5000/status")
    if not data:
        print(f"  {C_BRIGHT_RED}❌ Entity offline{C_RESET}\n")
        return
    persons = data.get('persons_detected', 0)
    emotion = data.get('emotion', '?')
    ai = data.get("ai_brain", {})
    last_analysis = ai.get("last_analysis", "")
    
    print(f"  Persons:   {persons}")
    print(f"  Emotion:   {emotion}")
    if last_analysis:
        # Truncate long analysis
        if len(last_analysis) > 200:
            last_analysis = last_analysis[:200] + "..."
        print(f"  AI sees:   {C_ITALIC}{last_analysis}{C_RESET}")
    print(f"\n  {C_DIM}Use 'que ves?' for full AI analysis{C_RESET}")
    print()


def _slash_sitrep():
    """Full SITREP — calls all slash commands."""
    print(f"\n  {C_BOLD}{C_BRIGHT_WHITE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"  {C_BOLD}{C_BRIGHT_WHITE}  SITREP — Full Situation Report{C_RESET}")
    print(f"  {C_BOLD}{C_BRIGHT_WHITE}  {time.strftime('%Y-%m-%d %H:%M:%S')}{C_RESET}")
    print(f"  {C_BOLD}{C_BRIGHT_WHITE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    _slash_status()
    _slash_threats()
    _slash_waf()
    _slash_health()
    _slash_wifi()


_SLASH_COMMANDS = {
    "/status": _slash_status,
    "/waf": _slash_waf,
    "/health": _slash_health,
    "/threats": _slash_threats,
    "/drone": _slash_drone,
    "/containers": _slash_containers,
    "/entity": _slash_entity,
    "/sitrep": _slash_sitrep,
    "/wifi": _slash_wifi,
    "/coffee": _slash_coffee,
    "/ha": _slash_ha,
    "/logs": _slash_logs,
    "/see": _slash_see,
    "/picar": _slash_picar,
}


# ─── Agent Factory ───────────────────────────────────

def create_agent():
    """Create a TokioAgent instance."""
    from tokio_agent.engine.agent import TokioAgent
    from tokio_agent.engine.memory.workspace import Workspace
    from tokio_agent.engine.llm import create_llm

    workspace = Workspace()
    llm = create_llm()
    agent = TokioAgent(llm=llm, workspace=workspace)
    return agent


# ─── Token Cost Estimation ───────────────────────────

# Approximate costs per 1M tokens (USD)
_MODEL_COSTS = {
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
}

def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> str:
    """Estimate cost based on model and tokens."""
    costs = None
    for key, val in _MODEL_COSTS.items():
        if key in model.lower():
            costs = val
            break
    if not costs:
        costs = _MODEL_COSTS["claude-sonnet-4"]
    
    cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


# ─── Status Bar ──────────────────────────────────────

def format_status(agent, elapsed: float, tool_count: int = 0) -> str:
    """Format a rich status bar after each response."""
    stats = agent.get_stats()
    parts = []

    # Time
    if elapsed >= 60:
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        parts.append(f"⏱ {mins}m{secs}s")
    else:
        parts.append(f"⏱ {elapsed:.1f}s")

    # Tokens
    total_tokens = stats.get("total_tokens", 0)
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("output_tokens", 0)
    if total_tokens > 1_000_000:
        parts.append(f"📊 {total_tokens/1_000_000:.1f}M tokens")
    elif total_tokens > 1000:
        parts.append(f"📊 {total_tokens/1000:.0f}K tokens")

    # Tools
    tools_used = tool_count or stats.get("tools_executed", 0)
    if tools_used > 0:
        parts.append(f"🔧 {tools_used} tools")

    # Compactions
    compactions = stats.get("compactions", 0)
    if compactions > 0:
        parts.append(f"📦 {compactions} compactions")

    # Memories
    mem = stats.get("auto_memory", {})
    if mem.get("total_memories_saved", 0) > 0:
        parts.append(f"🧠 {mem['total_memories_saved']} memories")

    # Cost estimation
    model = stats.get("llm", "")
    if input_tokens > 0 or output_tokens > 0:
        cost = _estimate_cost(model, input_tokens, output_tokens)
        parts.append(f"💰 ~{cost}")

    return " │ ".join(parts)


# ─── Help ────────────────────────────────────────────

def show_help(agent):
    w = min(_term_width() - 4, 60)
    print(f"""
{C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}
{C_BOLD} TokioAI CLI v3.3 — Help{C_RESET}
{C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}

{C_BOLD}Commands:{C_RESET}
  {C_BRIGHT_CYAN}exit{C_RESET}, {C_BRIGHT_CYAN}quit{C_RESET}       Exit CLI
  {C_BRIGHT_CYAN}reset{C_RESET}             New session
  {C_BRIGHT_CYAN}stats{C_RESET}             Show statistics
  {C_BRIGHT_CYAN}tools{C_RESET}             List available tools
  {C_BRIGHT_CYAN}model{C_RESET}             Show current LLM
  {C_BRIGHT_CYAN}clear{C_RESET}             Clear screen
  {C_BRIGHT_CYAN}unlimited{C_RESET}         Toggle unlimited mode
  {C_BRIGHT_CYAN}persistent{C_RESET}        Toggle persistent mode
  {C_BRIGHT_CYAN}config{C_RESET}            Show configuration

{C_BOLD}Quick Commands (instant, no LLM):{C_RESET}
  {C_BRIGHT_GREEN}/status{C_RESET}           System overview
  {C_BRIGHT_GREEN}/sitrep{C_RESET}           Full situation report
  {C_BRIGHT_GREEN}/waf{C_RESET}              WAF attack stats
  {C_BRIGHT_GREEN}/health{C_RESET}           Health vitals (HR, SpO2, BP)
  {C_BRIGHT_GREEN}/threats{C_RESET}          DEFCON threat level
  {C_BRIGHT_GREEN}/drone{C_RESET}            Drone status
  {C_BRIGHT_GREEN}/entity{C_RESET}           Entity vision status
  {C_BRIGHT_GREEN}/containers{C_RESET}       GCP Docker containers
  {C_BRIGHT_GREEN}/see{C_RESET}              Camera snapshot + AI analysis
  {C_BRIGHT_GREEN}/wifi{C_RESET}             WiFi defense status
  {C_BRIGHT_GREEN}/coffee{C_RESET}           Coffee machine status
  {C_BRIGHT_GREEN}/ha{C_RESET}               Home Assistant status
  {C_BRIGHT_GREEN}/picar{C_RESET}            PiCar-X robot status
  {C_BRIGHT_GREEN}/logs{C_RESET}             Recent Entity logs

{C_BOLD}Shortcuts:{C_RESET}
  {C_BRIGHT_YELLOW}Tab{C_RESET}               Autocomplete commands
  {C_BRIGHT_YELLOW}Escape{C_RESET}            Cancel current request
  {C_BRIGHT_YELLOW}Ctrl+C{C_RESET}            Cancel / Exit
  {C_BRIGHT_YELLOW}\\\\{C_RESET} (at end)        Multi-line input

{C_BOLD}Skills:{C_RESET}
{agent.skill_registry.format_help()}

{C_BOLD}Examples:{C_RESET}
  {C_GRAY}scan the network for open ports{C_RESET}
  {C_GRAY}check all docker containers on GCP{C_RESET}
  {C_GRAY}create a PDF report of WAF logs{C_RESET}
  {C_GRAY}conectate al drone y despegalo{C_RESET}
  {C_GRAY}/sitrep{C_RESET}
{C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}
""")


# ─── Banner ──────────────────────────────────────────

def show_banner(mode_parts: list, agent=None):
    """Show the startup banner with gradient colors."""

    g = [196, 202, 208, 214, 220, 226]  # Red to yellow gradient

    banner_lines = [
        "████████╗ ██████╗ ██╗  ██╗██╗ ██████╗ ",
        "╚══██╔══╝██╔═══██╗██║ ██╔╝██║██╔═══██╗",
        "   ██║   ██║   ██║█████╔╝ ██║██║   ██║",
        "   ██║   ██║   ██║██╔═██╗ ██║██║   ██║",
        "   ██║   ╚██████╔╝██║  ██╗██║╚██████╔╝",
        "   ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝",
    ]

    print()
    for i, line in enumerate(banner_lines):
        color = c256(g[i % len(g)])
        print(f"    {color}{line}{C_RESET}")

    # Version & model info
    model_name = "Claude Sonnet 4"
    tools_count = 0
    if agent:
        try:
            stats = agent.get_stats()
            model_name = stats.get("llm", model_name)
            tools_count = stats.get("tools_count", 0)
        except Exception:
            pass

    print(f"    {C_BOLD}{C_BRIGHT_WHITE}  Autonomous AI Agent{C_RESET} {C_GRAY}v3.3{C_RESET}")
    print(f"    {C_GRAY}  {model_name} • {tools_count} tools • {' • '.join(mode_parts)}{C_RESET}")
    print()
    print(f"    {C_GRAY}  Type {C_BRIGHT_CYAN}help{C_GRAY} for commands • {C_BRIGHT_YELLOW}Esc{C_GRAY} to cancel • {C_BRIGHT_YELLOW}Tab{C_GRAY} to complete{C_RESET}")
    print()


# ─── Stream Processing ──────────────────────────────

async def process_streaming(agent, user_input: str, session_id: str):
    """Process a message with streaming output and rich formatting."""
    cancel_event = asyncio.Event()
    spinner = Spinner("thinking...")
    t0 = time.time()

    streaming_text = False
    ever_streamed = False
    collected_text = []
    tool_count = 0
    current_round = 1
    old_settings = None
    _cbreak_active = False

    def _enter_cbreak():
        nonlocal old_settings, _cbreak_active
        if _cbreak_active or _IS_WINDOWS:
            return
        try:
            if sys.stdin.isatty():
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())
                _cbreak_active = True
        except Exception:
            pass

    def _exit_cbreak():
        nonlocal _cbreak_active
        if not _cbreak_active or _IS_WINDOWS:
            return
        _cbreak_active = False  # mark first to prevent re-entry
        try:
            if old_settings and sys.stdin.isatty():
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception:
            pass
        try:
            if sys.stdin.isatty():
                os.system("stty sane 2>/dev/null")
        except Exception:
            pass

    _enter_cbreak()

    async def _check_cancel():
        while not cancel_event.is_set():
            if _cbreak_active and _check_escape():
                cancel_event.set()
                spinner.stop()
                sys.stdout.write(f"\n  {C_BRIGHT_YELLOW}⛔ Cancelling...{C_RESET}\n")
                sys.stdout.flush()
                break
            await asyncio.sleep(0.1)  # reduced polling frequency

    cancel_task = asyncio.create_task(_check_cancel())

    stream_gen = None
    try:
        spinner.start("thinking...")

        stream_gen = agent.process_message_stream(
            user_message=user_input,
            session_id=session_id,
            cancel_event=cancel_event,
        )

        async for event_type, data in stream_gen:
            if event_type == "thinking":
                current_round = data
                if data > 1:
                    spinner.update(f"round {data}...")
                else:
                    spinner.start("thinking...")

            elif event_type == "token":
                if not streaming_text:
                    spinner.stop()
                    streaming_text = True
                    ever_streamed = True
                    sys.stdout.write("\n")
                sys.stdout.write(data)
                sys.stdout.flush()
                collected_text.append(data)

            elif event_type == "preparing":
                if streaming_text:
                    sys.stdout.write("\n")
                    streaming_text = False
                    collected_text = []
                spinner.start("preparing...")

            elif event_type == "tool_start":
                name, args = data
                spinner.stop()
                if streaming_text:
                    sys.stdout.write("\n")
                    streaming_text = False
                    collected_text = []
                tool_count += 1
                print(_format_tool_start(name, args, tool_count))
                spinner.start(f"running {name}...")

            elif event_type == "tool_end":
                name, output = data
                spinner.stop()
                is_error = False
                if output and output.strip():
                    lower_out = output.strip().lower()[:100]
                    is_error = any(e in lower_out for e in ['error', 'traceback', 'exception', 'failed', 'permission denied'])
                print(_format_tool_result(name, output, not is_error))
                spinner.start("thinking...")

            elif event_type == "text":
                spinner.stop()
                if not streaming_text and not ever_streamed:
                    rendered = MarkdownRenderer.render(data)
                    print(f"\n{rendered}")

            elif event_type == "done":
                spinner.stop()
                if not streaming_text and not ever_streamed and data:
                    rendered = MarkdownRenderer.render(data)
                    print(f"\n{rendered}")
                elif streaming_text:
                    sys.stdout.write("\n")

            elif event_type == "error":
                spinner.stop()
                print(f"\n  {C_BRIGHT_RED}✗ Error: {data}{C_RESET}")

        elapsed = time.time() - t0
        status = format_status(agent, elapsed, tool_count)
        print(f"\n  {C_GRAY}{status}{C_RESET}")

    except KeyboardInterrupt:
        spinner.stop()
        cancel_event.set()
        print(f"\n  {C_BRIGHT_YELLOW}⛔ Cancelled{C_RESET}")

    except Exception as e:
        spinner.stop()
        print(f"\n  {C_BRIGHT_RED}✗ Unexpected error: {e}{C_RESET}")

    finally:
        # CRITICAL: restore terminal FIRST, before anything else
        cancel_event.set()
        _exit_cbreak()

        spinner.stop()

        if stream_gen is not None:
            try:
                await stream_gen.aclose()
            except Exception:
                pass

        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass

        # Triple-check: flush + stty sane
        _flush_stdin()
        if not _IS_WINDOWS and sys.stdin.isatty():
            try:
                os.system("stty sane 2>/dev/null")
            except Exception:
                pass


# ─── Config Command ─────────────────────────────────

def _show_config():
    """Show current CLI configuration."""
    w = min(_term_width() - 4, 55)
    print(f"\n  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
    print(f"  {C_BOLD}⚙️  Configuration{C_RESET}")
    print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
    
    # Hosts
    print(f"\n  {C_BOLD}Hosts:{C_RESET}")
    print(f"  {C_GRAY}Raspi LAN:{C_RESET}      {_RASPI_IP or C_DIM + 'not set' + C_RESET}")
    print(f"  {C_GRAY}Raspi TS:{C_RESET}       {_RASPI_TS or C_DIM + 'not set' + C_RESET}")
    print(f"  {C_GRAY}GCP:{C_RESET}            {_GCP_IP or C_DIM + 'not set' + C_RESET}")
    
    # SSH Keys
    print(f"\n  {C_BOLD}SSH Keys:{C_RESET}")
    raspi_key = '✅' if os.path.exists(_SSH_RASPI) else '❌'
    gcp_key = '✅' if os.path.exists(_SSH_GCP) else '❌'
    print(f"  {C_GRAY}Raspi:{C_RESET}          {raspi_key} {_SSH_RASPI}")
    print(f"  {C_GRAY}GCP:{C_RESET}            {gcp_key} {_SSH_GCP}")
    
    # Env file
    env_file = os.path.expanduser("~/.tokioai/.env")
    env_exists = os.path.exists(env_file)
    print(f"\n  {C_BOLD}Environment:{C_RESET}")
    print(f"  {C_GRAY}.env file:{C_RESET}      {'✅' if env_exists else '❌'} {env_file}")
    
    # Model
    model = os.getenv("TOKIOAI_MODEL", "sonnet")
    print(f"  {C_GRAY}Model:{C_RESET}          {model}")
    
    # Session
    print(f"\n  {C_BOLD}Session:{C_RESET}")
    print(f"  {C_GRAY}History:{C_RESET}        {HISTORY_FILE}")
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                count = sum(1 for _ in f)
            print(f"  {C_GRAY}History size:{C_RESET}   {count} entries")
        except Exception:
            pass
    
    print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}\n")


# ─── Multi-line Input ────────────────────────────────

def _read_multiline(first_line: str) -> str:
    """Handle multi-line input with \\ continuation."""
    if not first_line.endswith('\\'):
        return first_line
    
    lines = [first_line[:-1]]  # Remove trailing backslash
    while True:
        try:
            continuation = input(f"  {C_DIM}...{C_RESET} ")
            if continuation.endswith('\\'):
                lines.append(continuation[:-1])
            else:
                lines.append(continuation)
                break
        except (EOFError, KeyboardInterrupt):
            break
    
    return '\n'.join(lines)


# ─── Main Loop ───────────────────────────────────────

async def run_interactive(session_id: Optional[str] = None, max_rounds: int = 25,
                          max_time: int = 600, persistent: bool = False):
    """Run the interactive CLI loop."""

    mode_parts = ["Streaming", "Native Tools", "Auto-compact"]
    if max_rounds == 0:
        mode_parts.append("Unlimited")
    if persistent:
        mode_parts.append("Persistent")

    _load_history()
    agent = create_agent()

    # Populate tool names for tab completion
    global _TOOL_NAMES
    try:
        _TOOL_NAMES = sorted(agent.registry.list_names())
    except Exception:
        pass

    show_banner(mode_parts, agent)

    # Session resumption
    sid = session_id
    resumed = False
    if not sid:
        prev = _load_session_state()
        if prev and prev.get("messages"):
            prev_sid = prev["session_id"]
            prev_ts = prev.get("timestamp", "?")
            prev_msgs = prev["messages"]
            print(f"  {C_BRIGHT_YELLOW}📋 Previous session found ({prev_ts}):{C_RESET}")
            for msg in prev_msgs[-4:]:
                role = msg.get("role", "?")
                content = msg.get("content", "")[:100]
                icon = f"{C_BRIGHT_CYAN}▸{C_RESET}" if role == "user" else f"{C_BRIGHT_GREEN}◂{C_RESET}"
                print(f"    {icon} {C_GRAY}{content}{'…' if len(msg.get('content', '')) > 100 else ''}{C_RESET}")
            print(f"  {C_GRAY}Enter = resume │ 'new' = fresh session{C_RESET}")
            try:
                choice = input(f"  ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if choice not in ("new", "nueva", "n"):
                sid = prev_sid
                resumed = True
                for msg in prev_msgs:
                    agent.session_manager.add_message(sid, msg["role"], msg["content"])
                print(f"  {C_BRIGHT_GREEN}✓ Session restored{C_RESET}\n")
            else:
                print(f"  {C_GRAY}New session.{C_RESET}\n")
        if not sid:
            sid = f"cli-{int(time.time())}"

    agent.MAX_TOOL_ROUNDS = max_rounds
    agent.MAX_TOTAL_TIME = max_time
    _persistent_mode = persistent

    if max_rounds == 0:
        print(f"  {C_BRIGHT_YELLOW}∞ Unlimited mode: no round or time limits{C_RESET}")
    if _persistent_mode:
        print(f"  {C_BRIGHT_YELLOW}🔄 Persistent mode: will keep working until you type 'stop'{C_RESET}")

    _clean_term = None
    try:
        if sys.stdin.isatty() and not _IS_WINDOWS:
            _clean_term = termios.tcgetattr(sys.stdin)
    except Exception:
        pass

    while True:
        # Restore terminal to clean state before every prompt
        if _clean_term:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _clean_term)
            except Exception:
                pass
        if not _IS_WINDOWS and sys.stdin.isatty():
            try:
                os.system("stty sane 2>/dev/null")
            except Exception:
                pass
        _flush_stdin()

        try:
            if _IS_WINDOWS:
                prompt = f"\n{C_BOLD}{C_BRIGHT_CYAN}❯{C_RESET} "
            else:
                prompt = f"\n\001{C_BOLD}{C_BRIGHT_CYAN}\002❯\001{C_RESET}\002 "
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_GRAY}Bye! 👋{C_RESET}")
            break

        if not user_input:
            continue

        # Handle multi-line input
        user_input = _read_multiline(user_input)

        _save_history()

        # Built-in commands
        lower = user_input.lower()
        if lower in ("exit", "quit", "q"):
            print(f"{C_GRAY}Bye! 👋{C_RESET}")
            break
        if lower == "help":
            show_help(agent)
            continue
        if lower == "reset":
            sid = f"cli-{int(time.time())}"
            print(f"  {C_BRIGHT_GREEN}✓ Session reset: {sid}{C_RESET}")
            continue
        if lower == "unlimited":
            if agent.MAX_TOOL_ROUNDS == 0:
                agent.MAX_TOOL_ROUNDS = 25
                agent.MAX_TOTAL_TIME = 600
                print(f"  {C_BRIGHT_GREEN}✓ Normal mode: 25 rounds, 10min max{C_RESET}")
            else:
                agent.MAX_TOOL_ROUNDS = 0
                agent.MAX_TOTAL_TIME = 0
                print(f"  {C_BRIGHT_YELLOW}∞ Unlimited mode: no limits{C_RESET}")
            continue
        if lower == "persistent":
            _persistent_mode = not _persistent_mode
            if _persistent_mode:
                agent.MAX_TOOL_ROUNDS = 0
                agent.MAX_TOTAL_TIME = 0
                print(f"  {C_BRIGHT_YELLOW}🔄 Persistent ON — will keep working until 'stop'{C_RESET}")
            else:
                agent.MAX_TOOL_ROUNDS = 25
                agent.MAX_TOTAL_TIME = 600
                print(f"  {C_BRIGHT_GREEN}✓ Persistent OFF — back to 25 rounds{C_RESET}")
            continue
        if lower == "stop" and _persistent_mode:
            _persistent_mode = False
            agent.MAX_TOOL_ROUNDS = 25
            agent.MAX_TOTAL_TIME = 600
            print(f"  {C_BRIGHT_GREEN}✓ Stopped. Normal mode: 25 rounds{C_RESET}")
            continue
        if lower == "config":
            _show_config()
            continue
        if lower == "stats":
            stats = agent.get_stats()
            mode = "UNLIMITED" if agent.MAX_TOOL_ROUNDS == 0 else f"{agent.MAX_TOOL_ROUNDS} rounds"
            w = min(_term_width() - 4, 50)
            print(f"\n  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            print(f"  {C_BOLD}📊 Statistics{C_RESET}")
            print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            print(f"  {C_BRIGHT_CYAN}🧠{C_RESET} LLM:          {stats.get('llm', '?')}")
            print(f"  {C_BRIGHT_CYAN}⚙️{C_RESET}  Mode:         {mode}{' + PERSISTENT' if _persistent_mode else ''}")
            print(f"  {C_BRIGHT_CYAN}🔧{C_RESET} Tools:         {stats.get('tools_count', 0)} available")
            print(f"  {C_BRIGHT_CYAN}💬{C_RESET} Messages:      {stats.get('messages_processed', 0)}")
            print(f"  {C_BRIGHT_CYAN}⚡{C_RESET} Tools used:    {stats.get('tools_executed', 0)}")
            total_tokens = stats.get('total_tokens', 0)
            input_tokens = stats.get('input_tokens', 0)
            output_tokens = stats.get('output_tokens', 0)
            print(f"  {C_BRIGHT_CYAN}📊{C_RESET} Tokens:        {total_tokens:,} (in:{input_tokens:,} out:{output_tokens:,})")
            print(f"  {C_BRIGHT_CYAN}📦{C_RESET} Compactions:   {stats.get('compactions', 0)}")
            print(f"  {C_BRIGHT_CYAN}🧠{C_RESET} Memories:      {stats.get('memories_extracted', 0)}")
            # Cost estimate
            model = stats.get('llm', '')
            cost = _estimate_cost(model, input_tokens, output_tokens)
            print(f"  {C_BRIGHT_CYAN}💰{C_RESET} Est. cost:     ~{cost}")
            print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            continue
        if lower == "tools":
            names = sorted(agent.registry.list_names())
            w = min(_term_width() - 4, 60)
            print(f"\n  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            print(f"  {C_BOLD}🔧 {len(names)} Tools Available{C_RESET}")
            print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            for n in names:
                t = agent.registry.get(n)
                icon = TOOL_ICONS.get(n, "🔧")
                desc = t.description[:55] if t else ""
                print(f"  {icon} {C_BRIGHT_CYAN}{n:<22}{C_RESET} {C_GRAY}{desc}{C_RESET}")
            print(f"  {C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}")
            continue
        if lower == "model":
            stats = agent.get_stats()
            print(f"\n  {C_BRIGHT_CYAN}🧠{C_RESET} Current model: {C_BOLD}{stats.get('llm', '?')}{C_RESET}")
            continue
        if lower == "clear":
            os.system("cls" if _IS_WINDOWS else "clear")
            continue

        # Slash commands (instant, no LLM)
        if lower in _SLASH_COMMANDS:
            try:
                _SLASH_COMMANDS[lower]()
            except Exception as e:
                print(f"  {C_BRIGHT_RED}✗ {e}{C_RESET}")
            continue

        # Process with streaming
        await process_streaming(agent, user_input, sid)

        # Save session state
        session = agent.session_manager.get_session(sid)
        if session:
            _save_session_state(sid, session.get("messages", []))

        # Persistent mode
        if _persistent_mode:
            while _persistent_mode:
                try:
                    if _IS_WINDOWS:
                        follow_prompt = f"\n  {C_GRAY}[persistent]{C_RESET} {C_BOLD}{C_BRIGHT_CYAN}❯{C_RESET} "
                    else:
                        follow_prompt = f"\n  \001{C_GRAY}\002[persistent]\001{C_RESET}\002 \001{C_BOLD}{C_BRIGHT_CYAN}\002❯\001{C_RESET}\002 "
                    follow_up = input(follow_prompt).strip()
                except (EOFError, KeyboardInterrupt):
                    _persistent_mode = False
                    print(f"\n  {C_BRIGHT_GREEN}✓ Persistent mode stopped.{C_RESET}")
                    break
                if not follow_up:
                    follow_up = "Continua con la tarea. Si terminaste, dime que esta listo."
                if follow_up.lower() in ("stop", "parar", "detener", "exit"):
                    _persistent_mode = False
                    agent.MAX_TOOL_ROUNDS = 25
                    agent.MAX_TOTAL_TIME = 600
                    print(f"  {C_BRIGHT_GREEN}✓ Persistent stopped. Back to 25 rounds.{C_RESET}")
                    break
                _save_history()
                await process_streaming(agent, follow_up, sid)

    _save_history()


async def run_single(query: str, session_id: Optional[str] = None):
    """Run a single command with streaming and exit."""
    agent = create_agent()
    sid = session_id or "cli-oneshot"

    print(f"\n  {C_BOLD}{C_BRIGHT_CYAN}❯{C_RESET} {query}\n")
    await process_streaming(agent, query, sid)


def main():
    """Entry point for the CLI."""
    # Enable ANSI escape codes on Windows 10+
    if _IS_WINDOWS:
        os.system("")
    import argparse

    parser = argparse.ArgumentParser(
        description="TokioAI CLI v3.3 — Autonomous AI Agent",
    )
    parser.add_argument("query", nargs="*", help="Query to run (omit for interactive mode)")
    parser.add_argument("--session", "-s", default=None, help="Session ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--max-rounds", type=int, default=25,
                        help="Max tool rounds per message (0 = unlimited, default: 25)")
    parser.add_argument("--max-time", type=int, default=600,
                        help="Max seconds per message (0 = unlimited, default: 600)")
    parser.add_argument("--unlimited", "-u", action="store_true",
                        help="No limits on rounds or time")
    parser.add_argument("--persistent", "-p", action="store_true",
                        help="Persistent mode: keeps working until you say 'stop'")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    max_rounds = 0 if args.unlimited else args.max_rounds
    max_time = 0 if args.unlimited else args.max_time
    persistent = args.persistent
    if persistent:
        max_rounds = 0
        max_time = 0

    try:
        if args.query:
            query = " ".join(args.query)
            asyncio.run(run_single(query, args.session))
        else:
            asyncio.run(run_interactive(args.session, max_rounds, max_time, persistent))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        # Restore terminal to sane state (in case of crash during raw mode)
        if not _IS_WINDOWS:
            try:
                os.system("stty sane 2>/dev/null")
            except Exception:
                pass
        _save_history()
        print(f"\n{C_GRAY}Bye! 👋{C_RESET}")


if __name__ == "__main__":
    main()
