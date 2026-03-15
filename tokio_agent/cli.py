"""
TokioAI CLI — Interactive terminal interface.

Usage:
    tokio              — Start interactive session
    tokio "message"    — Single message mode
    tokio setup        — Run setup wizard
    tokio status       — Show agent status
    tokio tools        — List available tools
"""
from __future__ import annotations

import asyncio
import logging
import os
import readline  # enables line editing (backspace, arrows, history)
import sys
from typing import Optional

# Configure logging before imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tokio")

# Reduce noise from libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


BANNER = r"""
╔══════════════════════════════════════════════╗
║  ████████╗ ██████╗ ██╗  ██╗██╗ ██████╗      ║
║  ╚══██╔══╝██╔═══██╗██║ ██╔╝██║██╔═══██╗     ║
║     ██║   ██║   ██║█████╔╝ ██║██║   ██║     ║
║     ██║   ██║   ██║██╔═██╗ ██║██║   ██║     ║
║     ██║   ╚██████╔╝██║  ██╗██║╚██████╔╝     ║
║     ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝ ╚═════╝     ║
║          Autonomous AI Agent v2.0            ║
╚══════════════════════════════════════════════╝
"""


def _load_env():
    """Load .env file if present."""
    for env_path in [".env", "/workspace/.env", os.path.expanduser("~/.tokio/.env")]:
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        os.environ.setdefault(key.strip(), value.strip())
            logger.debug(f"Loaded env from {env_path}")
            break


async def _interactive_session():
    """Run the interactive CLI session."""
    from .engine.agent import TokioAgent

    print(BANNER)
    print("Cargando agente...")

    agent = TokioAgent(
        on_thinking=lambda r: print(f"  🧠 Pensando... (ronda {r})", end="\r"),
        on_tool_start=lambda name, args: print(f"  🔧 {name}...", end="\r"),
        on_tool_end=lambda name, result: None,
    )

    # Validate LLM is configured
    if not agent.llm.is_available():
        print("\n⚠️  No LLM provider configured!")
        print("   Edit .env and set at least one of:")
        print("   - ANTHROPIC_API_KEY=sk-ant-...")
        print("   - OPENAI_API_KEY=sk-...")
        print("   - GEMINI_API_KEY=...")
        print("   Or run: tokio setup\n")
        return

    session_id = agent.session_manager.create_session()

    user_name = agent.workspace.get_preference("user_name")
    if user_name:
        print(f"\n¡Hola {user_name}! 👋")
    else:
        print("\n¡Hola! Soy TokioAI. ¿Cómo te llamas?")

    print(f"LLM: {agent.llm.display_name()}")
    print(f"Tools: {agent.registry.count()} disponibles")
    print(f"Comandos: /tools, /status, /clear, /exit\n")

    while True:
        try:
            user_input = input("🌀 tokio> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 ¡Hasta luego!")
            break

        if not user_input:
            continue

        # Special commands
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            print("👋 ¡Hasta luego!")
            break

        if user_input.lower() == "/tools":
            for cat, tools in sorted(agent.registry.list_by_category().items()):
                print(f"\n📁 {cat}:")
                for t in tools:
                    print(f"  • {t.name}: {t.description}")
            print()
            continue

        if user_input.lower() == "/status":
            stats = agent.get_stats()
            print(f"\n📊 Estado del agente:")
            print(f"  LLM: {stats['llm']}")
            print(f"  Tools: {stats['tools_count']}")
            print(f"  Mensajes: {stats['messages_processed']}")
            print(f"  Tools ejecutadas: {stats['tools_executed']}")
            print(f"  Errores recuperados: {stats['errors_recovered']}")
            print(f"  Tokens totales: {stats['total_tokens']}")
            print()
            continue

        if user_input.lower() == "/clear":
            session_id = agent.session_manager.create_session()
            print("🗑️ Sesión limpiada.\n")
            continue

        # Process message
        print()
        try:
            response = await agent.process_message(
                user_message=user_input,
                session_id=session_id,
            )
            print(f"\n{response}\n")
        except Exception as e:
            print(f"\n❌ Error: {e}\n")


async def _single_message(message: str):
    """Process a single message and exit."""
    from .engine.agent import TokioAgent

    agent = TokioAgent()
    session_id = agent.session_manager.create_session()
    response = await agent.process_message(message, session_id)
    print(response)


def main():
    """Main entry point."""
    _load_env()

    args = sys.argv[1:]

    if not args:
        # Interactive mode
        asyncio.run(_interactive_session())
    elif args[0] == "setup":
        # Setup wizard
        from .setup_wizard import run_wizard
        run_wizard()
    elif args[0] == "status":
        asyncio.run(_single_message("Muestra tu estado actual"))
    elif args[0] == "tools":
        asyncio.run(_single_message("/tools"))
    elif args[0] == "server":
        # Start API server
        import uvicorn
        port = int(os.getenv("TOKIO_PORT", "8000"))
        uvicorn.run(
            "tokio_agent.api.server:app",
            host="0.0.0.0",
            port=port,
            log_level="info",
        )
    else:
        # Single message mode
        message = " ".join(args)
        asyncio.run(_single_message(message))


if __name__ == "__main__":
    main()
