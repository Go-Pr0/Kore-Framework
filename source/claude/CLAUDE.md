# Kore Framework

AI engineering system powering Claude Code sessions. Kore provides:
- **Kore Engine** — Tree-sitter AST indexing for deep semantic code analysis
- **Kore FS Server (MCP)** — `abstract-fs` semantic search over any repository
- **Write Pipeline** — Safe, validated code changes via AST verification

## File Layout

Deployed from the global `~/.claude/CLAUDE.md` file (this file) plus separate rule files, agent definitions, and skills — all managed from `~/.claude-oracle/source/`.

- Global behavior comes from this file plus the oracle-managed agent files in `~/.claude/agents/`, the oracle-managed rule files in `~/.claude/rules/`, and the oracle-managed skills in `~/.claude/skills/`.
- The canonical source for this global setup is `~/.claude-oracle/source/`.
- `~/.claude/teams/` is Claude-only team state and team documentation. Runtime entries may be created there during native team runs.
- `~/.claude/skills/` contains explicitly callable global skills such as `/delta-team`, `/bravo-team`, `/alpha-team`.

## Rules Index

All operational rules live in `~/.claude/rules/` and are loaded automatically each session:

- `abstract-fs.md`: Codebase discovery — semantic/keyword/raw search modes, result budget, query precision
- `agents.md`: Agent selection — sub-agents vs teammates, model routing, context passing, parallelism
- `code.md`: Code writing — read before editing, file splitting, scope discipline
- `git.md`: Never commit without being asked
- `teams.md`: Team workspace — directory layout and artifact conventions for team runs

## Locations

- **Agent definitions**: `~/.claude/agents/`
- **Skills** (e.g., `/delta-team`, `/bravo-team`, `/alpha-team`): `~/.claude/skills/`
- **Local overrides**: repo-local `CLAUDE.md` or `AGENTS.md` take precedence for project-specific constraints
