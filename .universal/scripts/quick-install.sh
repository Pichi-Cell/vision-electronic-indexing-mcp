#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-Pichi-Cell/vision-electronic-indexing-mcp}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.vision-electronic-indexing}"
VENV_DIR="$INSTALL_DIR/.venv"
PYTHON="${PYTHON:-python3}"

# --local flag: copy from a local repository checkout instead of downloading from GitHub.
LOCAL_SRC=""
if [ "${1:-}" = "--local" ] || [ "${1:-}" = "-l" ]; then
  LOCAL_SRC="${2:-}"
  if [ -z "$LOCAL_SRC" ]; then
    # This script lives in .universal/scripts, so the repository root is two levels up.
    LOCAL_SRC="$(cd "$(dirname "$0")/../.." && pwd)"
  fi
  LOCAL_SRC="$(cd "$LOCAL_SRC" && pwd)"
fi

RAW_BASE="https://raw.githubusercontent.com/$REPO/$BRANCH"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=== Vision Electronic Indexing Universal Installer ===${NC}"
echo ""

# 1. Copy / Download
echo -e "${GREEN}[1/6]${NC} Installing to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"/{scripts,configs,skills/vision-inventory-workflow,setup,prompts}

if [ -n "$LOCAL_SRC" ]; then
  echo -e "  Copying from ${CYAN}$LOCAL_SRC${NC} ..."
  cp "$LOCAL_SRC/vision_inventory_mcp.py" "$INSTALL_DIR/"
  cp "$LOCAL_SRC/requirements.txt" "$INSTALL_DIR/"
  cp "$LOCAL_SRC/.env.example" "$INSTALL_DIR/"
  [ -f "$LOCAL_SRC/.gitignore" ] && cp "$LOCAL_SRC/.gitignore" "$INSTALL_DIR/" || true
  [ -f "$LOCAL_SRC/LICENSE" ] && cp "$LOCAL_SRC/LICENSE" "$INSTALL_DIR/" || true
  cp "$LOCAL_SRC/scripts/inventory_folder_to_csv.py" "$INSTALL_DIR/scripts/"
  for f in "$LOCAL_SRC"/.universal/configs/*.json.example; do
    cp "$f" "$INSTALL_DIR/configs/"
  done
  cp "$LOCAL_SRC/.universal/skills/vision-inventory-workflow/SKILL.md" "$INSTALL_DIR/skills/vision-inventory-workflow/"
  cp "$LOCAL_SRC/.universal/prompts/vision-inventory-agent-bom.md" "$INSTALL_DIR/prompts/"
else
  for f in vision_inventory_mcp.py requirements.txt .env.example .gitignore LICENSE; do
    curl -fsSL "$RAW_BASE/$f" -o "$INSTALL_DIR/$f"
  done
  curl -fsSL "$RAW_BASE/scripts/inventory_folder_to_csv.py" -o "$INSTALL_DIR/scripts/inventory_folder_to_csv.py"
  for f in opencode.json.example claude.json.example codex.json.example cursor.json.example; do
    curl -fsSL "$RAW_BASE/.universal/configs/$f" -o "$INSTALL_DIR/configs/$f"
  done
  curl -fsSL "$RAW_BASE/.universal/skills/vision-inventory-workflow/SKILL.md" -o "$INSTALL_DIR/skills/vision-inventory-workflow/SKILL.md"
  curl -fsSL "$RAW_BASE/.universal/prompts/vision-inventory-agent-bom.md" -o "$INSTALL_DIR/prompts/vision-inventory-agent-bom.md"
fi
echo -e "  ${GREEN}Done.${NC}"

# 2. Python venv + deps
echo ""
echo -e "${GREEN}[2/6]${NC} Creating Python virtual environment..."
"$PYTHON" -m venv "$VENV_DIR"
echo -e "  ${GREEN}Done.${NC}"

echo -e "${GREEN}[3/6]${NC} Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
echo -e "  ${GREEN}Done.${NC}"

# 3. Credentials
echo ""
echo -e "${GREEN}[4/6]${NC} Cloudflare Workers AI credentials"
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
  echo "  Created $ENV_FILE"
fi

echo "  Get these from https://dash.cloudflare.com/ -> AI -> Workers AI -> Use REST API"
echo "  Enter your credentials (or press enter to edit .env later):"
read -rp "  Cloudflare Account ID: " CF_ID || true
read -rp "  Cloudflare API Token: " CF_TOKEN || true

CF_ID="${CF_ID:-}"
CF_TOKEN="${CF_TOKEN:-}"
if [ -n "$CF_ID" ]; then
  if [[ "${OSTYPE:-}" == "darwin"* ]]; then
    sed -i '' "s/your_cloudflare_account_id/$CF_ID/" "$ENV_FILE"
  else
    sed -i "s/your_cloudflare_account_id/$CF_ID/" "$ENV_FILE"
  fi
fi
if [ -n "$CF_TOKEN" ]; then
  if [[ "${OSTYPE:-}" == "darwin"* ]]; then
    sed -i '' "s/your_cloudflare_workers_ai_token/$CF_TOKEN/" "$ENV_FILE"
  else
    sed -i "s/your_cloudflare_workers_ai_token/$CF_TOKEN/" "$ENV_FILE"
  fi
fi
echo -e "  ${GREEN}Done.${NC}"

# 4. Harness skill install
echo ""
echo -e "${GREEN}[5/6]${NC} Installing skill for your agent..."
install_skill() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  cp "$INSTALL_DIR/skills/vision-inventory-workflow/SKILL.md" "$target"
  echo -e "  Installed skill to ${CYAN}$target${NC}"
}

echo "  Which agent are you using?"
echo "    1) OpenCode"
echo "    2) Claude Code"
echo "    3) Codex CLI"
echo "    4) Pi (original package)"
echo "    5) All of the above"
echo "    6) Skip (I'll install manually)"
read -rp "  Choice [1-6]: " AGENT_CHOICE || true

AGENT_CHOICE="${AGENT_CHOICE:-}"
case "$AGENT_CHOICE" in
  1) install_skill "$HOME/.config/opencode/skills/vision-inventory-workflow/SKILL.md" ;;
  2) install_skill "$HOME/.claude/skills/vision-inventory-workflow/SKILL.md" ;;
  3) install_skill "$HOME/.agents/skills/vision-inventory-workflow/SKILL.md" ;;
  4)
    echo -e "  ${CYAN}Pi install:${NC} pi install npm:vision-electronic-indexing-pi"
    if command -v pi &>/dev/null; then
      pi install npm:vision-electronic-indexing-pi
    else
      echo "  'pi' command not found. Install Pi first, then run:"
      echo "  pi install npm:vision-electronic-indexing-pi"
    fi
    ;;
  5)
    install_skill "$HOME/.config/opencode/skills/vision-inventory-workflow/SKILL.md"
    install_skill "$HOME/.claude/skills/vision-inventory-workflow/SKILL.md"
    install_skill "$HOME/.agents/skills/vision-inventory-workflow/SKILL.md"
    if command -v pi &>/dev/null; then
      pi install npm:vision-electronic-indexing-pi
    fi
    ;;
  *) echo "  Skipping." ;;
esac

# 5. Summary
echo ""
echo -e "${GREEN}[6/6]${NC} Setup complete!"
echo ""
echo -e "  ${CYAN}Installed to:${NC}       $INSTALL_DIR"
echo -e "  ${CYAN}Run server:${NC}         $VENV_DIR/bin/python $INSTALL_DIR/vision_inventory_mcp.py"
echo -e "  ${CYAN}Activate env:${NC}       source $VENV_DIR/bin/activate"
echo -e "  ${CYAN}Edit credentials:${NC}   $ENV_FILE"
echo ""
echo "  Add the MCP server config to your agent:"
echo ""
echo "  OpenCode:  cp $INSTALL_DIR/configs/opencode.json.example <project>/.opencode/opencode.json"
echo "  Claude:    cp $INSTALL_DIR/configs/claude.json.example <project>/.claude/settings.json"
echo "  Codex CLI: cp $INSTALL_DIR/configs/codex.json.example <project>/.codex/settings.json"
echo "  Cursor:    cp $INSTALL_DIR/configs/cursor.json.example <project>/.cursor/mcp.json"
echo ""
echo "  Make sure the path to vision_inventory_mcp.py and credentials are correct in the config."
echo ""
echo -e "${CYAN}=== Done ===${NC}"
