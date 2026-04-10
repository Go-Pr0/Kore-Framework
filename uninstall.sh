#!/usr/bin/env bash
# ============================================================
# Kore Framework — Interactive Uninstaller
# ============================================================
# Removes everything this system deployed to ~/.claude, ~/.gemini,
# ~/.codex, ~/.claude.json, and the systemd/launchd daemons.
#
# Does NOT remove:
#   - This repo (~/.claude-oracle/)
#   - Model downloads (inside the repo)
#   - Semantic index cache (~/.cache/claude-semantic-mcp/)
#   - The RTK binary (installed system-wide, not owned by this system)
#   - Any files not placed by this system
# ============================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$REPO/source/claude"
MCP_JSON="$REPO/source/runtime/semantic-mcp.json"

HOME_DIR="$HOME"
CLAUDE_HOME="$HOME_DIR/.claude"
CLAUDE_CONFIG="$HOME_DIR/.claude.json"
CLAUDE_SETTINGS="$CLAUDE_HOME/settings.json"
CLAUDE_HOOKS="$CLAUDE_HOME/hooks"
GEMINI_HOME="$HOME_DIR/.gemini"
GEMINI_SETTINGS="$GEMINI_HOME/settings.json"
CODEX_HOME="$HOME_DIR/.codex"
CODEX_CONFIG="$CODEX_HOME/config.toml"

SYSTEMD_DIR="$HOME_DIR/.config/systemd/user"
FS_SERVICE="$SYSTEMD_DIR/abstract-fs.service"
SYNC_SERVICE="$SYSTEMD_DIR/claude-oracle-sync.service"
LAUNCH_AGENT="$HOME_DIR/Library/LaunchAgents/com.claude.oracle.sync.plist"

# ── Colors ───────────────────────────────────────────────────
BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; NC='\033[0m'

ok()      { echo -e "  ${GREEN}✓${NC} $*"; }
info()    { echo -e "  ${CYAN}>${NC} $*"; }
warn()    { echo -e "  ${YELLOW}!${NC} $*"; }
removed() { echo -e "  ${RED}-${NC} $*"; }
section() { echo ""; echo -e "${BOLD}${BLUE}━━  $*${NC}"; echo ""; }
hr()      { echo -e "${DIM}────────────────────────────────────────────────────${NC}"; }

confirm() {
    local prompt="$1" default="${2:-y}"
    local hint; [[ "$default" == "y" ]] && hint="Y/n" || hint="y/N"
    echo -ne "  ${YELLOW}?${NC} ${prompt} ${DIM}[${hint}]${NC}: "
    local ans; read -r ans
    ans="${ans:-$default}"
    [[ "${ans,,}" == "y" ]]
}

# ── Repo sanity check ────────────────────────────────────────
if [[ ! -f "$SOURCE/CLAUDE.md" ]]; then
    echo "Error: Run this script from the Kore Framework repo root." >&2
    exit 1
fi

# Resolve server name from semantic-mcp.json
MCP_SERVER_NAME="abstract-fs"
if command -v python3 &>/dev/null && [[ -f "$MCP_JSON" ]]; then
    MCP_SERVER_NAME=$(python3 - "$MCP_JSON" <<'PYEOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(d['semantic_mcp']['name'])
except Exception:
    print('abstract-fs')
PYEOF
)
fi

# ════════════════════════════════════════════════════════════
# Build the removal plan
# ════════════════════════════════════════════════════════════

# Collect agent filenames from current source + tombstones + known legacy names
_collect_agent_names() {
    # Current source agents
    if [[ -d "$SOURCE/agents" ]]; then
        find "$SOURCE/agents" -maxdepth 1 -name "*.md" -exec basename {} \;
    fi
    # Tombstoned agents (previously deployed, now removed from source)
    if [[ -d "$SOURCE/removed/agents" ]]; then
        find "$SOURCE/removed/agents" -maxdepth 1 -name "*.md" -exec basename {} \;
    fi
    # Legacy agent names from previous naming conventions (before current release)
    cat <<'LEGACY'
apex.md
raptor.md
recon.md
r-operative.md
vector.md
v-operative.md
team-executor.md
team-researcher.md
team-reviewer.md
team-ticket-agent.md
team-lead.md
team-planner.md
executor-manager.md
ticket-sub-agent.md
LEGACY
}

# Collect skill dir names from source
_collect_skill_names() {
    if [[ -d "$SOURCE/skills" ]]; then
        find "$SOURCE/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \;
    fi
    # Legacy skill names
    echo "team-lead"
}

# Collect rule filenames from source
_collect_rule_names() {
    if [[ -d "$SOURCE/rules" ]]; then
        find "$SOURCE/rules" -maxdepth 1 -name "*.md" -exec basename {} \;
    fi
}

# Build sorted unique lists
AGENT_NAMES=$(  _collect_agent_names | sort -u)
SKILL_NAMES=$(  _collect_skill_names | sort -u)
RULE_NAMES=$(   _collect_rule_names  | sort -u)

# ── Banner ───────────────────────────────────────────────────
clear 2>/dev/null || true
echo ""
echo -e "${BOLD}${RED}  ╔═══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${RED}  ║        Kore Framework — Uninstaller       ║${NC}"
echo -e "${BOLD}${RED}  ╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Repo: ${DIM}$REPO${NC}"
echo ""
echo "  This will remove everything Kore deployed to your system."
echo "  It will NOT remove this repo, model downloads, or the RTK binary."
echo ""

# ════════════════════════════════════════════════════════════
# Show removal plan
# ════════════════════════════════════════════════════════════
section "Removal plan"

_os="$(uname -s)"

echo -e "  ${BOLD}Services:${NC}"
if [[ "$_os" == "Darwin" ]]; then
    if [[ -f "$LAUNCH_AGENT" ]]; then
        echo "    [found]   $LAUNCH_AGENT"
    else
        echo "    [absent]  com.claude.oracle.sync (LaunchAgent)"
    fi
else
    if [[ -f "$FS_SERVICE" ]]; then
        echo "    [found]   $FS_SERVICE"
    else
        echo "    [absent]  abstract-fs.service"
    fi
    if [[ -f "$SYNC_SERVICE" ]]; then
        echo "    [found]   $SYNC_SERVICE"
    else
        echo "    [absent]  claude-oracle-sync.service"
    fi
fi

echo ""
echo -e "  ${BOLD}~/.claude/agents/:${NC}"
_agents_found=0
while IFS= read -r name; do
    target="$CLAUDE_HOME/agents/$name"
    if [[ -f "$target" ]]; then
        echo "    [found]   $name"
        _agents_found=$((_agents_found + 1))
    fi
done <<< "$AGENT_NAMES"
[[ $_agents_found -eq 0 ]] && echo "    (none found)"

echo ""
echo -e "  ${BOLD}~/.claude/skills/:${NC}"
_skills_found=0
while IFS= read -r name; do
    target="$CLAUDE_HOME/skills/$name"
    if [[ -d "$target" ]]; then
        echo "    [found]   $name/"
        _skills_found=$((_skills_found + 1))
    fi
done <<< "$SKILL_NAMES"
[[ $_skills_found -eq 0 ]] && echo "    (none found)"

echo ""
echo -e "  ${BOLD}~/.claude/rules/:${NC}"
_rules_found=0
while IFS= read -r name; do
    target="$CLAUDE_HOME/rules/$name"
    if [[ -f "$target" ]]; then
        echo "    [found]   $name"
        _rules_found=$((_rules_found + 1))
    fi
done <<< "$RULE_NAMES"
[[ $_rules_found -eq 0 ]] && echo "    (none found)"

echo ""
echo -e "  ${BOLD}Hooks:${NC}"
_hook="$CLAUDE_HOOKS/rtk-rewrite.sh"
if [[ -f "$_hook" ]]; then
    echo "    [found]   ~/.claude/hooks/rtk-rewrite.sh"
else
    echo "    [absent]  ~/.claude/hooks/rtk-rewrite.sh"
fi

echo ""
echo -e "  ${BOLD}Managed blocks and MCP entries (will be surgically removed):${NC}"
[[ -f "$CLAUDE_HOME/CLAUDE.md" ]]  && echo "    ~/.claude/CLAUDE.md          — managed block stripped"
[[ -f "$GEMINI_HOME/GEMINI.md" ]]  && echo "    ~/.gemini/GEMINI.md          — managed block stripped"
[[ -f "$CODEX_HOME/AGENTS.md" ]]   && echo "    ~/.codex/AGENTS.md           — managed block stripped"
[[ -f "$CLAUDE_CONFIG" ]]          && echo "    ~/.claude.json               — '$MCP_SERVER_NAME' entry removed"
[[ -f "$GEMINI_SETTINGS" ]]        && echo "    ~/.gemini/settings.json      — '$MCP_SERVER_NAME' entry removed"
[[ -f "$CODEX_CONFIG" ]]           && echo "    ~/.codex/config.toml         — managed MCP block stripped"
[[ -f "$CLAUDE_SETTINGS" ]]        && echo "    ~/.claude/settings.json      — RTK PreToolUse hook removed"

echo ""
echo -e "  ${BOLD}NOT removed:${NC}"
echo "    This repo ($REPO)"
echo "    Model downloads ($REPO/models/)"
echo "    Semantic index cache (~/.cache/claude-semantic-mcp/)"
echo "    RTK binary (system-wide install)"
echo "    Any files not placed by this system"

echo ""
hr
if ! confirm "Proceed with uninstall?" "n"; then
    echo ""
    echo "  Aborted. Nothing was changed."
    exit 0
fi

REMOVED_COUNT=0
SKIPPED_COUNT=0

# ════════════════════════════════════════════════════════════
# Stop and remove services
# ════════════════════════════════════════════════════════════
section "Stopping services"

if [[ "$_os" == "Darwin" ]]; then
    if [[ -f "$LAUNCH_AGENT" ]]; then
        launchctl unload "$LAUNCH_AGENT" 2>/dev/null && ok "Unloaded LaunchAgent" || warn "Could not unload LaunchAgent (may not be loaded)"
        rm -f "$LAUNCH_AGENT"
        removed "Removed: $LAUNCH_AGENT"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    else
        info "LaunchAgent not installed — skipping"
    fi
else
    if systemctl --user is-active abstract-fs.service &>/dev/null 2>&1; then
        systemctl --user stop abstract-fs.service
        ok "Stopped abstract-fs.service"
    fi
    if systemctl --user is-enabled abstract-fs.service &>/dev/null 2>&1; then
        systemctl --user disable abstract-fs.service 2>/dev/null || true
        ok "Disabled abstract-fs.service"
    fi
    if [[ -f "$FS_SERVICE" ]]; then
        rm -f "$FS_SERVICE"
        removed "Removed: $FS_SERVICE"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    fi

    if systemctl --user is-active claude-oracle-sync.service &>/dev/null 2>&1; then
        systemctl --user stop claude-oracle-sync.service
        ok "Stopped claude-oracle-sync.service"
    fi
    if systemctl --user is-enabled claude-oracle-sync.service &>/dev/null 2>&1; then
        systemctl --user disable claude-oracle-sync.service 2>/dev/null || true
        ok "Disabled claude-oracle-sync.service"
    fi
    if [[ -f "$SYNC_SERVICE" ]]; then
        rm -f "$SYNC_SERVICE"
        removed "Removed: $SYNC_SERVICE"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    fi

    if [[ -f "$FS_SERVICE" || -f "$SYNC_SERVICE" ]]; then
        : # already removed above
    fi
    # Reload regardless so systemd forgets them
    systemctl --user daemon-reload 2>/dev/null || true
    ok "systemd daemon-reload"
fi

# ════════════════════════════════════════════════════════════
# Remove agent files
# ════════════════════════════════════════════════════════════
section "Removing agent definitions"

while IFS= read -r name; do
    target="$CLAUDE_HOME/agents/$name"
    if [[ -f "$target" ]]; then
        rm -f "$target"
        removed "Removed: ~/.claude/agents/$name"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    fi
done <<< "$AGENT_NAMES"

_remaining=$(find "$CLAUDE_HOME/agents" -maxdepth 1 -name "*.md" 2>/dev/null | wc -l || echo 0)
if [[ "$_remaining" -gt 0 ]]; then
    info "$_remaining agent file(s) remain (not placed by this system)"
fi

# ════════════════════════════════════════════════════════════
# Remove skill directories
# ════════════════════════════════════════════════════════════
section "Removing skills"

while IFS= read -r name; do
    target="$CLAUDE_HOME/skills/$name"
    if [[ -d "$target" ]]; then
        rm -rf "$target"
        removed "Removed: ~/.claude/skills/$name/"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    fi
done <<< "$SKILL_NAMES"

# ════════════════════════════════════════════════════════════
# Remove rule files
# ════════════════════════════════════════════════════════════
section "Removing rules"

while IFS= read -r name; do
    target="$CLAUDE_HOME/rules/$name"
    if [[ -f "$target" ]]; then
        rm -f "$target"
        removed "Removed: ~/.claude/rules/$name"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    fi
done <<< "$RULE_NAMES"

# ════════════════════════════════════════════════════════════
# Remove hook script
# ════════════════════════════════════════════════════════════
section "Removing hook script"

if [[ -f "$CLAUDE_HOOKS/rtk-rewrite.sh" ]]; then
    rm -f "$CLAUDE_HOOKS/rtk-rewrite.sh"
    removed "Removed: ~/.claude/hooks/rtk-rewrite.sh"
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
else
    info "Hook script not found — skipping"
fi

# ════════════════════════════════════════════════════════════
# Strip managed blocks and remove MCP entries
# ════════════════════════════════════════════════════════════
section "Removing managed configuration"

# Helper: strip managed block from a file using Python
_strip_html_block() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    python3 - "$path" <<'PYEOF'
import re, sys, os
path = sys.argv[1]
try:
    content = open(path, encoding='utf-8').read()
    START = '<!-- BEGIN CLAUDE-ORACLE MANAGED -->'
    END   = '<!-- END CLAUDE-ORACLE MANAGED -->'
    if START not in content:
        print(f'  No managed block found in {path}')
        sys.exit(0)
    pattern = re.escape(START) + r'.*?' + re.escape(END)
    cleaned = re.sub(pattern, '', content, flags=re.DOTALL).strip()
    if cleaned:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cleaned + '\n')
        print(f'  Managed block removed from {path}')
    else:
        os.remove(path)
        print(f'  File was only the managed block — removed: {path}')
except Exception as e:
    print(f'  Warning: {e}', file=sys.stderr)
PYEOF
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
}

_strip_toml_block() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    python3 - "$path" <<'PYEOF'
import re, sys, os
path = sys.argv[1]
try:
    content = open(path, encoding='utf-8').read()
    START = '# BEGIN CLAUDE-ORACLE MANAGED MCP'
    END   = '# END CLAUDE-ORACLE MANAGED MCP'
    if START not in content:
        print(f'  No managed MCP block found in {path}')
        sys.exit(0)
    pattern = re.escape(START) + r'.*?' + re.escape(END)
    cleaned = re.sub(pattern, '', content, flags=re.DOTALL).strip()
    if cleaned:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cleaned + '\n')
        print(f'  Managed MCP block removed from {path}')
    else:
        os.remove(path)
        print(f'  File was only the managed block — removed: {path}')
except Exception as e:
    print(f'  Warning: {e}', file=sys.stderr)
PYEOF
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
}

# ~/.claude/CLAUDE.md
if [[ -f "$CLAUDE_HOME/CLAUDE.md" ]]; then
    info "Stripping managed block from ~/.claude/CLAUDE.md"
    _strip_html_block "$CLAUDE_HOME/CLAUDE.md"
fi

# ~/.gemini/GEMINI.md
if [[ -f "$GEMINI_HOME/GEMINI.md" ]]; then
    info "Stripping managed block from ~/.gemini/GEMINI.md"
    _strip_html_block "$GEMINI_HOME/GEMINI.md"
fi

# ~/.codex/AGENTS.md
if [[ -f "$CODEX_HOME/AGENTS.md" ]]; then
    info "Stripping managed block from ~/.codex/AGENTS.md"
    _strip_html_block "$CODEX_HOME/AGENTS.md"
fi

# ~/.codex/config.toml — managed MCP block
if [[ -f "$CODEX_CONFIG" ]]; then
    info "Stripping managed MCP block from ~/.codex/config.toml"
    _strip_toml_block "$CODEX_CONFIG"
fi

# ~/.claude.json — remove abstract-fs from mcpServers (global + per-project)
if [[ -f "$CLAUDE_CONFIG" ]]; then
    info "Removing '$MCP_SERVER_NAME' from ~/.claude.json"
    python3 - "$CLAUDE_CONFIG" "$MCP_SERVER_NAME" <<'PYEOF'
import json, sys
path, server_name = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    top_mcps = data.get('mcpServers', {})
    removed = server_name in top_mcps
    top_mcps.pop(server_name, None)
    for proj_val in data.get('projects', {}).values():
        if isinstance(proj_val, dict):
            proj_val.get('mcpServers', {}).pop(server_name, None)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=True) + '\n')
    status = 'removed' if removed else 'not found'
    print(f'  {server_name} ({status}) in {path}')
except Exception as e:
    print(f'  Warning: {e}', file=sys.stderr)
PYEOF
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
fi

# ~/.gemini/settings.json — remove abstract-fs from mcpServers
if [[ -f "$GEMINI_SETTINGS" ]]; then
    info "Removing '$MCP_SERVER_NAME' from ~/.gemini/settings.json"
    python3 - "$GEMINI_SETTINGS" "$MCP_SERVER_NAME" <<'PYEOF'
import json, sys
path, server_name = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    removed = server_name in data.get('mcpServers', {})
    data.get('mcpServers', {}).pop(server_name, None)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=True) + '\n')
    status = 'removed' if removed else 'not found'
    print(f'  {server_name} ({status}) in {path}')
except Exception as e:
    print(f'  Warning: {e}', file=sys.stderr)
PYEOF
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
fi

# ~/.claude/settings.json — remove RTK PreToolUse hook
if [[ -f "$CLAUDE_SETTINGS" ]]; then
    info "Removing RTK hook from ~/.claude/settings.json"
    python3 - "$CLAUDE_SETTINGS" <<'PYEOF'
import json, sys
path = sys.argv[1]
MARKER = 'rtk-rewrite'
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    hooks = data.get('hooks', {})
    pre = hooks.get('PreToolUse', [])
    filtered = [
        entry for entry in pre
        if not any(MARKER in h.get('command', '') for h in entry.get('hooks', []))
    ]
    changed = len(filtered) != len(pre)
    if changed:
        if filtered:
            hooks['PreToolUse'] = filtered
        else:
            hooks.pop('PreToolUse', None)
        if not hooks:
            data.pop('hooks', None)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=True) + '\n')
        print(f'  RTK hook removed from {path}')
    else:
        print(f'  RTK hook not found in {path}')
except Exception as e:
    print(f'  Warning: {e}', file=sys.stderr)
PYEOF
    REMOVED_COUNT=$((REMOVED_COUNT + 1))
fi

# ════════════════════════════════════════════════════════════
# Summary
# ════════════════════════════════════════════════════════════
echo ""
hr
echo -e "${BOLD}${GREEN}  ✓ Kore Framework uninstalled${NC}"
hr
echo ""
echo "  $REMOVED_COUNT item(s) processed."
echo ""
echo "  What remains on your system:"
echo "    $REPO/"
echo "    (models, backups, and source files — delete the repo to remove all of it)"
echo ""
echo "  What was preserved:"
echo "    ~/.claude/settings.json  — any hooks other than rtk-rewrite"
echo "    ~/.claude.json           — all MCP entries other than $MCP_SERVER_NAME"
echo "    ~/.gemini/settings.json  — all MCP entries other than $MCP_SERVER_NAME"
echo "    ~/.claude/CLAUDE.md      — any content outside the managed block"
echo "    RTK binary               — install again with: cargo install --git https://github.com/rtk-ai/rtk"
echo ""
echo -e "  ${BOLD}Restart Claude Code to apply the changes.${NC}"
echo ""
