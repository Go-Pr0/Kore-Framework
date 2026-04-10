# Claude Oracle

Claude is the source of truth for global assistant configuration on this machine.

This repo owns the global assistant setup:

- `source/claude/CLAUDE.md`
- `source/claude/agents/*.md`
- `source/claude/rules/*.md`
- `source/claude/skills/**`
- `source/claude/teams/*.md`
- `source/runtime/semantic-mcp.json`

It does not own histories, sessions, telemetry, caches, or logs from the assistant tools themselves.

## Targets

Running the sync script updates:

- `~/.claude/CLAUDE.md`
- `~/.claude/agents/*.md`
- `~/.claude/rules/*.md`
- `~/.claude/skills/**`
- `~/.claude/teams/*.md`
- `~/.claude.json` semantic MCP wiring
- `~/.gemini/GEMINI.md`
- `~/.gemini/settings.json` semantic MCP wiring
- `~/.codex/AGENTS.md`
- `~/.codex/config.toml` semantic MCP wiring

Backups are written to `backups/<timestamp>/`.

The semantic MCP wiring includes:

- `EMBEDDING_MODEL=jinaai/jina-code-embeddings-1.5b`
- `SEMANTIC_DEVICE=auto`

`auto` is intended to resolve to the best available backend on each machine instead of hard-coding Linux CUDA assumptions into generated configs.

## Usage

```bash
python3 scripts/install.py
python3 scripts/sync.py
python3 scripts/verify.py
```

## Auto Sync

Auto sync can run as a user service:

```bash
python3 scripts/install.py
```

That installer writes the user service, runs sync plus verify, and enables automatic watching of `source/`.

On Linux it installs a `systemd --user` service. On macOS it installs a `launchd` agent under `~/Library/LaunchAgents/`.

## Server (abstract-fs MCP)

The abstract-fs MCP server is bundled in `server/` (moved from the former standalone `~/Documents/claude-native-global-migration` repo). That separate repo is now obsolete.

On a fresh clone, bootstrap the server venv once:

```bash
bash server/install.sh
```

This creates `server/.venv/` with all Python dependencies. After that, the normal sync and install scripts work as usual. The `.venv/` directory is excluded from git because venv shims contain hardcoded absolute paths baked at creation time — each machine must run `install.sh` locally.

## Policy

- Edit Claude-owned source files here, not directly in the generated targets.
- Claude is the oracle.
- Codex and Gemini are generated outputs only.
- Native Claude team workflows remain Claude-only.
- The abstract-fs MCP server code now lives in `server/` within this repo. `source/runtime/semantic-mcp.json` holds the configuration that points tools at it.
