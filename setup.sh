#!/bin/bash
# TokioAI CLI — Linux/macOS Setup
# Requires Python 3.10+

set -e

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   TokioAI CLI — Linux/macOS Setup    ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python3 not found. Install Python 3.10+"
    exit 1
fi

echo "[1/4] Creating virtual environment..."
if [ -d ".venv" ]; then
    echo "       .venv already exists, skipping"
else
    python3 -m venv .venv
fi

echo "[2/4] Activating venv..."
source .venv/bin/activate

echo "[3/4] Installing dependencies..."
pip install --upgrade pip setuptools wheel > /dev/null 2>&1
pip install -e . 2>&1

echo "[4/4] Verifying installation..."
python3 -c "from tokio_cli.interactive import main; print('  OK: TokioAI CLI ready!')" 2>&1

echo ""
echo "  ✅ TokioAI CLI installed successfully!"
echo ""
echo "  To use:"
echo "    source .venv/bin/activate"
echo "    tokioai"
echo ""
