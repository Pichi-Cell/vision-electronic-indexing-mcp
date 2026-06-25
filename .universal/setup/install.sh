#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNIVERSAL_DIR="$REPO_ROOT/.universal"

echo "=== Vision Electronic Indexing Universal Setup ==="
echo ""

# 1. Python dependencies
echo "[1/3] Installing Python dependencies..."
python3 -m pip install -r "$REPO_ROOT/requirements.txt" --quiet
echo "  Done."

# 2. Environment file
ENV_FILE="$REPO_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[2/3] Creating .env file from .env.example..."
    cp "$REPO_ROOT/.env.example" "$ENV_FILE"
    echo "  Edit $ENV_FILE with your Cloudflare Workers AI credentials."
else
    echo "[2/3] .env file already exists."
fi

# 3. MCP config
echo "[3/3] To configure your agent, copy the appropriate config from .universal/configs/:"
echo ""
echo "  OpenCode:  cp $UNIVERSAL_DIR/configs/opencode.json.example <project>/.opencode/opencode.json"
echo "  Claude:    cp $UNIVERSAL_DIR/configs/claude.json.example <project>/.claude/settings.json"
echo "  Codex CLI: cp $UNIVERSAL_DIR/configs/codex.json.example <project>/.codex/settings.json"
echo "  Cursor:    cp $UNIVERSAL_DIR/configs/cursor.json.example <project>/.cursor/mcp.json"
echo ""
echo "Then edit the file to set the path to $REPO_ROOT/vision_inventory_mcp.py and your credentials."
echo ""
echo "=== Setup complete ==="
