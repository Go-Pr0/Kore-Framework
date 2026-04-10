# Global Rules

Do not use the `Explore`, `general-purpose`, or `codebase-analyst` sub-agent types for codebase exploration or code reading. These generic agent types produce shallow, unfocused results. Use semantic search (especially), Read, Grep, Glob, and Bash directly for codebase understanding.

If the `abstract-fs` semantic MCP server is available, use it aggressively in any codebase before broad file reads:
- Start with `semantic_status` to confirm repo root, parser/fallback coverage, and index health.
- Prefer `search_codebase` in `semantic` or `keyword` mode, plus `file_find` and `type_shape`, before falling back to raw reads or wide grep sweeps.
- Treat `abstract-fs` as the default cross-codebase discovery layer, especially for unfamiliar repos or mixed-language projects.
