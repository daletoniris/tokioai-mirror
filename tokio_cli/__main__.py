"""
Entry point: python3 -m tokio_cli [query]

Uses the full TokioAI engine with all features:
- Auto-compact, Auto-memory, Skills, Subagents, File tools

For the legacy ops CLI: python3 -m tokio_cli.tokio_ops
"""
import sys

if "--ops" in sys.argv:
    # Legacy ops mode
    sys.argv.remove("--ops")
    from tokio_cli.tokio_ops import main
else:
    # New interactive CLI with full engine
    from tokio_cli.interactive import main

main()
