#!/usr/bin/env bash
# ============================================================
# Kore Framework — Interactive Installer
# ============================================================
# Usage:  bash install.sh          (interactive)
#         bash install.sh --yes    (accept all defaults)
#
# Runs on Linux and macOS (bash 3.2+). If invoked from a
# directory other than ~/.claude-oracle, the installer copies
# itself there and re-executes from the canonical location.
# ============================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL_REPO="$HOME/.claude-oracle"
SCRIPTS="$REPO/scripts"
SERVER="$REPO/server"
SOURCE_RUNTIME="$REPO/source/runtime"
ENV_FILE="$REPO/.env"
MCP_JSON="$SOURCE_RUNTIME/semantic-mcp.json"
OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"

# ── Colors ───────────────────────────────────────────────────
BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; MAGENTA='\033[0;35m'; NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────
info()    { echo -e "  ${CYAN}>${NC} $*"; }
ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
warn()    { echo -e "  ${YELLOW}!${NC} $*"; }
err()     { echo -e "  ${RED}✗${NC} $*" >&2; }
section() { echo ""; echo -e "${BOLD}${BLUE}━━  $*${NC}"; echo ""; }
hr()      { echo -e "${DIM}────────────────────────────────────────────────────${NC}"; }

YES_MODE=false
RELOCATED=false
for _arg in "$@"; do
    case "$_arg" in
        --yes) YES_MODE=true ;;
        --relocated) RELOCATED=true ;;
    esac
done

# Lowercase a string without bash 4 ${var,,} (macOS ships bash 3.2)
_lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

confirm() {
    # confirm "prompt" [default: y|n]
    local prompt="$1"
    local default="${2:-y}"
    if $YES_MODE; then
        echo -e "  ${DIM}?${NC} ${prompt}  ${DIM}→ auto: ${default}${NC}"
        [ "$default" = "y" ]
        return
    fi
    local hint
    if [ "$default" = "y" ]; then hint="Y/n"; else hint="y/N"; fi
    echo -ne "  ${YELLOW}?${NC} ${prompt} ${DIM}[${hint}]${NC}: "
    local ans
    read -r ans || ans=""
    ans="${ans:-$default}"
    [ "$(_lower "$ans")" = "y" ]
}

prompt() {
    # prompt VAR "message" "default"
    local varname="$1" msg="$2" default="${3:-}"
    if $YES_MODE; then
        echo -e "  ${DIM}?${NC} ${msg}  ${DIM}→ auto: ${default}${NC}"
        printf -v "$varname" '%s' "$default"
        return
    fi
    if [[ -n "$default" ]]; then
        echo -ne "  ${YELLOW}?${NC} ${msg} ${DIM}[${default}]${NC}: "
    else
        echo -ne "  ${YELLOW}?${NC} ${msg}: "
    fi
    local ans; read -r ans
    printf -v "$varname" '%s' "${ans:-$default}"
}

# Read a KEY from .env (ignores commented lines)
env_read() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || { echo ""; return; }
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || echo ""
}

# Set or update a KEY in .env (creates file if needed).
# Portable across GNU/BSD sed by using Python for the rewrite.
env_write() {
    local key="$1" value="$2"
    python3 - "$ENV_FILE" "$key" "$value" <<'PYEOF'
import os, re, sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = []
if os.path.exists(path):
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()
pat = re.compile(r'^\s*#?\s*' + re.escape(key) + r'\s*=')
found = False
out = []
for line in lines:
    if pat.match(line):
        out.append(f'{key}={value}')
        found = True
    else:
        out.append(line)
if not found:
    out.append(f'{key}={value}')
with open(path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
PYEOF
}

# ── Repo sanity check ────────────────────────────────────────
if [[ ! -f "$SCRIPTS/sync.py" || ! -f "$SERVER/pyproject.toml" ]]; then
    err "Run this script from the Kore Framework repo root."
    err "Expected: $REPO/scripts/sync.py and $REPO/server/pyproject.toml"
    exit 1
fi

# ── Banner ───────────────────────────────────────────────────
clear 2>/dev/null || true
echo ""
echo -e "${BOLD}${MAGENTA}  ╔═══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${MAGENTA}  ║         Kore Framework — Installer        ║${NC}"
echo -e "${BOLD}${MAGENTA}  ╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Repo: ${DIM}$REPO${NC}"
echo -e "  Platform: ${DIM}${OS_NAME} ${ARCH_NAME}${NC}"
echo ""

# ════════════════════════════════════════════════════════════
# STEP 0 — Self-relocation to ~/.claude-oracle
# ════════════════════════════════════════════════════════════
# The rest of the system assumes the canonical path
# ~/.claude-oracle/. scripts/sync.py writes that string into
# ~/.claude/CLAUDE.md, ~/.gemini/GEMINI.md and ~/.codex/AGENTS.md
# as the documented "source of truth" location. Running the
# installer from anywhere else would leave the generated docs
# pointing at a directory that doesn't exist.
if ! $RELOCATED && [[ "$REPO" != "$CANONICAL_REPO" ]]; then
    section "Step 0: Relocating to ~/.claude-oracle"
    echo "  The installer needs to live at the canonical location:"
    echo -e "    ${BOLD}$CANONICAL_REPO${NC}"
    echo ""
    echo "  Current location: $REPO"
    echo ""
    if [[ -e "$CANONICAL_REPO" ]]; then
        _existing_items=$(ls -A "$CANONICAL_REPO" 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$_existing_items" != "0" ]]; then
            warn "$CANONICAL_REPO already exists and is not empty."
            echo "  Contents:"
            ls -A "$CANONICAL_REPO" 2>/dev/null | head -20 | sed 's/^/    /'
            echo ""
            if ! confirm "Overwrite the existing directory?" "n"; then
                err "Aborted. Move or remove $CANONICAL_REPO and re-run."
                exit 1
            fi
            info "Removing existing $CANONICAL_REPO ..."
            rm -rf "$CANONICAL_REPO"
        fi
    fi

    if ! confirm "Copy this repo to $CANONICAL_REPO and continue from there?"; then
        err "Aborted. The installer must run from $CANONICAL_REPO."
        exit 1
    fi

    info "Copying repo to $CANONICAL_REPO ..."
    mkdir -p "$CANONICAL_REPO"
    # Prefer rsync when available — it's on both Linux and macOS by default.
    # Exclude build artefacts, caches, and the local workspace so we ship a
    # clean tree. We deliberately include .git so `git pull` still works.
    if command -v rsync &>/dev/null; then
        rsync -a \
            --exclude='server/.venv/' \
            --exclude='models/' \
            --exclude='backups/' \
            --exclude='.team_workspace/' \
            --exclude='__pycache__/' \
            --exclude='*.pyc' \
            "$REPO"/ "$CANONICAL_REPO"/
    else
        # Fallback: tar-pipe, portable on any POSIX system.
        ( cd "$REPO" && tar \
            --exclude='server/.venv' \
            --exclude='models' \
            --exclude='backups' \
            --exclude='.team_workspace' \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            -cf - . ) | ( cd "$CANONICAL_REPO" && tar -xf - )
    fi
    chmod +x "$CANONICAL_REPO/install.sh" "$CANONICAL_REPO/uninstall.sh" 2>/dev/null || true
    ok "Copied to $CANONICAL_REPO"

    # Remember the original path so we can offer to clean it up at the end.
    export KORE_ORIGINAL_REPO="$REPO"

    info "Re-executing installer from the new location..."
    echo ""
    exec bash "$CANONICAL_REPO/install.sh" --relocated "$@"
fi

echo "  This installer will walk you through:"
echo "    1.  Checking prerequisites"
echo "    2.  Configuring your Hugging Face token and cache"
echo "    3.  Choosing your GPU/device for semantic search"
echo "    4.  Building the Python server venv"
echo "    5.  Downloading semantic search models"
echo "    6.  Enabling offline mode"
echo "    7.  Installing RTK (token compression)"
echo "    8.  Deploying agents, skills, rules, and MCP config"
echo "    9.  Installing the auto-sync daemon"
echo "    10. Verifying the installation"
echo ""
hr
if ! confirm "Continue with installation?"; then
    echo "  Aborted."
    exit 0
fi

# semantic-mcp.json is self-normalising: scripts/sync.py rewrites the
# location-dependent fields (repo_path, python_path, preload_repo_paths)
# on every run based on the actual REPO path. No mutation needed here.

# ════════════════════════════════════════════════════════════
# STEP 1 — Prerequisites
# ════════════════════════════════════════════════════════════
section "Step 1: Checking prerequisites"

# Python 3.12+
PYTHON=""
for py in python3.13 python3.12 python3; do
    if command -v "$py" &>/dev/null; then
        _ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        _maj="${_ver%%.*}"; _min="${_ver##*.}"
        if [[ "$_maj" -ge 3 && "$_min" -ge 12 ]]; then
            PYTHON=$(command -v "$py")
            ok "python3: $PYTHON  ($("$PYTHON" --version 2>&1))"
            break
        fi
    fi
done
if [[ -z "$PYTHON" ]]; then
    err "Python 3.12 or newer is required."
    err "Install it: https://www.python.org/downloads/"
    exit 1
fi

if ! command -v git &>/dev/null; then
    err "git is required."
    exit 1
fi
ok "git: $(git --version)"

if ! command -v jq &>/dev/null; then
    warn "jq not found — the RTK bash hook requires jq to function."
    warn "Install jq before using Claude Code: https://jqlang.github.io/jq/download/"
else
    ok "jq: $(jq --version)"
fi

# ════════════════════════════════════════════════════════════
# STEP 2 — Hugging Face configuration
# ════════════════════════════════════════════════════════════
section "Step 2: Hugging Face token"

echo "  Two models are required by the semantic search engine:"
echo "    • jinaai/jina-code-embeddings-1.5b  — public (no token needed)"
echo "    • jinaai/jina-reranker-v3           — gated (requires HF token)"
echo ""
echo "  Get a free read-only token at: https://huggingface.co/settings/tokens"
echo ""

# HF_TOKEN
_cur_token="$(env_read HF_TOKEN)"
if [[ -n "$_cur_token" && "$_cur_token" != hf_XX* && ${#_cur_token} -gt 15 ]]; then
    ok "HF_TOKEN already set in .env (${_cur_token:0:12}...)"
    if confirm "Use existing token?"; then
        HF_TOKEN="$_cur_token"
    else
        prompt HF_TOKEN "Enter new HF token" ""
    fi
else
    warn "HF_TOKEN not set or is the placeholder value."
    prompt HF_TOKEN "Enter HF token (leave blank to skip — reranker download will fail)" ""
fi

# HF_HUB_CACHE
_cur_cache="$(env_read HF_HUB_CACHE)"
_default_cache="${_cur_cache:-$REPO/models}"
prompt HF_HUB_CACHE "Model cache directory" "$_default_cache"

# Write .env
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<ENVEOF
# Kore Framework — model configuration
# Run scripts/init_models.py once to download models into HF_HUB_CACHE.
# After that, uncomment TRANSFORMERS_OFFLINE / HF_HUB_OFFLINE.

HF_HUB_CACHE=${HF_HUB_CACHE}
HF_TOKEN=${HF_TOKEN}

# Uncomment after running init_models.py:
# TRANSFORMERS_OFFLINE=1
# HF_HUB_OFFLINE=1
ENVEOF
    ok "Created .env"
else
    env_write "HF_HUB_CACHE" "$HF_HUB_CACHE"
    [[ -n "$HF_TOKEN" ]] && env_write "HF_TOKEN" "$HF_TOKEN"
    ok "Updated .env"
fi

# ════════════════════════════════════════════════════════════
# STEP 3 — Device selection
# ════════════════════════════════════════════════════════════
section "Step 3: GPU / device configuration"

# Current setting from semantic-mcp.json
_cur_device="$("$PYTHON" - "$MCP_JSON" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d['semantic_mcp'].get('semantic_device', 'cuda'))
except Exception:
    print('cuda')
PYEOF
)"

# Hardware probe (no torch required)
_detected="cpu"
if [[ "$OS_NAME" == "Darwin" ]]; then
    if [[ "$ARCH_NAME" == "arm64" ]]; then
        _detected="mps"
        ok "Apple Silicon detected (${ARCH_NAME}) — Metal Performance Shaders available"
    else
        info "Intel Mac detected — CPU will be used (no CUDA/MPS available)"
        _detected="cpu"
    fi
elif command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
    _gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    _detected="cuda"
    ok "NVIDIA GPU detected: ${_gpu_name}"
elif command -v rocminfo &>/dev/null && rocminfo 2>/dev/null | grep -q "Device Type.*GPU"; then
    _detected="rocm"
    ok "AMD GPU detected (ROCm)"
elif [[ -c "/dev/kfd" || -d "/sys/class/kfd" ]]; then
    _detected="rocm"
    ok "AMD KFD device found"
else
    info "No discrete GPU detected — CPU will be used (slower but functional)"
    _detected="cpu"
fi

# On macOS, override any stale cuda/rocm value from semantic-mcp.json with the
# detected Apple-side default so the user isn't offered a non-functional option.
if [[ "$OS_NAME" == "Darwin" ]]; then
    case "$_cur_device" in
        cuda|rocm) _cur_device="$_detected" ;;
    esac
fi

echo ""
echo -e "  Current setting in semantic-mcp.json: ${BOLD}${_cur_device}${NC}"
echo -e "  Detected hardware default:            ${BOLD}${_detected}${NC}"
echo "  Valid options: cuda | rocm | mps | cpu | auto"
echo ""
prompt NEW_DEVICE "Device to use for semantic indexing" "$_detected"

if [[ "$NEW_DEVICE" != "$_cur_device" ]]; then
    "$PYTHON" - "$MCP_JSON" "$NEW_DEVICE" <<'PYEOF'
import json, sys
path, device = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data['semantic_mcp']['semantic_device'] = device
with open(path, 'w') as f:
    f.write(json.dumps(data, indent=2) + '\n')
print(f'  Updated semantic-mcp.json: semantic_device = {device}')
PYEOF
    ok "semantic-mcp.json updated"
fi

# ════════════════════════════════════════════════════════════
# STEP 4 — Server venv
# ════════════════════════════════════════════════════════════
section "Step 4: Python server venv"

if [[ -f "$SERVER/.venv/bin/python" ]]; then
    ok "server/.venv already exists"
    _venv_ver=$("$SERVER/.venv/bin/python" --version 2>&1)
    info "Venv python: $_venv_ver"
    if confirm "Reinstall / upgrade server venv?"; then
        bash "$SERVER/install.sh"
        ok "Server venv updated"
    fi
else
    info "Creating server/.venv and installing all Python dependencies..."
    echo "  (This installs torch, lancedb, transformers, tree-sitter, mcp, etc.)"
    echo "  This may take a few minutes on first run."
    echo ""
    bash "$SERVER/install.sh"
    ok "Server venv installed: $SERVER/.venv"
fi

# ════════════════════════════════════════════════════════════
# STEP 5 — Download models
# ════════════════════════════════════════════════════════════
section "Step 5: Download semantic search models"

_cache_expanded="${HF_HUB_CACHE/#\~/$HOME}"
_embed_marker="$_cache_expanded/models--jinaai--jina-code-embeddings-1.5b"
_rerank_marker="$_cache_expanded/models--jinaai--jina-reranker-v3"

echo "  Models to download:"
echo "    • jinaai/jina-code-embeddings-1.5b  (embedder, ~600 MB)"
echo "    • jinaai/jina-reranker-v3           (reranker, ~2 GB)"
echo "  Cache: $_cache_expanded"
echo ""

_already_downloaded=true
[[ -d "$_embed_marker" ]] || _already_downloaded=false
[[ -d "$_rerank_marker" ]] || _already_downloaded=false

if $_already_downloaded; then
    ok "Both models already present in cache"
    if confirm "Re-download / update models?"; then
        "$PYTHON" "$SCRIPTS/init_models.py"
    fi
else
    if confirm "Download models now? (required for semantic search to work)"; then
        "$PYTHON" "$SCRIPTS/init_models.py"
        ok "Models downloaded successfully"
    else
        warn "Skipping model download."
        warn "Run later:  python3 scripts/init_models.py"
    fi
fi

# ════════════════════════════════════════════════════════════
# STEP 6 — Offline mode
# ════════════════════════════════════════════════════════════
section "Step 6: Offline mode"

echo "  Once models are downloaded, you can lock the server to offline mode."
echo "  This prevents any network calls at MCP server startup (faster, safer)."
echo ""

# Check if offline flags are already active
_offline_enabled=false
if grep -qE "^TRANSFORMERS_OFFLINE=1" "$ENV_FILE" 2>/dev/null && \
   grep -qE "^HF_HUB_OFFLINE=1" "$ENV_FILE" 2>/dev/null; then
    _offline_enabled=true
fi

if $_offline_enabled; then
    ok "Offline mode already enabled in .env"
else
    _default_offline="n"
    if [[ -d "$_embed_marker" && -d "$_rerank_marker" ]]; then
        _default_offline="y"
        info "Both models are present — offline mode is safe to enable"
    fi
    if confirm "Enable offline mode?" "$_default_offline"; then
        # env_write already handles both the "commented-out" and "missing"
        # cases via its Python rewriter — no sed needed.
        env_write "TRANSFORMERS_OFFLINE" "1"
        env_write "HF_HUB_OFFLINE" "1"
        ok "Offline mode enabled in .env"
    else
        info "Skipped — the server will check HuggingFace at startup for updates"
    fi
fi

# ════════════════════════════════════════════════════════════
# STEP 7 — RTK
# ════════════════════════════════════════════════════════════
section "Step 7: RTK (token compression)"

echo "  RTK rewrites shell commands to token-efficient equivalents."
echo "  Example:  cat file.txt  →  rtk cat file.txt  (uses head+tail+ranges)"
echo "  It reduces API cost and latency on large file output."
echo ""

if command -v rtk &>/dev/null; then
    _rtk_ver=$(rtk --version 2>/dev/null | head -1 || echo "unknown")
    ok "RTK already installed: $_rtk_ver"
    if confirm "Upgrade RTK to latest?"; then
        if command -v cargo &>/dev/null; then
            cargo install --git https://github.com/rtk-ai/rtk
        elif command -v curl &>/dev/null; then
            bash -c "curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh"
        else
            warn "Neither cargo nor curl found — cannot upgrade RTK automatically."
        fi
    fi
else
    warn "RTK not found in PATH."
    if confirm "Install RTK?"; then
        if command -v cargo &>/dev/null; then
            info "Installing via cargo (compiling from source)..."
            cargo install --git https://github.com/rtk-ai/rtk
        elif command -v curl &>/dev/null; then
            info "Installing via curl installer..."
            bash -c "curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh"
        else
            warn "Neither cargo nor curl found."
            warn "Install manually: https://github.com/rtk-ai/rtk#installation"
        fi
        if command -v rtk &>/dev/null; then
            ok "RTK installed: $(rtk --version 2>/dev/null | head -1)"
        else
            warn "RTK binary not found in current PATH after install."
            warn "Restart your shell or add cargo/local bin to PATH, then re-run verify.py."
        fi
    else
        warn "Skipped. The RTK hook will be wired but inactive until RTK is installed."
    fi
fi

# ════════════════════════════════════════════════════════════
# STEP 8 — Deploy (sync)
# ════════════════════════════════════════════════════════════
section "Step 8: Deploying agents, skills, rules, and MCP configuration"

echo "  This deploys to:"
echo "    ~/.claude/CLAUDE.md, agents/, skills/, rules/, hooks/"
echo "    ~/.claude.json          — abstract-fs MCP entry"
echo "    ~/.gemini/GEMINI.md     — Gemini equivalent"
echo "    ~/.gemini/settings.json — Gemini MCP entry"
echo "    ~/.codex/AGENTS.md      — Codex equivalent"
echo "    ~/.codex/config.toml    — Codex MCP entry"
echo "    ~/.config/systemd/user/abstract-fs.service  (daemon mode)"
echo ""

info "Running sync..."
"$PYTHON" "$SCRIPTS/sync.py"
ok "Sync complete"

# ════════════════════════════════════════════════════════════
# STEP 9 — Daemon installation
# ════════════════════════════════════════════════════════════
section "Step 9: Auto-sync daemon"

echo "  The auto-sync daemon watches source/ for changes and re-deploys"
echo "  within seconds, keeping all targets always up to date."
echo ""

_os="$OS_NAME"

if [[ "$_os" == "Darwin" ]]; then
    # ── macOS LaunchAgents ──────────────────────────────────
    # Two agents are installed:
    #   1. com.claude.oracle.sync        — watches source/ and re-syncs
    #   2. com.claude.oracle.abstract-fs — runs the semantic MCP HTTP daemon
    #
    # The MCP config emitted by sync.py is in daemon mode (points at
    # http://127.0.0.1:8800/mcp), so without agent #2 nothing listens
    # and every MCP call from Claude Code would silently fail.
    _la_dir="$HOME/Library/LaunchAgents"
    _sync_plist="$_la_dir/com.claude.oracle.sync.plist"
    _fs_plist="$_la_dir/com.claude.oracle.abstract-fs.plist"
    _log_dir="$HOME/.claude/debug"
    mkdir -p "$_log_dir" "$_la_dir"

    _venv_py="$SERVER/.venv/bin/python"
    [[ ! -f "$_venv_py" ]] && _venv_py="$PYTHON"

    echo "  Platform: macOS — will install two LaunchAgents:"
    echo "    $_sync_plist"
    echo "    $_fs_plist"
    echo ""

    _install_sync_plist() {
        "$PYTHON" - "$_sync_plist" "$_venv_py" "$SCRIPTS/watch_sync.py" "$REPO" \
                       "$_log_dir/claude-oracle-sync.out.log" \
                       "$_log_dir/claude-oracle-sync.err.log" <<'PYEOF'
import plistlib, sys
plist_path, python, watch_script, cwd, stdout_log, stderr_log = sys.argv[1:]
plist = {
    "Label": "com.claude.oracle.sync",
    "ProgramArguments": [python, watch_script],
    "RunAtLoad": True,
    "KeepAlive": True,
    "WorkingDirectory": cwd,
    "StandardOutPath": stdout_log,
    "StandardErrorPath": stderr_log,
}
with open(plist_path, 'wb') as f:
    plistlib.dump(plist, f)
PYEOF
    }

    _install_fs_plist() {
        # Render a LaunchAgent that mirrors abstract-fs.service.template:
        # same env vars, same ExecStart, same working dir. We read
        # semantic-mcp.json + .env so the behavior stays in sync with
        # the Linux systemd path.
        "$PYTHON" - "$_fs_plist" "$MCP_JSON" "$ENV_FILE" "$_venv_py" "$SERVER" \
                       "$_log_dir/abstract-fs.out.log" \
                       "$_log_dir/abstract-fs.err.log" <<'PYEOF'
import json, os, plistlib, sys
from pathlib import Path

plist_path, mcp_json, env_file, python, server_dir, out_log, err_log = sys.argv[1:]

def expand(p):
    return str(Path(p).expanduser())

manifest = json.load(open(mcp_json))['semantic_mcp']
host = manifest.get('host', '127.0.0.1')
port = str(manifest.get('port', 8800))
semantic_device = manifest.get('semantic_device', 'auto')
if semantic_device == 'auto':
    # On Darwin, 'auto' means MPS on Apple Silicon, CPU otherwise.
    import platform
    semantic_device = 'mps' if platform.machine() == 'arm64' else 'cpu'
embedding_model = manifest.get('embedding_model', 'jinaai/jina-code-embeddings-1.5b')
pythonpath = os.path.join(expand(manifest.get('repo_path', server_dir)), manifest.get('pythonpath', 'src'))
log_file = expand(manifest.get('log_file', '~/.claude/debug/abstract-fs.log'))
preload_list = manifest.get('preload_repo_paths', []) or []
preload_repos = ','.join(expand(p) for p in preload_list)

env = {
    'HOME': os.path.expanduser('~'),
    'MCP_TRANSPORT': 'streamable-http',
    'MCP_HOST': host,
    'MCP_PORT': port,
    'SEMANTIC_DEVICE': semantic_device,
    'EMBEDDING_MODEL': embedding_model,
    'PYTHONPATH': pythonpath,
    'LOG_FILE': log_file,
    'PRELOAD_REPO_PATHS': preload_repos,
    'PATH': '/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/sbin:/usr/sbin',
}

# Forward HF_* keys from repo .env so the daemon inherits the same cache and
# offline toggles the installer configured.
if os.path.exists(env_file):
    for raw in open(env_file, encoding='utf-8').read().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if key in ('HF_HUB_CACHE', 'HF_TOKEN', 'TRANSFORMERS_OFFLINE', 'HF_HUB_OFFLINE'):
            env[key] = expand(value) if value.startswith('~') else value

plist = {
    'Label': 'com.claude.oracle.abstract-fs',
    'ProgramArguments': [python, '-m', manifest.get('module', 'abstract_fs_server.server')],
    'EnvironmentVariables': env,
    'WorkingDirectory': expand(manifest.get('repo_path', server_dir)),
    'RunAtLoad': True,
    'KeepAlive': True,
    'StandardOutPath': out_log,
    'StandardErrorPath': err_log,
    'ProcessType': 'Interactive',
}
with open(plist_path, 'wb') as f:
    plistlib.dump(plist, f)
PYEOF
    }

    # Sync LaunchAgent
    if [[ -f "$_sync_plist" ]]; then
        ok "sync LaunchAgent already installed"
        if confirm "Reload / restart it?"; then
            launchctl unload "$_sync_plist" 2>/dev/null || true
            _install_sync_plist
            launchctl load -w "$_sync_plist"
            ok "sync LaunchAgent reloaded"
        fi
    elif confirm "Install auto-sync LaunchAgent?"; then
        _install_sync_plist
        launchctl load -w "$_sync_plist"
        ok "sync LaunchAgent installed: $_sync_plist"
    else
        warn "Skipped sync LaunchAgent."
    fi

    echo ""

    # abstract-fs LaunchAgent (the MCP daemon)
    # Always kill any stale daemon (from a previous repo location) before
    # touching the plist. Otherwise an old process keeps owning :8800 and
    # the new plist silently fails to bind.
    _kill_stale_fs_daemon() {
        launchctl unload "$_fs_plist" 2>/dev/null || true
        if command -v pkill &>/dev/null; then
            pkill -f 'abstract_fs_server.server' 2>/dev/null || true
        fi
    }

    if [[ -f "$_fs_plist" ]]; then
        ok "abstract-fs LaunchAgent already installed"
        if confirm "Reload / restart it?"; then
            _kill_stale_fs_daemon
            _install_fs_plist
            launchctl load -w "$_fs_plist"
            ok "abstract-fs LaunchAgent reloaded"
        fi
    elif confirm "Install abstract-fs LaunchAgent (semantic MCP daemon)?"; then
        _kill_stale_fs_daemon
        _install_fs_plist
        launchctl load -w "$_fs_plist"
        ok "abstract-fs LaunchAgent installed: $_fs_plist"
        info "Logs: $_log_dir/abstract-fs.{out,err}.log"
    else
        warn "Skipped abstract-fs LaunchAgent — semantic MCP will not start."
        warn "Claude Code will fail to reach http://127.0.0.1:8800/mcp until you install it."
    fi

else
    # Linux systemd
    _unit_dir="$HOME/.config/systemd/user"
    _sync_unit="$_unit_dir/claude-oracle-sync.service"
    _fs_unit="$_unit_dir/abstract-fs.service"
    echo "  Platform: Linux — will install systemd user services"
    echo "  Sync service: $_sync_unit"
    echo ""

    if [[ -f "$_sync_unit" ]]; then
        ok "claude-oracle-sync.service already installed"
        _sync_active=$(systemctl --user is-active claude-oracle-sync.service 2>/dev/null || echo "inactive")
        info "Status: $_sync_active"
        if confirm "Restart daemon?"; then
            systemctl --user restart claude-oracle-sync.service
            ok "Restarted"
        fi
    elif confirm "Install auto-sync daemon?"; then
        mkdir -p "$_unit_dir"
        _venv_py="$SERVER/.venv/bin/python"
        [[ ! -f "$_venv_py" ]] && _venv_py="$PYTHON"
        cat > "$_sync_unit" <<UNITEOF
[Unit]
Description=Kore Framework Auto Sync
After=default.target

[Service]
Type=simple
ExecStart=${_venv_py} ${SCRIPTS}/watch_sync.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNITEOF
        systemctl --user daemon-reload
        systemctl --user enable --now claude-oracle-sync.service
        ok "claude-oracle-sync.service installed and started"
    else
        warn "Skipped. Run later: python3 scripts/install.py"
    fi

    # Start / restart abstract-fs.service. The unit file is always rewritten
    # by sync.py in Step 8 above, so we must daemon-reload and restart even
    # if the unit was already active — otherwise the daemon keeps running
    # with the old PRELOAD_REPO_PATHS / paths.
    echo ""
    if [[ -f "$_fs_unit" ]]; then
        systemctl --user daemon-reload
        _fs_active=$(systemctl --user is-active abstract-fs.service 2>/dev/null || echo "inactive")
        if [[ "$_fs_active" == "active" ]]; then
            info "abstract-fs.service already running — restarting to pick up new unit..."
            systemctl --user restart abstract-fs.service && \
                ok "abstract-fs.service restarted" || \
                warn "Failed to restart abstract-fs.service — check: journalctl --user -u abstract-fs.service"
        else
            info "abstract-fs.service unit installed by sync — starting it now..."
            systemctl --user enable --now abstract-fs.service && \
                ok "abstract-fs.service started" || \
                warn "Failed to start abstract-fs.service — check: journalctl --user -u abstract-fs.service"
        fi
    else
        info "abstract-fs.service unit not found (only present in daemon mode)"
    fi
fi

# ════════════════════════════════════════════════════════════
# STEP 10 — Verify
# ════════════════════════════════════════════════════════════
section "Step 10: Verifying installation"

if "$PYTHON" "$SCRIPTS/verify.py"; then
    ok "All checks passed"
else
    warn "Some checks did not pass — review the output above."
    warn "Re-run after fixing issues:  python3 scripts/verify.py"
fi

# ════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════
echo ""
hr
echo -e "${BOLD}${GREEN}  ✓ Kore Framework installed successfully!${NC}"
hr
echo ""
echo "  What was deployed:"
echo "    ~/.claude/CLAUDE.md         — oracle system prompt"
echo "    ~/.claude/agents/           — all agent definitions"
echo "    ~/.claude/skills/           — callable skills (delta-team, alpha-team, ...)"
echo "    ~/.claude/rules/global.md   — behavioral rules"
echo "    ~/.claude/hooks/rtk-rewrite.sh"
echo "    ~/.claude.json              — abstract-fs MCP wired"
echo "    ~/.gemini/, ~/.codex/       — Gemini and Codex synced"
echo ""
echo "  Useful commands:"
echo "    Verify:       python3 scripts/verify.py"
echo "    Re-sync:      python3 scripts/sync.py"
echo "    Init models:  python3 scripts/init_models.py"
echo "    Uninstall:    bash uninstall.sh"
echo ""
if [[ "$_os" == "Darwin" ]]; then
    echo "  Service logs:"
    echo "    tail -f ~/.claude/debug/abstract-fs.out.log"
    echo "    tail -f ~/.claude/debug/claude-oracle-sync.out.log"
    echo ""
    echo "  Manage agents:"
    echo "    launchctl list | grep claude.oracle"
    echo "    launchctl unload ~/Library/LaunchAgents/com.claude.oracle.abstract-fs.plist"
    echo ""
else
    echo "  Service logs:"
    echo "    journalctl --user -u abstract-fs.service -f"
    echo "    journalctl --user -u claude-oracle-sync.service -f"
    echo ""
fi

# Offer to remove the original clone after a successful relocated run.
if [[ -n "${KORE_ORIGINAL_REPO:-}" && -d "$KORE_ORIGINAL_REPO" && "$KORE_ORIGINAL_REPO" != "$CANONICAL_REPO" ]]; then
    hr
    echo ""
    echo "  The installer was copied from:"
    echo -e "    ${DIM}$KORE_ORIGINAL_REPO${NC}"
    echo "  It can be safely removed — everything now lives under $CANONICAL_REPO."
    echo ""
    if confirm "Remove the original clone at $KORE_ORIGINAL_REPO?" "n"; then
        rm -rf "$KORE_ORIGINAL_REPO"
        ok "Removed: $KORE_ORIGINAL_REPO"
    else
        info "Left in place. Delete it manually whenever you're ready."
    fi
    echo ""
fi

echo -e "  ${BOLD}Restart Claude Code to load the new MCP server and agent definitions.${NC}"
echo ""
