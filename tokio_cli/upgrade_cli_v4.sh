#!/bin/bash
# Upgrade CLI from v3.3 to v4.0
# Run this AFTER the current CLI session ends

set -e

DIR="$(dirname "$0")"

echo "🔄 Upgrading TokioAI CLI v3.3 → v4.0..."

# Backup
cp "$DIR/interactive.py" "$DIR/interactive_v3.3_backup.py"
echo "  ✅ Backup saved as interactive_v3.3_backup.py"

# Swap
cp "$DIR/interactive_v4.py" "$DIR/interactive.py"
echo "  ✅ interactive_v4.py → interactive.py"

# Verify syntax
python3 -c "import py_compile; py_compile.compile('$DIR/interactive.py', doraise=True)"
echo "  ✅ Syntax OK"

echo ""
echo "🎉 CLI v4.0 ready! Run: python3 -m tokio_cli"
