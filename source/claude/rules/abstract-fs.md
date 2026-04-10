# Abstract FS — Codebase Discovery

Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for codebase exploration or code reading. Use semantic search, Read, Grep, Glob, and Bash directly.

If the `abstract-fs` semantic MCP server is available, use it before broad file reads:

- Every tool requires a `repo_path` argument — always pass the absolute path to the repo root. Never omit it.
- **Search hierarchy**: `semantic` (intent) → `keyword` (names/signatures) → `raw` (literal strings/logs).
- **Default result budget is 15.** Do not raise `max_results` unless a more specific query genuinely won't work. Iterate with narrower queries first.
- **Prefer specific queries over broad ones.** "function that validates tokens" >> "validate". Specific queries produce focused results; vague queries burn context.
- **Pick the right mode:** `semantic` for meaning/intent, `keyword` for known names or signatures, `raw` for literal strings in file contents. Don't use `semantic` to find a literal string.
- Multiple repos can be queried in one session by varying `repo_path`. First call on a new repo triggers indexing.
- Use `semantic_status(repo_path=...)` to check index health — not to discover the repo root.
