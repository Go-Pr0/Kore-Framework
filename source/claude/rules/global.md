# Global Rules

Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for codebase exploration or code reading. These generic agent types produce shallow, unfocused results. Use semantic search (especially), Read, Grep, Glob, and Bash directly for codebase understanding.

If the `abstract-fs` semantic MCP server is available, use it aggressively in any codebase before broad file reads:
- The server is a shared daemon (not per-session). Every tool requires a `repo_path` argument — always pass the absolute path to the repo root you are working in (typically the session `cwd` or the project root). Never omit it.
- Prefer `search_codebase` in `semantic` or `keyword` mode, plus `file_find` and `type_shape`, before falling back to raw reads or wide grep sweeps.
- Use `semantic_status(repo_path=...)` to check index health or confirm coverage — not to discover the repo root (you already know it).
- Treat `abstract-fs` as the default cross-codebase discovery layer, especially for unfamiliar repos or mixed-language projects.
- Multiple repos can be queried in one session by varying `repo_path` across calls. First call on a new repo triggers indexing (fast if previously cached).
- **Prefer specific queries over broad ones.** Natural-language phrases like "function that backs up markdown files" are far more useful than single keywords like "backup". Specific queries produce focused results; vague queries just burn context.
- **Default result budget is 15.** Do not raise `max_results` unless you genuinely need a wide sweep and have ruled out a more specific query. When exploring an unfamiliar area, iterate with narrower queries instead of fetching larger batches.
- **Pick the right mode:** `semantic` for meaning/intent ("code that validates tokens"), `keyword` for known names or signatures (`parse_candle`, `async.*order`), `raw` for literal strings in file contents (log messages, config keys, TODOs). Don't use `semantic` to find a literal string.

# Agent Systems

Two distinct agent systems exist. Which one to use depends solely on whether `/team-lead` was invoked.

## Sub-agents (default)

When the user has NOT invoked `/team-lead`, YOU are the orchestrator. Spawn sub-agents via `Agent(subagent_type=...)` as needed. Each runs, returns a result, and you decide what to do next.

Available sub-agents: `ticket-agent`, `worker-agent`, `bug-identifier-agent`, `researcher-agent`, `reviewer-agent`

Sequence them based on what the task actually needs — no fixed tiers, just judgment:
- External unknowns or unfamiliar APIs → `researcher-agent` first
- Bug to diagnose before fixing → `bug-identifier-agent` first
- Multi-file or complex change → `ticket-agent` → `worker-agent`(s)
- Simple, obvious change → `worker-agent` directly
- Non-trivial implementation that warrants verification → `reviewer-agent` after workers

## Teammates (only inside `/team-lead`)

When the user invoked `/team-lead`, you become the team lead. Teammates are spawned via `TeamCreate` and communicate via `SendMessage`. They self-route through the pipeline — you only re-enter at gates and completion.

Available teammates: `team-ticket-agent`, `team-executor`, `team-researcher`, `team-reviewer`

# Team Workspace Convention

Every native team run creates a workspace directory at:

  {project_root}/.team_workspace/{YYYYMMDD-HH MM-task-slug}/

This path is created by the team lead before any agent is spawned and is passed to every teammate in their spawn prompt. All agents write their artifacts here — never elsewhere.

Structure within a workspace:
  vision.md              — pipeline contract (written by team lead, read by everyone)
  ticket.json            — written by ticket-agent
  research_{topic}.md    — written by each researcher (one file per researcher)
  phase_{N}/
    handoff.json         — written by team-executor for phase N (contains changes_summary)
  review.md              — written by team-reviewer
  handoff.json           — final handoff written by team-lead at completion

No agent may create files outside this workspace directory except when editing actual production code files in the project.
