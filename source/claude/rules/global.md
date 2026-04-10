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

Two distinct agent systems exist. Which one to use depends solely on whether `/delta-team` was invoked.

## Sub-agents (default)

When the user has NOT invoked `/delta-team`, YOU are the orchestrator. Spawn sub-agents via `Agent(subagent_type=...)` as needed. Each runs, returns a result, and you decide what to do next.

Available sub-agents: `ticket-agent`, `worker-agent`, `bug-identifier-agent`, `researcher-agent`, `reviewer-agent`

Sequence them based on what the task actually needs — no fixed tiers, just judgment:
- External unknowns or unfamiliar APIs → `researcher-agent` first
- Bug to diagnose before fixing → `bug-identifier-agent` first
- Multi-file or complex change → `ticket-agent` → `worker-agent`(s)
- Simple, obvious change → `worker-agent` directly
- Non-trivial implementation that warrants verification → `reviewer-agent` after workers

### Trivial tasks: answer directly, do NOT spawn

Agents carry a fixed boot cost (full system prompt + tool round-trips). For work that finishes in one or two direct tool calls, spawning an agent is pure overhead. Handle these yourself:

- Questions about the codebase, architecture, or how something works
- Typo fixes, single-line edits, renames in a known file
- Anything resolvable with ≤2 Reads and 1 Edit
- Config tweaks with a fully specified target

Only escalate to an agent when the work genuinely benefits from isolated context (wide exploration, multi-file edits, verification).

### Pass context into agents (no re-discovery tax)

When you spawn an agent after doing your own exploration, hand over what you already know: exact file paths, symbol names, relevant line ranges, prior findings, and the specific change required. Every re-read of a file you already opened is wasted tokens.

All sub-agents (`ticket-agent`, `worker-agent`, `bug-identifier-agent`, `reviewer-agent`) are instructed to trust pre-gathered context when supplied and skip re-discovery. Use it — don't hand them a one-line task and force them to rebuild your map.

Likewise, between sequenced agents (identifier → worker, ticket → worker, worker → reviewer): forward the prior agent's concrete findings in the next spawn prompt, not just a task restatement.

### Parallel workers — split by domain, never by files

When a task has genuinely independent sub-problems along **domain boundaries**, spawn workers in parallel in a single message — one per domain. Examples of valid splits:

- Parser changes vs. CLI wiring
- Backend API vs. frontend UI
- Auth layer vs. rate limiter
- Database migration vs. application code consuming it

The test: *could these halves ship as two independent PRs without either breaking the other?* If yes, parallelize. If no, one worker.

**Do NOT split by file count.** Files in the same domain share types, invariants, and conventions; two workers editing them in parallel will make incompatible assumptions and collide. A 10-file single-domain change is still one worker.

### Reviewer: quick vs full

- **Quick** — diff-only sanity check, no extra file reads. Use for small, single-domain changes. Say `quick review` in the prompt.
- **Full** — diff plus surrounding context, constraint checks. Use for multi-file, architectural, or security-sensitive changes.

## Model routing for sub-agents

Always set `model` explicitly when spawning any sub-agent. Three tiers:

**opus** — Reserve for phases/tasks requiring genuine deep reasoning:
- Core logic: algorithms, data transformations, state machines, complex control flow
- Security-sensitive code
- Architectural decisions affecting multiple systems
- Elusive bugs spanning multiple domains
- Ticket analysis for highly ambiguous or architecturally novel tasks

**sonnet** — Default. Use when in doubt:
- Most ticket analysis and planning
- General multi-file implementation
- Reviews, research, diagnosis

**haiku** — Only for zero-reasoning mechanical tasks with no judgment required:
- Rename a constant across files
- Update a config key or add a trivial field
- Pure search-and-replace with fully specified inputs
- If ANY ambiguity exists, use sonnet instead

For teams: the ticket's `model_hint` per phase drives executor model selection. For sub-agents: read the ticket phases and spawn each worker with the appropriate model.

## Teammates (only inside `/delta-team`)

When the user invoked `/delta-team`, you become the team lead. Teammates are spawned via `TeamCreate` and communicate via `SendMessage`. They self-route through the pipeline — you only re-enter at gates and completion.

Available teammates: `vector`, `raptor`, `recon`, `apex`

# Team Workspace Convention

Every native team run creates a workspace directory at:

  {project_root}/.team_workspace/{YYYYMMDD-HH MM-task-slug}/

This path is created by the team lead before any agent is spawned and is passed to every teammate in their spawn prompt. All agents write their artifacts here — never elsewhere.

Structure within a workspace:
  vision.md              — pipeline contract + Execution Schedule (written by team lead in two passes, read by everyone)
  ticket.json            — written by vector (waves with depends_on DAG)
  research_{topic}.md    — written by each recon agent (one file per recon)
  wave_{N}/
    handoff.json         — written by raptor for wave N (contains changes_summary)
  review.md              — written by apex (includes chosen quick|full mode)
  handoff.json           — final handoff written by delta-command at completion

No agent may create files outside this workspace directory except when editing actual production code files in the project.
