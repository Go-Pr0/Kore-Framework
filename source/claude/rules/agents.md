# Agent Systems

Two distinct systems exist. Which one to use depends solely on whether `/delta-team` was invoked.

## Sub-agents (default)

When `/delta-team` is NOT active, spawn sub-agents via `Agent(subagent_type=...)`. Each runs, returns, and you decide next.

Available: `ticket-agent`, `worker-agent`, `bug-identifier-agent`, `researcher-agent`, `reviewer-agent`

Sequence by need — no fixed tiers, just judgment:
- External unknowns or unfamiliar APIs → `researcher-agent` first
- Bug to diagnose before fixing → `bug-identifier-agent` first
- Multi-file or complex change → `ticket-agent` → `worker-agent`(s)
- Simple, obvious change → `worker-agent` directly
- Non-trivial implementation that warrants verification → `reviewer-agent` after workers


### Initial codebase exploration
Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for codebase exploration or code reading. Use semantic search, Read, Grep, Glob, and Bash directly.

### Don't spawn for trivial tasks

Agents carry a fixed boot cost. Handle these directly:
- Questions about the codebase, architecture, or how something works
- Typo fixes, single-line edits, renames in a known file
- Anything resolvable with ≤2 Reads and 1 Edit
- Config tweaks with a fully specified target

### Pass context into agents

When you spawn after doing your own exploration, hand over: exact file paths, symbol names, relevant line ranges, prior findings, and the specific change required. All sub-agents are instructed to trust pre-gathered context and skip re-discovery. Forward prior agent output to the next — don't restate the task, forward the concrete findings.

### Parallel workers — split by domain, never by files

When a task has genuinely independent sub-problems along domain boundaries, spawn workers in parallel — one per domain. The test: *could these halves ship as two independent PRs without either breaking the other?* If yes, parallelize. If no, one worker.

Do NOT split by file count. Files in the same domain share invariants; two workers editing them in parallel will make incompatible assumptions.

### Reviewer: quick vs full

- **Quick** — diff-only sanity check, no extra file reads. Use for small, single-domain changes. Say `quick review` in the prompt.
- **Full** — diff plus surrounding context. Use for multi-file, architectural, or security-sensitive changes.

## Model routing

Always set `model` explicitly on every sub-agent spawn.

| Model | When to use |
|-------|-------------|
| `opus` | Core logic, algorithms, security-sensitive code, architectural decisions, elusive multi-domain bugs, highly ambiguous ticket analysis |
| `sonnet` | Default. Most planning, multi-file implementation, reviews, research, diagnosis |
| `haiku` | Zero-reasoning mechanical tasks only: rename a constant, update a config key, pure search-and-replace with fully specified inputs. Any ambiguity → sonnet |

## Teammates (only inside `/delta-team`)

When `/delta-team` is invoked, you become the team lead. Spawn teammates via `TeamCreate`; they communicate via `SendMessage`. Available: `vector`, `raptor`, `recon`, `apex`
