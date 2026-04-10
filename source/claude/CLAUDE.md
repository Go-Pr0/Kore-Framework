# Global Rules

Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for initial codebase exploration. These generic agent types produce shallow, unfocused results. Use the semantic search, Read, Grep, Glob, and Bash directly for codebase understanding.

If the `abstract-fs` semantic MCP server is available, use it aggressively in any codebase before broad file reads:
- Start with `semantic_status` to confirm repo root, parser/fallback coverage, and index health.
- Prefer `search_codebase` in `semantic` or `keyword` mode, plus `file_find` and `type_shape`, before falling back to raw reads or wide grep sweeps.
- Treat `abstract-fs` as the default cross-codebase discovery layer, especially for unfamiliar repos or mixed-language projects.

Always respect skills, if the user invoces a skill read & activate it and then follow it properly.

# Prompt Management

Behavioral rules for Claude Code sessions are defined by the global `~/.claude/CLAUDE.md` file plus any repo-local `CLAUDE.md` or `AGENTS.md` files in the active project.

## How it works
- The semantic MCP server does not manage Claude prompt files. It only provides search and codebase retrieval tools.
- Global behavior comes from this file plus the oracle-managed agent files in `~/.claude/agents/`, the oracle-managed rule files in `~/.claude/rules/`, and the oracle-managed skills in `~/.claude/skills/`.
- The canonical source for this global setup is `~/.claude-oracle/source/`.
- `~/.claude/teams/` is Claude-only team state and team documentation. Runtime entries may be created there during native team runs.
- `~/.claude/skills/` contains explicitly callable global skills such as `/team-lead`.
