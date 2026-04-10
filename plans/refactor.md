  Bottom line

  Yes, centralize it. One systemd user daemon per device, FastMCP in streamable-http mode on 127.0.0.1:8800/mcp,
  multi-repo via a per-request repo_path argument (with MCP roots as a future enhancement), LanceDB pooled
  one-per-repo, shared Jina model loaded once. Keep MCP — dropping it would cost you three bespoke client shims for
  zero benefit.

  ---
  Why MCP stays

  All three CLIs natively speak MCP Streamable HTTP in 2026:

  ┌────────────┬─────────────────────────────────────────────┬───────────────────────────────────────────────────┐
  │   Client   │                 Config file                 │                     URL form                      │
  ├────────────┼─────────────────────────────────────────────┼───────────────────────────────────────────────────┤
  │ Claude     │ ~/.claude.json → mcpServers.abstract-fs     │ {"type":"http","url":"http://127.0.0.1:8800/mcp"} │
  │ Code       │                                             │                                                   │
  ├────────────┼─────────────────────────────────────────────┼───────────────────────────────────────────────────┤
  │ Gemini CLI │ ~/.gemini/settings.json →                   │ {"httpUrl":"http://127.0.0.1:8800/mcp"}           │
  │            │ mcpServers.abstract-fs                      │                                                   │
  ├────────────┼─────────────────────────────────────────────┼───────────────────────────────────────────────────┤
  │ Codex CLI  │ ~/.codex/config.toml →                      │ url = "http://127.0.0.1:8800/mcp"                 │
  │            │ [mcp_servers.abstract-fs]                   │                                                   │
  └────────────┴─────────────────────────────────────────────┴───────────────────────────────────────────────────┘

  Zero shim code. Any other protocol (gRPC, plain HTTP, LSP) forces you to write and maintain three client adapters.
  Not worth it.

  Transport choice: streamable-http, not sse. SSE is the deprecated November-2024 transport; the March-2025 spec
  replaces it with Streamable HTTP (unified endpoint, session IDs via Mcp-Session-Id header). FastMCP 1.26 (what you
  have installed) supports it via mcp.run(transport="streamable-http").

  ---
  How the current server is shaped vs. what we need

  Good news: the on-disk layout is already multi-repo ready. repo_paths.py:171 hashes each resolved repo into
  ~/.cache/claude-semantic-mcp/<leaf>-<sha16>/ containing abstract-index.json and semantic-index/ (LanceDB). Multiple
   repos can coexist on disk today — nothing to change there.

  What's single-repo-locked:

  1. config.py:34 — ServerConfig.from_env() resolves ONE repo_root at startup by walking parent PIDs. This model is
  fundamentally stdio-shaped: "my parent is Claude Code, so I'll snoop its cwd". In a long-running daemon with no
  meaningful parent, this logic is meaningless.
  2. server.py:50-53 — Module-level singletons _index, _semantic_index, _watcher for one repo.
  3. server.py:86-138 — Lifespan builds that one repo's index/semantic index/watcher at startup.
  4. tools/search_tools.py — Every @mcp.tool() calls get_index() with no repo_path parameter.
  5. server.py:216,221 — mcp.run() with no transport arg → hardcoded stdio.

  None of this is architectural damage; it's just assumptions that need to relax.

  ---
  The refactor

  A. Server: turn singletons into a pool (the core change)

  Replace the three module-level singletons with a RepoRegistry — a dict keyed by resolved repo path, value is a
  small RepoBundle holding (AbstractIndex, SemanticIndex, FileWatcher, per_repo_lock). Lazy-create on first tool call
   that touches a repo; LRU-evict idle repos after N minutes to release RAM (not VRAM — the Jina model itself is
  global and stays resident).

  # sketch — abstract_fs_server/registry.py
  class RepoBundle:
      index: AbstractIndex
      semantic: SemanticIndex
      watcher: FileWatcher
      write_lock: asyncio.Lock   # serialize re-index writes per-repo
      last_used: float

  class RepoRegistry:
      def __init__(self, config: ServerConfig, shared_embedder, shared_reranker):
          self._bundles: dict[str, RepoBundle] = {}
          self._global_lock = asyncio.Lock()
          self._embedder = shared_embedder    # loaded ONCE at daemon startup
          self._reranker = shared_reranker

      async def get(self, repo_path: str) -> RepoBundle:
          resolved = str(Path(repo_path).expanduser().resolve())
          async with self._global_lock:
              bundle = self._bundles.get(resolved)
              if bundle is None:
                  bundle = await self._build(resolved)
                  self._bundles[resolved] = bundle
              bundle.last_used = time.monotonic()
              return bundle

      async def evict_idle(self, max_age_s: float = 1800): ...

  The Jina model is the big VRAM consumer. Load it once in the daemon lifespan, pass the same embedder/reranker
  handles to every SemanticIndex. This requires a small refactor of SemanticIndex.__init__ to accept pre-loaded
  embedder/reranker objects instead of constructing them internally. That's the entire VRAM win — N repos, one model.

  B. Tool signatures: add repo_path

  Every tool gets an optional repo_path parameter. Default resolution order:
  1. Explicit repo_path argument from the caller.
  2. MCP roots, if the client advertised any (future — implement the roots/list handler and cache the client's
  declared roots per session).
  3. Fall back to error: "No repo specified and client advertised no roots."

  Crucially, drop /proc/<ppid>/environ probing in daemon mode — the daemon's parent is systemd, not a CLI client.
  That heuristic only made sense for stdio.

  @mcp.tool()
  async def file_find(pattern: str, repo_path: str, include_tier1: bool = False) -> str:
      bundle = await registry.get(repo_path)
      await bundle.watcher.flush(timeout=1.0)
      return are_adapter.glob_files(bundle.index, pattern, include_tier1, bundle.config)

  The clients can easily pass repo_path — in Claude Code, the agent already knows its cwd. Document it in the tool
  description so the LLM learns to include it.

  C. Transport: one line in main()

  def main() -> None:
      transport = os.environ.get("MCP_TRANSPORT", "stdio")  # stdio default = backwards compat
      if transport == "streamable-http":
          mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
          mcp.settings.port = int(os.environ.get("MCP_PORT", "8800"))
      mcp.run(transport=transport)

  Bind to 127.0.0.1 only. No auth token needed for a loopback-only service; if you ever expose it, add a bearer token
   via FastMCP middleware.

  D. LanceDB concurrency

  The registry already gives you a per-repo write_lock. Use it:
  - File watcher holds the lock while applying re-index writes for that repo.
  - Query path does not take the lock — LanceDB reads are concurrent-safe.
  - This avoids the "concurrent writes to same table" foot-gun the research flagged without blocking reads.

  One caveat from research: don't fork anywhere in the daemon (e.g., no multiprocessing with default start method).
  LanceDB + internal threads + fork is a landmine. Use asyncio or threads, not processes.

  E. File watchers across many repos

  watchdog is fine, but you'll bump the inotify limit. Default fs.inotify.max_user_watches is ~8k; 20 repos × a few
  hundred dirs each eats that fast. One-time system fix:

  # /etc/sysctl.d/40-inotify.conf
  fs.inotify.max_user_watches = 524288
  fs.inotify.max_user_instances = 1024

  Also debounce — batch events in a 500ms window before triggering SemanticIndex.update_file so saves from editors
  don't cause re-embed storms.

  F. systemd user service

  # ~/.config/systemd/user/abstract-fs.service
  [Unit]
  Description=Abstract-FS semantic code search daemon
  After=default.target

  [Service]
  Type=simple
  Environment=MCP_TRANSPORT=streamable-http
  Environment=MCP_HOST=127.0.0.1
  Environment=MCP_PORT=8800
  Environment=SEMANTIC_DEVICE=cuda
  Environment=EMBEDDING_MODEL=jinaai/jina-code-embeddings-1.5b
  Environment=HF_HUB_OFFLINE=1
  Environment=HF_HUB_CACHE=%h/.cache/huggingface/hub
  Environment=PYTHONPATH=%h/Documents/claude-native-global-migration/src
  Environment=LOG_FILE=%h/.claude/debug/abstract-fs.log
  ExecStart=%h/Documents/claude-native-global-migration/.venv/bin/python -m abstract_fs_server.server
  Restart=on-failure
  RestartSec=5
  MemoryHigh=20G

  [Install]
  WantedBy=default.target

  systemctl --user enable --now abstract-fs and it boots with your session.

  G. Oracle-side changes (sync.py + semantic-mcp.json)

  Add transport fields to the runtime manifest:

  {
    "semantic_mcp": {
      "name": "abstract-fs",
      "mode": "daemon",
      "transport": "streamable-http",
      "host": "127.0.0.1",
      "port": 8800,
      "mount_path": "/mcp",
      "repo_path": "~/Documents/claude-native-global-migration",
      "python_path": "~/Documents/claude-native-global-migration/.venv/bin/python",
      "module": "abstract_fs_server.server",
      "pythonpath": "src",
      "embedding_model": "jinaai/jina-code-embeddings-1.5b",
      "semantic_device": "cuda",
      "log_file": "~/.claude/debug/abstract-fs.log"
    }
  }

  Then build_semantic_mcp_config() branches on mode:
  - mode == "daemon": emit URL-based configs for all three clients.
  - mode == "stdio": emit the current command/args/env configs (keep as escape hatch).

  Per-client emission:
  - Claude (update_claude_config): {"type": "http", "url": f"http://{host}:{port}{mount_path}"}
  - Gemini (update_gemini_settings): {"httpUrl": f"http://{host}:{port}{mount_path}"}
  - Codex (update_codex_config): a TOML block with url = "..." instead of command/args/env.

  Also emit/install the systemd unit from sync.py (or a new scripts/install_daemon.py).

  ---
  MCP roots — defer, don't skip

  The spec defines roots/list (clients advertise workspace folders to the server), but research couldn't confirm
  Claude Code / Gemini CLI / Codex CLI actually send roots today. Don't block on it. Ship with repo_path as a
  required tool parameter first. Add a roots/list handler later as sugar — when present, the server treats declared
  roots as an allow-list so models don't need to type the path every call.

  ---
  The critical open questions you should decide

  1. LRU eviction policy for RepoBundles. Full eviction (drop AbstractIndex + stop watcher + release LanceDB handle
  after 30 min idle) vs. keep-alive-forever (simpler; eats RAM, not VRAM, since the Jina model is global). I'd start
  with keep-alive and add LRU only if RAM becomes a problem.
  2. Should the daemon auto-index any repo mentioned, or require an explicit register_repo tool call first? Auto is
  ergonomic but means one bad repo_path from the model triggers a multi-minute cold index build. Explicit
  registration is safer. I lean explicit, with a warm-cache path that skips build if the LanceDB dir already exists.
  3. Stdio fallback retention. Keep the stdio path working for debugging (e.g., running the server manually against
  one repo)? I'd say yes — it's one env var and zero extra code.

  ---
  Suggested execution order

  1. Server-side: refactor SemanticIndex to accept pre-loaded embedder/reranker. Unblocks sharing the model.
  Low-risk, isolated.
  2. Add RepoRegistry + convert server.py lifespan to build the global model, empty registry. Remove the module-level
   index singletons.
  3. Add repo_path to every tool in tools/search_tools.py; route via registry.get(repo_path). Breaking change for
  existing stdio callers — document it.
  4. Add MCP_TRANSPORT env branch in main(). Test locally against all three clients via URL.
  5. Write the systemd unit; update semantic-mcp.json schema; rewrite build_semantic_mcp_config() to branch on mode.
  Update Claude/Gemini/Codex emitters.
  6. Run sync.py; restart the daemon; smoke-test from each CLI.
  7. (Later) Add roots/list handler. Add LRU eviction if needed.