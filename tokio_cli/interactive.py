#!/usr/bin/env python3
"""
TokioAI Interactive CLI — Claude Code-style terminal interface.

Features:
- Streaming responses (token by token)
- Escape to cancel running requests
- Real-time tool execution feedback
- Spinner while thinking
- readline history

Usage:
    python3 -m tokio_cli                    # interactive mode
    python3 -m tokio_cli "fix the bug"      # single command
    python3 -m tokio_cli --session mysession # resume session
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
from typing import Optional

# ─── ANSI Colors ─────────────────────────────────────

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[90m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"
C_CLEAR_LINE = "\033[2K\033[G"

# ─── History ─────────────────────────────────────────

HISTORY_FILE = os.path.expanduser("~/.tokio_cli_history")


def _load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            readline.read_history_file(HISTORY_FILE)
    except Exception:
        pass


def _save_history():
    try:
        readline.set_history_length(1000)
        readline.write_history_file(HISTORY_FILE)
    except Exception:
        pass


# ─── Spinner ─────────────────────────────────────────

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Spinner:
    """Async spinner that shows activity while waiting."""

    def __init__(self, message: str = "thinking"):
        self._message = message
        self._task: Optional[asyncio.Task] = None
        self._active = False
        self._stopped = False
        self._start_time = 0.0

    def start(self, message: Optional[str] = None):
        if message:
            self._message = message
        self._active = True
        self._stopped = False
        self._start_time = time.time()
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._spin())

    def stop(self):
        self._active = False
        self._stopped = True
        sys.stdout.write(C_CLEAR_LINE)
        sys.stdout.flush()
        if self._task and not self._task.done():
            self._task.cancel()

    def update(self, message: str):
        self._message = message

    async def _spin(self):
        i = 0
        try:
            while self._active:
                frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                elapsed = time.time() - self._start_time
                timer = f" {C_DIM}({elapsed:.0f}s){C_RESET}" if elapsed >= 2 else ""
                sys.stdout.write(f"{C_CLEAR_LINE}  {C_CYAN}{frame}{C_RESET} {C_DIM}{self._message}{C_RESET}{timer}")
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            pass
        finally:
            if not self._stopped:
                sys.stdout.write(C_CLEAR_LINE)
                sys.stdout.flush()


# ─── Key Detection ───────────────────────────────────

def _check_escape() -> bool:
    """Non-blocking check if Escape was pressed."""
    if not sys.stdin.isatty():
        return False
    try:
        if select.select([sys.stdin], [], [], 0)[0]:
            ch = sys.stdin.read(1)
            return ch == '\x1b'  # Escape
    except Exception:
        pass
    return False


# ─── Sensitive Data Masking ──────────────────────────

_SENSITIVE_PATTERNS = [
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP]'),          # IPv4
    (re.compile(r'\b\w+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[USER@HOST]'), # user@IP
    (re.compile(r'\b\w+@[\w.-]+\.\w+\b'), '[USER@HOST]'),                     # user@hostname
    (re.compile(r'ssh\s+-i\s+\S+'), 'ssh -i [KEY]'),                           # SSH key paths
    (re.compile(r'TELEGRAM_BOT_TOKEN=\S+'), 'TELEGRAM_BOT_TOKEN=[REDACTED]'),  # Bot tokens
    (re.compile(r'POSTGRES_PASSWORD=\S+'), 'POSTGRES_PASSWORD=[REDACTED]'),     # DB passwords
    (re.compile(r'TOKEN=\S+'), 'TOKEN=[REDACTED]'),                             # Generic tokens
    (re.compile(r'password["\s:=]+\S+', re.IGNORECASE), 'password=[REDACTED]'), # Passwords
]


def _mask_sensitive(text: str) -> str:
    """Mask IPs, credentials, and other sensitive data from CLI output."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ─── Tool Icons ──────────────────────────────────────

TOOL_ICONS = {
    "bash": ">>", "python": "Py", "read_file": "RD",
    "write_file": "WR", "edit_file": "ED", "search_code": "SR",
    "find_files": "FF", "list_files": "LS", "subagent": "AG",
    "docker": "DK", "raspi_vision": "RP", "postgres_query": "DB",
    "curl": "CL", "wget": "WG", "gcp_compute": "GC",
    "host_control": "HC", "router_control": "RT", "coffee": "CF",
    "drone": "DR", "iot_control": "IO",
}


def _format_tool_start(name: str, args: dict) -> str:
    """Format tool start line."""
    icon = TOOL_ICONS.get(name, "TL")
    detail = ""
    if "command" in args:
        cmd = _mask_sensitive(str(args["command"])[:80])
        detail = f" {C_DIM}{cmd}{C_RESET}"
    elif "path" in args:
        detail = f" {C_DIM}{args['path']}{C_RESET}"
    elif "pattern" in args:
        detail = f" {C_DIM}'{args['pattern']}'{C_RESET}"
    elif "action" in args:
        detail = f" {C_DIM}{args['action']}{C_RESET}"
    return f"  {C_CYAN}[{icon}]{C_RESET} {name}{detail}"


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

def format_status(agent, elapsed: float) -> str:
    """Format the status bar."""
    stats = agent.get_stats()
    parts = [f"{elapsed:.1f}s"]

    total_tokens = stats.get("total_tokens", 0)
    if total_tokens > 1_000_000:
        parts.append(f"{total_tokens/1_000_000:.1f}M tok")
    elif total_tokens > 1000:
        parts.append(f"{total_tokens/1000:.0f}K tok")

    tools_used = stats.get("tools_executed", 0)
    if tools_used > 0:
        parts.append(f"{tools_used} tools")

    compactions = stats.get("compactions", 0)
    if compactions > 0:
        parts.append(f"{compactions} compactions")

    mem = stats.get("auto_memory", {})
    if mem.get("total_memories_saved", 0) > 0:
        parts.append(f"{mem['total_memories_saved']} memories")

    return " | ".join(parts)


# ─── Help ────────────────────────────────────────────

def show_help(agent):
    print(f"""
{C_BOLD}TokioAI CLI{C_RESET}

{C_BOLD}Comandos:{C_RESET}
  {C_CYAN}exit{C_RESET}, {C_CYAN}quit{C_RESET}     Salir
  {C_CYAN}reset{C_RESET}           Nueva sesion
  {C_CYAN}stats{C_RESET}           Estadisticas
  {C_CYAN}tools{C_RESET}           Herramientas
  {C_CYAN}clear{C_RESET}           Limpiar pantalla

{C_BOLD}Atajos:{C_RESET}
  {C_CYAN}Escape{C_RESET}          Cancelar request actual
  {C_CYAN}Ctrl+C{C_RESET}          Cancelar / Salir

{C_BOLD}Skills:{C_RESET}
{agent.skill_registry.format_help()}

{C_BOLD}Ejemplos:{C_RESET}
  {C_DIM}buscar archivos python que usen asyncio{C_RESET}
  {C_DIM}/status{C_RESET}
  {C_DIM}/compact{C_RESET}
  {C_DIM}editar main.py y cambiar el timeout a 30{C_RESET}
""")


# ─── Stream Processing ──────────────────────────────

async def process_streaming(agent, user_input: str, session_id: str):
    """Process a message with streaming output."""
    cancel_event = asyncio.Event()
    spinner = Spinner("thinking...")
    t0 = time.time()

    # Track state for display
    streaming_text = False
    tool_count = 0
    current_round = 0

    # Save/restore terminal for escape detection
    old_settings = None
    try:
        if sys.stdin.isatty():
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
    except Exception:
        pass

    # Background escape checker
    async def _check_cancel():
        while not cancel_event.is_set():
            if _check_escape():
                cancel_event.set()
                spinner.stop()
                sys.stdout.write(f"\n  {C_YELLOW}⛔ Cancelando...{C_RESET}\n")
                sys.stdout.flush()
                break
            await asyncio.sleep(0.05)

    cancel_task = asyncio.create_task(_check_cancel())

    try:
        spinner.start("thinking...")

        async for event_type, data in agent.process_message_stream(
            user_message=user_input,
            session_id=session_id,
            cancel_event=cancel_event,
        ):
            if event_type == "thinking":
                current_round = data
                if data > 1:
                    spinner.update(f"thinking round {data}...")
                else:
                    spinner.start("thinking...")

            elif event_type == "token":
                # Native tool use means text tokens are always displayable
                if not streaming_text:
                    spinner.stop()
                    streaming_text = True
                    sys.stdout.write("\n")
                sys.stdout.write(data)
                sys.stdout.flush()

            elif event_type == "tool_start":
                name, args = data
                spinner.stop()
                if streaming_text:
                    sys.stdout.write("\n")
                    streaming_text = False
                tool_count += 1
                print(_format_tool_start(name, args))
                spinner.start(f"running {name}...")

            elif event_type == "tool_end":
                name, output = data
                spinner.stop()
                if output and output.strip():
                    preview = _mask_sensitive(output.strip().replace("\n", " ")[:120])
                    print(f"      {C_DIM}-> {preview}{'...' if len(output.strip()) > 120 else ''}{C_RESET}")

            elif event_type == "text":
                # Non-streaming fallback
                spinner.stop()
                if not streaming_text:
                    print(f"\n{data}")
                response_text = data

            elif event_type == "done":
                spinner.stop()
                if not streaming_text and data:
                    print(f"\n{data}")
                elif streaming_text:
                    sys.stdout.write("\n")

            elif event_type == "error":
                spinner.stop()
                print(f"\n{C_RED}Error: {data}{C_RESET}")

        elapsed = time.time() - t0
        status = format_status(agent, elapsed)
        print(f"\n{C_DIM}[{status}]{C_RESET}")

    except KeyboardInterrupt:
        spinner.stop()
        cancel_event.set()
        print(f"\n  {C_YELLOW}⛔ Cancelado{C_RESET}")

    finally:
        cancel_event.set()
        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass
        # Restore terminal
        if old_settings:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass


# ─── Main Loop ───────────────────────────────────────

async def run_interactive(session_id: Optional[str] = None):
    """Run the interactive CLI loop."""
    print(f"""
{C_MAGENTA}    ████████╗ ██████╗ ██╗  ██╗██╗ ██████╗
    ╚══██╔══╝██╔═══██╗██║ ██╔╝██║██╔═══██╗
       ██║   ██║   ██║█████╔╝ ██║██║   ██║
       ██║   ██║   ██║██╔═██╗ ██║██║   ██║
       ██║   ╚██████╔╝██║  ██╗██║╚██████╔╝
       ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝{C_RESET}
{C_BOLD}{C_WHITE}              AI CLI v3.0{C_RESET}
{C_DIM}  Streaming | Native Tools | Auto-compact | Skills
  Escape cancela. 'help' para comandos.{C_RESET}
""")

    _load_history()
    agent = create_agent()
    sid = session_id or "cli-interactive"

    while True:
        try:
            user_input = input("\ntokio> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}Bye.{C_RESET}")
            break

        if not user_input:
            continue

        _save_history()

        # Built-in commands
        lower = user_input.lower()
        if lower in ("exit", "quit", "q"):
            print(f"{C_DIM}Bye.{C_RESET}")
            break
        if lower == "help":
            show_help(agent)
            continue
        if lower == "reset":
            sid = f"cli-{int(time.time())}"
            print(f"{C_DIM}Sesion reiniciada: {sid}{C_RESET}")
            continue
        if lower == "stats":
            stats = agent.get_stats()
            print(f"\n{C_BOLD}Stats:{C_RESET}")
            print(f"  LLM: {stats.get('llm', '?')}")
            print(f"  Tools: {stats.get('tools_count', 0)}")
            print(f"  Mensajes: {stats.get('messages_processed', 0)}")
            print(f"  Tools ejecutadas: {stats.get('tools_executed', 0)}")
            print(f"  Tokens: {stats.get('total_tokens', 0):,}")
            print(f"  Compactaciones: {stats.get('compactions', 0)}")
            print(f"  Memorias: {stats.get('memories_extracted', 0)}")
            continue
        if lower == "tools":
            names = sorted(agent.registry.list_names())
            print(f"\n{C_BOLD}{len(names)} herramientas:{C_RESET}")
            for n in names:
                t = agent.registry.get(n)
                print(f"  {C_CYAN}{n}{C_RESET} — {t.description[:60]}")
            continue
        if lower == "clear":
            os.system("clear")
            continue

        # Process with streaming
        await process_streaming(agent, user_input, sid)

    _save_history()


async def run_single(query: str, session_id: Optional[str] = None):
    """Run a single command with streaming and exit."""
    agent = create_agent()
    sid = session_id or "cli-oneshot"

    print(f"{C_BOLD}{C_MAGENTA}TokioAI{C_RESET} > {query}\n")
    await process_streaming(agent, query, sid)


def main():
    """Entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="TokioAI CLI — Interactive agent with streaming",
    )
    parser.add_argument("query", nargs="*", help="Query to run (omit for interactive mode)")
    parser.add_argument("--session", "-s", default=None, help="Session ID")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.query:
        query = " ".join(args.query)
        asyncio.run(run_single(query, args.session))
    else:
        asyncio.run(run_interactive(args.session))


if __name__ == "__main__":
    main()
