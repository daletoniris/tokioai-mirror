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
C_DIM = "\033[90m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"
C_CLEAR_LINE = "\033[2K\033[G"

# ─── History & Session Persistence ───────────────────

HISTORY_FILE = os.path.expanduser("~/.tokio_cli_history")
SESSION_FILE = os.path.expanduser("~/.tokio_cli_session.json")


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


def _save_session_state(sid: str, last_messages: list):
    """Save session state to disk so it persists across CLI restarts."""
    import json
    try:
        # Keep last 20 messages as context summary
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
            # Check if session is less than 7 days old
            ts = state.get("timestamp", "")
            if ts:
                from datetime import datetime, timedelta
                saved = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if datetime.now() - saved > timedelta(days=7):
                    return None  # Too old, start fresh
            return state
    except Exception:
        pass
    return None


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


def _flush_stdin():
    """Flush any buffered bytes from stdin (leftover from cbreak mode)."""
    if not sys.stdin.isatty():
        return
    try:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


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
  {C_CYAN}unlimited{C_RESET}       Toggle modo ilimitado (rounds + tiempo)
  {C_CYAN}persistent{C_RESET}      Toggle modo persistente (sigue hasta que digas 'stop')

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
                spinner.start("thinking...")

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

    except Exception as e:
        spinner.stop()
        print(f"\n{C_RED}Error inesperado: {e}{C_RESET}")

    finally:
        cancel_event.set()
        spinner.stop()

        # Close the async generator to release resources
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

        # Restore terminal ALWAYS, then flush leftover input
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

    mode_parts = ["Streaming", "Native Tools", "Auto-compact", "Skills"]
    if max_rounds == 0:
        mode_parts.append("Unlimited")
    if persistent:
        mode_parts.append("Persistent")

    print(f"""
{C_MAGENTA}    ████████╗ ██████╗ ██╗  ██╗██╗ ██████╗
    ╚══██╔══╝██╔═══██╗██║ ██╔╝██║██╔═══██╗
       ██║   ██║   ██║█████╔╝ ██║██║   ██║
       ██║   ██║   ██║██╔═██╗ ██║██║   ██║
       ██║   ╚██████╔╝██║  ██╗██║╚██████╔╝
       ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝{C_RESET}
{C_BOLD}{C_WHITE}              AI CLI v3.0{C_RESET}
{C_DIM}  {' | '.join(mode_parts)}
  Escape cancela. 'help' para comandos.{C_RESET}
""")

    _load_history()
    agent = create_agent()

    # Session resumption
    sid = session_id
    resumed = False
    if not sid:
        prev = _load_session_state()
        if prev and prev.get("messages"):
            prev_sid = prev["session_id"]
            prev_ts = prev.get("timestamp", "?")
            prev_msgs = prev["messages"]
            # Show last few messages as context
            print(f"  {C_YELLOW}Sesion anterior encontrada ({prev_ts}):{C_RESET}")
            for msg in prev_msgs[-4:]:
                role = msg.get("role", "?")
                content = msg.get("content", "")[:100]
                icon = ">" if role == "user" else "<"
                print(f"    {C_DIM}{icon} {content}{'...' if len(msg.get('content', '')) > 100 else ''}{C_RESET}")
            print(f"  {C_DIM}Enter = continuar | 'new' = nueva sesion{C_RESET}")
            try:
                choice = input(f"  ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = ""
            if choice not in ("new", "nueva", "n"):
                sid = prev_sid
                resumed = True
                # Re-inject previous messages into the session
                for msg in prev_msgs:
                    agent.session_manager.add_message(sid, msg["role"], msg["content"])
                print(f"  {C_GREEN}Sesion restaurada: {sid}{C_RESET}\n")
            else:
                print(f"  {C_DIM}Nueva sesion.{C_RESET}\n")
        if not sid:
            sid = f"cli-{int(time.time())}"

    # Apply limits
    agent.MAX_TOOL_ROUNDS = max_rounds
    agent.MAX_TOTAL_TIME = max_time
    _persistent_mode = persistent

    if max_rounds == 0:
        print(f"  {C_YELLOW}Modo ilimitado: sin limite de rounds ni tiempo{C_RESET}")
    if _persistent_mode:
        print(f"  {C_YELLOW}Modo persistente: seguira trabajando hasta que escribas 'stop'{C_RESET}")

    # Save clean terminal state for recovery
    _clean_term = None
    try:
        if sys.stdin.isatty():
            _clean_term = termios.tcgetattr(sys.stdin)
    except Exception:
        pass

    while True:
        # Ensure terminal is in a sane state before reading input
        if _clean_term:
            try:
                termios.tcsetattr(sys.stdin, termios.TCSANOW, _clean_term)
            except Exception:
                pass
        _flush_stdin()

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
        if lower == "unlimited":
            if agent.MAX_TOOL_ROUNDS == 0:
                agent.MAX_TOOL_ROUNDS = 25
                agent.MAX_TOTAL_TIME = 600
                print(f"  {C_GREEN}Modo normal: 25 rounds, 10min max{C_RESET}")
            else:
                agent.MAX_TOOL_ROUNDS = 0
                agent.MAX_TOTAL_TIME = 0
                print(f"  {C_YELLOW}Modo ilimitado: sin limite de rounds ni tiempo{C_RESET}")
            continue
        if lower == "persistent":
            _persistent_mode = not _persistent_mode
            if _persistent_mode:
                agent.MAX_TOOL_ROUNDS = 0
                agent.MAX_TOTAL_TIME = 0
                print(f"  {C_YELLOW}Modo persistente ON: seguira trabajando hasta que escribas 'stop'{C_RESET}")
                print(f"  {C_DIM}  Tokio procesara tu mensaje y seguira iterando.{C_RESET}")
                print(f"  {C_DIM}  Escribe 'stop' en cualquier momento para que termine.{C_RESET}")
            else:
                agent.MAX_TOOL_ROUNDS = 25
                agent.MAX_TOTAL_TIME = 600
                print(f"  {C_GREEN}Modo persistente OFF: volvio a 25 rounds, 10min max{C_RESET}")
            continue
        if lower == "stop" and _persistent_mode:
            _persistent_mode = False
            agent.MAX_TOOL_ROUNDS = 25
            agent.MAX_TOTAL_TIME = 600
            print(f"  {C_GREEN}Detenido. Modo normal: 25 rounds, 10min max{C_RESET}")
            continue
        if lower == "stats":
            stats = agent.get_stats()
            mode = "UNLIMITED" if agent.MAX_TOOL_ROUNDS == 0 else f"{agent.MAX_TOOL_ROUNDS} rounds"
            print(f"\n{C_BOLD}Stats:{C_RESET}")
            print(f"  LLM: {stats.get('llm', '?')}")
            print(f"  Modo: {mode}{' + PERSISTENT' if _persistent_mode else ''}")
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

        # Save session state after each interaction
        session = agent.session_manager.get_session(sid)
        if session:
            _save_session_state(sid, session.get("messages", []))

        # Persistent mode: keep going until user says stop
        if _persistent_mode:
            while _persistent_mode:
                try:
                    follow_up = input(f"\n{C_DIM}[persistent] tokio>{C_RESET} ").strip()
                except (EOFError, KeyboardInterrupt):
                    _persistent_mode = False
                    print(f"\n{C_GREEN}Modo persistente detenido.{C_RESET}")
                    break
                if not follow_up:
                    # Empty input = let Tokio continue on its own
                    follow_up = "Continua con la tarea. Si terminaste, dime que esta listo."
                if follow_up.lower() in ("stop", "parar", "detener", "exit"):
                    _persistent_mode = False
                    agent.MAX_TOOL_ROUNDS = 25
                    agent.MAX_TOTAL_TIME = 600
                    print(f"  {C_GREEN}Modo persistente detenido. Volvio a 25 rounds.{C_RESET}")
                    break
                _save_history()
                await process_streaming(agent, follow_up, sid)

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
