# Claude Semantic MCP

Standalone semantic code search MCP server for native Claude Code.

This repository is the long-term replacement for the copied ACP/abstract stack pieces that were only needed during migration. Its scope is intentionally narrow:

- detect the active repository from Claude Code's working directory
- build and persist a semantic search index for that repo
- watch files and update the index incrementally
- expose search-only MCP tools to Claude Code

It is not an ACP application, not a queue/orchestrator, and not a write pipeline.

## Current Design

The server combines:

- `abstract_engine/`
  - builds the repository's abstract index
  - uses one generalized extraction protocol across all supported file types
  - emits normalized semantic regions plus heuristic symbol records

- `abstract_fs_server/`
  - runs the stdio MCP server
  - resolves repo root automatically
  - stores cache data under `~/.cache/claude-semantic-mcp/`
  - exposes semantic, keyword, and raw search tools

## Language Coverage

Today:

- Python, Rust, JavaScript, TypeScript, docs, and config files all use the same generalized indexing path.
- The indexer still labels languages by extension, but retrieval quality now comes from one shared protocol instead of per-language parser branches.

That means mixed-language repos are searchable through the same portable setup without relying on language-specific parser installs.

## MCP Behavior

This server is designed for user-scoped Claude Code MCP configuration.

Typical flow:

1. Claude Code starts the stdio MCP server.
2. The server infers the active repo root from the current working directory.
3. It loads or builds the abstract index and semantic index from cache.
4. It starts a file watcher for that repo.
5. Claude uses search tools against the active repo.

## Setup

```bash
python3 -m venv .venv
bash install.sh
```

Quick checks:

```bash
bash install.sh
SEMANTIC_DEVICE=auto PYTHONPATH=src .venv/bin/python -m abstract_fs_server.server
```

## ROCm / GPU

On ROCm systems, `install.sh` creates the venv with `--system-site-packages` when it detects a working host ROCm PyTorch install. This preserves the ROCm wheel set instead of downloading a separate CUDA build into the repo venv.

The server reads `SEMANTIC_DEVICE` and defaults it to `auto`. `auto` prefers `cuda`, then Apple `mps`, then `cpu`. You can still force `cuda`, `mps`, or `cpu` explicitly when needed.

The current default semantic stack is `jinaai/jina-code-embeddings-1.5b` plus `jinaai/jina-reranker-v3`, with the embedding model override exposed as `EMBEDDING_MODEL`.

## Main Files

- `src/abstract_fs_server/server.py`
- `src/abstract_fs_server/config.py`
- `src/abstract_fs_server/repo_paths.py`
- `src/abstract_fs_server/semantic_index.py`
- `src/abstract_fs_server/file_watcher.py`
- `src/abstract_fs_server/tools/search_tools.py`
- `src/abstract_engine/index.py`

## Notes

- Migration ticket and architecture notes live under `tickets/` and `docs/`.
- This repo should only evolve toward a cleaner semantic MCP server, not back toward ACP.
