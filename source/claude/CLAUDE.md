# Global Rules

Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for initial codebase exploration. These generic agent types produce shallow, unfocused results. Use the semantic search, Read, Grep, Glob, and Bash directly for codebase understanding.

If the `abstract-fs` semantic MCP server is available, use it aggressively in any codebase before broad file reads:
- The server is a shared daemon (not per-session). Every tool requires a `repo_path` argument — always pass the absolute path to the repo root you are working in (typically the session `cwd` or the project root). Never omit it.
- Prefer `search_codebase` in `semantic` or `keyword` mode, plus `file_find` and `type_shape`, before falling back to raw reads or wide grep sweeps.
- Use `semantic_status(repo_path=...)` to check index health or confirm coverage — not to discover the repo root (you already know it).
- Treat `abstract-fs` as the default cross-codebase discovery layer, especially for unfamiliar repos or mixed-language projects.
- Multiple repos can be queried in one session by varying `repo_path` across calls. First call on a new repo triggers indexing (fast if previously cached).
- **Prefer specific queries over broad ones.** Natural-language phrases like "function that backs up markdown files" are far more useful than single keywords like "backup". Specific queries produce focused results; vague queries just burn context.
- **Default result budget is 15.** Do not raise `max_results` unless you genuinely need a wide sweep and have ruled out a more specific query. When exploring an unfamiliar area, iterate with narrower queries instead of fetching larger batches.
- **Pick the right mode:** `semantic` for meaning/intent ("code that validates tokens"), `keyword` for known names or signatures (`parse_candle`, `async.*order`), `raw` for literal strings in file contents (log messages, config keys, TODOs). Don't use `semantic` to find a literal string.

Always respect skills, if the user invoces a skill read & activate it and then follow it properly.

# Prompt Management

Behavioral rules for Claude Code sessions are defined by the global `~/.claude/CLAUDE.md` file plus any repo-local `CLAUDE.md` or `AGENTS.md` files in the active project.

## How it works
- The semantic MCP server does not manage Claude prompt files. It only provides search and codebase retrieval tools.
- Global behavior comes from this file plus the oracle-managed agent files in `~/.claude/agents/`, the oracle-managed rule files in `~/.claude/rules/`, and the oracle-managed skills in `~/.claude/skills/`.
- The canonical source for this global setup is `~/.claude-oracle/source/`.
- `~/.claude/teams/` is Claude-only team state and team documentation. Runtime entries may be created there during native team runs.
- `~/.claude/skills/` contains explicitly callable global skills such as `/team-lead`.
