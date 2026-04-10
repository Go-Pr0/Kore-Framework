#!/usr/bin/env bash
# ============================================================
# Kore Framework — Interactive Installer
# ============================================================
# Usage:  bash install.sh          (interactive)
#         bash install.sh --yes    (accept all defaults)
# ============================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$REPO/scripts"
SERVER="$REPO/server"
SOURCE_RUNTIME="$REPO/source/runtime"
ENV_FILE="$REPO/.env"
MCP_JSON="$SOURCE_RUNTIME/semantic-mcp.json"

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
[[ "${1:-}" == "--yes" ]] && YES_MODE=true

confirm() {
    # confirm "prompt" [default: y|n]
    local prompt="$1"
    local default="${2:-y}"
    if $YES_MODE; then
        echo -e "  ${DIM}?${NC} ${prompt}  ${DIM}→ auto: ${default}${NC}"
        [[ "$default" == "y" ]]
        return
    fi
    local hint; [[ "$default" == "y" ]] && hint="Y/n" || hint="y/N"
    echo -ne "  ${YELLOW}?${NC} ${prompt} ${DIM}[${hint}]${NC}: "
    local ans; read -r ans
    ans="${ans:-$default}"
    [[ "${ans,,}" == "y" ]]
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

# Set or update a KEY in .env (creates file if needed)
env_write() {
    local key="$1" value="$2"
    if [[ -f "$ENV_FILE" ]] && grep -qE "^#?[[:space:]]*${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^#[[:space:]]*${key}=.*|${key}=${value}|;s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
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
echo ""
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

# ── Normalize semantic-mcp.json to the actual repo location ──
# semantic-mcp.json ships with hardcoded ~/.claude-oracle paths.
# Update repo_path and python_path to $REPO/server so the installer
# works correctly regardless of where the repo was cloned.
python3 - "$MCP_JSON" "$SERVER" <<'PYEOF'
import json, sys, os
path, server_dir = sys.argv[1], sys.argv[2]
home = os.path.expanduser('~')
def to_tilde(p):
    return ('~' + p[len(home):]) if p.startswith(home + '/') else p
data = json.load(open(path))
s = data['semantic_mcp']
current = os.path.expanduser(s.get('repo_path', ''))
if current != server_dir:
    s['repo_path']   = to_tilde(server_dir)
    s['python_path'] = to_tilde(server_dir + '/.venv/bin/python')
    with open(path, 'w') as f:
        f.write(json.dumps(data, indent=2) + '\n')
    print(f"  Normalized semantic-mcp.json paths to: {to_tilde(server_dir)}")
PYEOF

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
_detected="unknown"
if command -v nvidia-smi &>/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null 2>&1; then
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

echo ""
echo "  Current setting in semantic-mcp.json: ${BOLD}${_cur_device}${NC}"
echo "  Valid options: cuda | rocm | cpu | auto"
echo ""
prompt NEW_DEVICE "Device to use for semantic indexing" "$_cur_device"

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
        # Uncomment the lines if commented, or add them
        if grep -qE "^#[[:space:]]*TRANSFORMERS_OFFLINE=" "$ENV_FILE" 2>/dev/null; then
            sed -i "s|^#[[:space:]]*TRANSFORMERS_OFFLINE=.*|TRANSFORMERS_OFFLINE=1|" "$ENV_FILE"
        else
            env_write "TRANSFORMERS_OFFLINE" "1"
        fi
        if grep -qE "^#[[:space:]]*HF_HUB_OFFLINE=" "$ENV_FILE" 2>/dev/null; then
            sed -i "s|^#[[:space:]]*HF_HUB_OFFLINE=.*|HF_HUB_OFFLINE=1|" "$ENV_FILE"
        else
            env_write "HF_HUB_OFFLINE" "1"
        fi
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

_os="$(uname -s)"

if [[ "$_os" == "Darwin" ]]; then
    # macOS LaunchAgent
    _plist="$HOME/Library/LaunchAgents/com.claude.oracle.sync.plist"
    echo "  Platform: macOS — will install a LaunchAgent"
    echo "  Plist: $_plist"
    echo ""
    if [[ -f "$_plist" ]]; then
        ok "LaunchAgent already installed"
        if confirm "Reload / restart it?"; then
            launchctl unload "$_plist" 2>/dev/null || true
            launchctl load -w "$_plist"
            ok "LaunchAgent reloaded"
        fi
    elif confirm "Install auto-sync daemon?"; then
        _log_dir="$HOME/.claude/debug"
        mkdir -p "$_log_dir" "$(dirname "$_plist")"
        _venv_py="$SERVER/.venv/bin/python"
        [[ ! -f "$_venv_py" ]] && _venv_py="$PYTHON"
        # Write plist via python for reliable XML encoding
        "$PYTHON" - "$_plist" "$_venv_py" "$SCRIPTS/watch_sync.py" "$REPO" \
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
        launchctl load -w "$_plist"
        ok "LaunchAgent installed: $_plist"
    else
        warn "Skipped. Run later: python3 scripts/install.py"
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

    # Start abstract-fs.service if the unit file was written by sync.py
    echo ""
    if [[ -f "$_fs_unit" ]]; then
        _fs_active=$(systemctl --user is-active abstract-fs.service 2>/dev/null || echo "inactive")
        if [[ "$_fs_active" == "active" ]]; then
            ok "abstract-fs.service already running"
        else
            info "abstract-fs.service unit installed by sync — starting it now..."
            systemctl --user daemon-reload
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
if [[ "$_os" != "Darwin" ]]; then
    echo "  Service logs:"
    echo "    journalctl --user -u abstract-fs.service -f"
    echo "    journalctl --user -u claude-oracle-sync.service -f"
    echo ""
fi
echo -e "  ${BOLD}Restart Claude Code to load the new MCP server and agent definitions.${NC}"
echo ""
