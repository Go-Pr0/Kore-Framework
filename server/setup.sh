#!/usr/bin/env bash
# One-command setup for the standalone semantic MCP server.
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}>${NC} $*"; }
success() { echo -e "${GREEN}>${NC} $*"; }
warn()    { echo -e "${YELLOW}>${NC} $*"; }
error()   { echo -e "${RED}>${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Load .env ────────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
    info "Loaded .env"
fi

echo ""
echo -e "${BOLD}  Claude Semantic MCP — Setup${NC}"
echo ""

# ── 1. Python deps ──────────────────────────────────────────────────────────
info "Installing Python dependencies..."

DEPS=(
    "watchdog"
    "tree-sitter"
    "tree-sitter-language-pack"
    "tree_sitter_python"
    "networkx"
    "mcp>=1.10"
    "anyio>=4.0"
    "lancedb>=0.12"
    "pyarrow>=15.0"
    "pandas>=2.0"
    "transformers==4.52.4"
    "torch>=2.4"
    "torchvision"
    "pillow"
    "peft"
    "requests"
)

pip install --break-system-packages "${DEPS[@]}" -q 2>&1 | tail -3
success "Python dependencies installed"

# ── 2. Verify core components ──────────────────────────────────────────────
info "Testing Abstract Engine (indexer)..."
PYTHONPATH=src python3 -c "
from abstract_engine.index import AbstractIndex
print('  AbstractIndex: OK')
from abstract_engine.models import FileEntry, FunctionEntry
print('  Models: OK')
" 2>&1

info "Testing MCP server module..."
PYTHONPATH=src python3 -c "
from abstract_fs_server.server import mcp
print('  MCP server: OK')
" 2>&1

echo ""
success "Setup complete!"
echo ""

echo -e "${BOLD}How to use:${NC}"
echo ""
echo "  # Install this repo into a venv"
echo -e "  ${CYAN}bash install.sh${NC}"
echo ""
echo "  # Run the server manually from this repo"
echo -e "  ${CYAN}PYTHONPATH=src python3 -m abstract_fs_server.server${NC}"
echo ""
echo "  # Check repo detection and cache paths"
echo -e "  ${CYAN}PYTHONPATH=src python3 -c 'from abstract_fs_server.config import ServerConfig; print(ServerConfig.from_env())'${NC}"
echo ""
echo -e "${BOLD}Current server behavior:${NC}"
echo "  1. Detects the active repo root from the current working directory"
echo "  2. Stores cache data under ~/.cache/claude-semantic-mcp/"
echo "  3. Builds an abstract index plus semantic index for the repo"
echo "  4. Exposes search-only MCP tools for Claude Code"
echo ""
