#!/usr/bin/env python3
"""
TokioAI Interactive CLI — Pro Terminal Interface.

Features:
- Rich markdown rendering (headers, bold, code blocks, tables, lists)
- Gradient banner with system info
- Styled tool calls with icons & progress
- Status bar with token/tool/time tracking
- Streaming responses (token by token)
- Escape to cancel running requests
- readline history & session persistence

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
import re
import readline
import select
import signal
import sys
import termios
import time
import tty
import warnings
from typing import Optional

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


def _load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            readline.read_history_file(HISTORY_FILE)
        # Let readline wrap long lines normally — \001/\002 markers handle width calc
        readline.parse_and_bind('set horizontal-scroll-mode off')
        # Better paste handling — treat pasted text as a single unit
        readline.parse_and_bind('set enable-bracketed-paste on')
        # Don't ring bell on wrap/complete
        readline.parse_and_bind('set bell-style none')
        # Ensure proper multi-line editing
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
    import json
    try:
        recent = last_messages[-20:] if last_messages else []
        state = {
            "session_id": sid,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages": recent,
        }
        with open(SESSION_FILE, "w") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _load_session_state():
    """Load previous session state from disk."""
    import json
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r") as f:
                state = json.load(f)
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

    # Patterns for inline rendering
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
            if '|' in line and line.strip().startswith('|'):
                cells = [c.strip() for c in line.strip('|').split('|')]
                if all(re.match(r'^[-:]+$', c) for c in cells if c):
                    # Separator row
                    result.append(f"  {C_DIM}{'─' * 50}{C_RESET}")
                    continue
                row_parts = []
                for cell in cells:
                    rendered = cls._render_inline(cell)
                    row_parts.append(f" {rendered} ")
                result.append(f"  {C_DIM}│{C_RESET}{f'{C_DIM}│{C_RESET}'.join(row_parts)}{C_DIM}│{C_RESET}")
                continue

            # Normal text with inline rendering
            rendered = cls._render_inline(line)
            result.append(rendered)

        return '\n'.join(result)

    @classmethod
    def _render_inline(cls, text: str) -> str:
        """Apply inline markdown formatting."""
        # Links [text](url)
        text = cls._LINK.sub(f'{C_UNDERLINE}{C_BRIGHT_BLUE}\\1{C_RESET}{C_DIM} (\\2){C_RESET}', text)
        # Bold
        text = cls._BOLD.sub(f'{C_BOLD}\\1{C_RESET}', text)
        # Italic
        text = cls._ITALIC.sub(f'{C_ITALIC}\\1{C_RESET}', text)
        # Inline code
        text = cls._INLINE_CODE.sub(f'{C_BG_GRAY}{C_BRIGHT_GREEN} \\1 {C_RESET}', text)
        # Strikethrough
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
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            return ch == '\x1b'
    except Exception:
        pass
    return False


def _flush_stdin():
    if not sys.stdin.isatty():
        return
    try:
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
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP]'),
    (re.compile(r'\b\w+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[USER@HOST]'),
    (re.compile(r'\b\w+@[\w.-]+\.\w+\b'), '[USER@HOST]'),
    (re.compile(r'ssh\s+-i\s+\S+'), 'ssh -i [KEY]'),
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
    """Format tool result with visual indicator."""
    if not output or not output.strip():
        return f"    {C_BRIGHT_GREEN}✓{C_RESET} {C_GRAY}done{C_RESET}"

    preview = _mask_sensitive(output.strip().replace("\n", " ")[:150])
    truncated = '…' if len(output.strip()) > 150 else ''

    if success:
        return f"    {C_BRIGHT_GREEN}✓{C_RESET} {C_GRAY}{preview}{truncated}{C_RESET}"
    else:
        return f"    {C_BRIGHT_RED}✗{C_RESET} {C_GRAY}{preview}{truncated}{C_RESET}"


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

    return " │ ".join(parts)


# ─── Help ────────────────────────────────────────────

def show_help(agent):
    w = min(_term_width() - 4, 60)
    print(f"""
{C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}
{C_BOLD} TokioAI CLI — Help{C_RESET}
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

{C_BOLD}Shortcuts:{C_RESET}
  {C_BRIGHT_YELLOW}Escape{C_RESET}            Cancel current request
  {C_BRIGHT_YELLOW}Ctrl+C{C_RESET}            Cancel / Exit

{C_BOLD}Skills:{C_RESET}
{agent.skill_registry.format_help()}

{C_BOLD}Examples:{C_RESET}
  {C_GRAY}scan the network for open ports{C_RESET}
  {C_GRAY}check all docker containers on GCP{C_RESET}
  {C_GRAY}create a PDF report of WAF logs{C_RESET}
  {C_GRAY}/status{C_RESET}
{C_BOLD}{C_BRIGHT_CYAN}{'─' * w}{C_RESET}
""")


# ─── Banner ──────────────────────────────────────────

def show_banner(mode_parts: list, agent=None):
    """Show the startup banner with gradient colors."""

    # Gradient colors for TOKIO letters
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
    if agent:
        try:
            stats = agent.get_stats()
            model_name = stats.get("llm", model_name)
        except Exception:
            pass

    tools_count = 0
    if agent:
        try:
            tools_count = stats.get("tools_count", 0)
        except Exception:
            pass

    print(f"    {C_BOLD}{C_BRIGHT_WHITE}  Autonomous AI Agent{C_RESET} {C_GRAY}v3.0{C_RESET}")
    print(f"    {C_GRAY}  {model_name} • {tools_count} tools • {' • '.join(mode_parts)}{C_RESET}")
    print()
    print(f"    {C_GRAY}  Type {C_BRIGHT_CYAN}help{C_GRAY} for commands • {C_BRIGHT_YELLOW}Esc{C_GRAY} to cancel • {C_BRIGHT_YELLOW}Ctrl+C{C_GRAY} to exit{C_RESET}")
    print()


# ─── Stream Processing ──────────────────────────────

async def process_streaming(agent, user_input: str, session_id: str):
    """Process a message with streaming output and rich formatting."""
    cancel_event = asyncio.Event()
    spinner = Spinner("thinking...")
    t0 = time.time()

    streaming_text = False
    tool_count = 0
    current_round = 0
    collected_text = []

    old_settings = None
    try:
        if sys.stdin.isatty():
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
    except Exception:
        pass

    async def _check_cancel():
        while not cancel_event.is_set():
            if _check_escape():
                cancel_event.set()
                spinner.stop()
                sys.stdout.write(f"\n  {C_BRIGHT_YELLOW}⛔ Cancelling...{C_RESET}\n")
                sys.stdout.flush()
                break
            await asyncio.sleep(0.05)

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
                    sys.stdout.write("\n")
                sys.stdout.write(data)
                sys.stdout.flush()
                collected_text.append(data)

            elif event_type == "preparing":
                # Text finished, tools coming — show spinner immediately
                if streaming_text:
                    sys.stdout.write("\n")
                    streaming_text = False
                    collected_text = []
                spinner.start("preparing...")

            elif event_type == "tool_start":
                name, args = data
                spinner.stop()
                if streaming_text:
                    # Render collected markdown before tool
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
                if not streaming_text:
                    rendered = MarkdownRenderer.render(data)
                    print(f"\n{rendered}")

            elif event_type == "done":
                spinner.stop()
                if not streaming_text and data:
                    rendered = MarkdownRenderer.render(data)
                    print(f"\n{rendered}")
                elif streaming_text:
                    # Render collected streaming text as markdown
                    full_text = ''.join(collected_text)
                    sys.stdout.write(C_CLEAR_LINE)
                    # Move up and clear what was streamed raw, re-render with markdown
                    # Actually for streaming we already printed raw, just end cleanly
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
        cancel_event.set()
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

        if old_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSANOW, old_settings)
            except Exception:
                pass
        _flush_stdin()


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
        if sys.stdin.isatty():
            _clean_term = termios.tcgetattr(sys.stdin)
    except Exception:
        pass

    while True:
        if _clean_term:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSANOW, _clean_term)
            except Exception:
                pass
        _flush_stdin()

        try:
            # \001 and \002 tell readline to ignore ANSI codes for width calc
            # This fixes the bug where long text or pasted text wraps incorrectly
            prompt = f"\n\001{C_BOLD}{C_BRIGHT_CYAN}\002❯\001{C_RESET}\002 "
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_GRAY}Bye! 👋{C_RESET}")
            break

        if not user_input:
            continue

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
            print(f"  {C_BRIGHT_CYAN}📊{C_RESET} Tokens:        {stats.get('total_tokens', 0):,}")
            print(f"  {C_BRIGHT_CYAN}📦{C_RESET} Compactions:   {stats.get('compactions', 0)}")
            print(f"  {C_BRIGHT_CYAN}🧠{C_RESET} Memories:      {stats.get('memories_extracted', 0)}")
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
            os.system("clear")
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
                    follow_up = input(f"\n  \001{C_GRAY}\002[persistent]\001{C_RESET}\002 \001{C_BOLD}{C_BRIGHT_CYAN}\002❯\001{C_RESET}\002 ").strip()
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
    import argparse

    parser = argparse.ArgumentParser(
        description="TokioAI CLI — Autonomous AI Agent",
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


if __name__ == "__main__":
    main()
