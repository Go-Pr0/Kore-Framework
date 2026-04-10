"""RepoRegistry — per-repo bundle pool for the abstract-fs daemon.

Manages a collection of RepoBundle objects, one per resolved repository path.
Bundles are created lazily on first access and stay resident for the daemon's
lifetime (no LRU eviction — see pre-locked decisions in the refactor plan).

Usage::

    registry = RepoRegistry(base_config, embedder, reranker)
    bundle = await registry.get("/path/to/repo")
    results = bundle.index.search(...)
    await registry.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from abstract_fs_server.config import ServerConfig
from abstract_fs_server.file_watcher import FileWatcher
from abstract_fs_server.path_filter import PathFilter
from abstract_fs_server.repo_paths import repo_cache_dir
from abstract_fs_server.semantic_index import SemanticIndex

log = logging.getLogger(__name__)


def _resolve_and_validate(repo_path: str) -> str:
    """Expand ``repo_path``, stat it, and return the canonical absolute form.

    Uses ``os.stat`` rather than ``Path.exists()`` so we surface the real
    ``OSError`` (EACCES, ELOOP, ENOTDIR, etc.) instead of a blanket
    "does not exist" — which on macOS was masking a launchd-level HOME
    / symlink-resolution mismatch.
    """
    expanded = Path(repo_path).expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded

    try:
        st = os.stat(absolute)
    except FileNotFoundError as exc:
        raise ValueError(
            f"repo_path does not exist: {str(absolute)!r} "
            f"(daemon HOME={os.environ.get('HOME', '?')!r}, cwd={os.getcwd()!r}, "
            f"errno={exc.errno})"
        ) from exc
    except PermissionError as exc:
        raise ValueError(
            f"repo_path is not readable by the daemon: {str(absolute)!r} "
            f"(daemon uid={os.getuid()}, errno={exc.errno}). On macOS this "
            f"usually means the LaunchAgent needs Full Disk Access for "
            f"the containing directory."
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"repo_path could not be stat()'d: {str(absolute)!r} "
            f"(errno={exc.errno}, {exc.strerror})"
        ) from exc

    import stat as _stat
    if not _stat.S_ISDIR(st.st_mode):
        raise ValueError(
            f"repo_path is not a directory: {str(absolute)!r}"
        )

    # Only follow symlinks AFTER we know the raw path is stat-able, so a
    # broken symlink chain produces a clear error instead of a False return.
    try:
        resolved = str(absolute.resolve())
    except OSError as exc:
        log.warning(
            "resolve() failed for %s (%s); falling back to absolute path",
            absolute,
            exc,
        )
        resolved = str(absolute)

    return resolved


@dataclass
class RepoBundle:
    """All per-repo server state, held together for the daemon's lifetime."""

    repo_root: str
    config: ServerConfig
    index: Any  # AbstractIndex
    semantic: SemanticIndex | None
    watcher: FileWatcher | None
    write_lock: asyncio.Lock
    last_used: float = field(default_factory=time.monotonic)


class RepoRegistry:
    """Lazy-loading registry of per-repo bundles.

    - ``get(repo_path)`` builds and caches a RepoBundle on first call; subsequent
      calls for the same resolved path are O(1) dict lookups.
    - A single global lock serialises the build path so that two concurrent
      first-touches on the same repo do not race.
    - ``default_repo()`` returns the repo_root when exactly one bundle is
      registered (stdio compatibility shim).
    - ``shutdown()`` stops all watchers and persists all indices; call it from
      the server lifespan on exit.
    """

    def __init__(
        self,
        base_config: ServerConfig,
        embedder: Any,
        reranker: Any,
    ) -> None:
        self._base_config = base_config
        self._embedder = embedder
        self._reranker = reranker
        self._bundles: dict[str, RepoBundle] = {}
        # Single global lock: serialises the BUILD path only.
        # Once a bundle is in _bundles, subsequent gets skip the lock.
        self._global_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, repo_path: str) -> RepoBundle:
        """Return the bundle for ``repo_path``, building it lazily if needed.

        Args:
            repo_path: Absolute path to the repository root.

        Returns:
            The ``RepoBundle`` for the resolved path.

        Raises:
            ValueError: If the path does not exist or is not a directory.
        """
        resolved = _resolve_and_validate(repo_path)

        # Fast-path: already built — no lock needed.
        bundle = self._bundles.get(resolved)
        if bundle is not None:
            bundle.last_used = time.monotonic()
            return bundle

        # Slow-path: build under lock to prevent concurrent first-touches.
        async with self._global_lock:
            # Re-check inside lock in case another coroutine built it while we
            # were waiting.
            bundle = self._bundles.get(resolved)
            if bundle is not None:
                bundle.last_used = time.monotonic()
                return bundle

            bundle = await self._build(resolved)
            self._bundles[resolved] = bundle
            return bundle

    def default_repo(self) -> str | None:
        """Return the repo_root iff exactly one bundle is registered, else None.

        This is the stdio-mode compatibility shim: when the server is running in
        stdio mode with a single auto-registered repo, tools can omit repo_path
        and this method supplies the default.
        """
        if len(self._bundles) == 1:
            return next(iter(self._bundles))
        return None

    async def shutdown(self) -> None:
        """Stop all watchers and persist all indices.  Called from lifespan exit."""
        for resolved, bundle in list(self._bundles.items()):
            log.info("RepoRegistry.shutdown: stopping watcher for %s", resolved)
            if bundle.watcher is not None:
                bundle.watcher.stop()
            if bundle.index is not None:
                try:
                    await asyncio.to_thread(bundle.index.save_to_disk)
                    log.info("RepoRegistry.shutdown: index persisted for %s", resolved)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "RepoRegistry.shutdown: failed to persist index for %s: %s",
                        resolved,
                        exc,
                    )

    def preload_sync(self, repo_paths: list[str]) -> None:
        """Build bundles synchronously for ``repo_paths`` (startup preload only).

        Intended for use in ``_eager_startup()`` before the asyncio event loop
        starts.  Each bundle's abstract index is built inline; the semantic
        embedding build is offloaded to a daemon thread so it runs concurrently
        with the server coming up.

        Paths already in the registry are skipped.
        """
        for path in repo_paths:
            try:
                resolved = _resolve_and_validate(path)
            except ValueError as exc:
                log.warning(
                    "RepoRegistry.preload_sync: skipping %s: %s", path, exc
                )
                continue
            if resolved in self._bundles:
                continue
            try:
                bundle = self._build_sync(resolved)
                self._bundles[resolved] = bundle
                log.info("RepoRegistry.preload_sync: loaded %s", resolved)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "RepoRegistry.preload_sync: failed for %s: %s", path, exc
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _build(self, resolved: str) -> RepoBundle:
        """Build a RepoBundle for ``resolved`` (already validated absolute path)."""
        log.info("RepoRegistry: building bundle for %s", resolved)

        # Build a per-repo ServerConfig by cloning base_config and overriding
        # the repo-specific paths.
        per_repo_cache = repo_cache_dir(resolved, self._base_config.cache_root)
        config = ServerConfig(
            repo_root=resolved,
            cache_root=self._base_config.cache_root,
            repo_cache_dir=per_repo_cache,
            abstract_index_path=os.path.join(per_repo_cache, "abstract-index.json"),
            lancedb_path=os.path.join(per_repo_cache, "semantic-index"),
            watch_files=self._base_config.watch_files,
            log_level=self._base_config.log_level,
            log_file=self._base_config.log_file,
            include_private_functions=self._base_config.include_private_functions,
            languages=self._base_config.languages,
            extra_extensions=self._base_config.extra_extensions,
            exclude_patterns=self._base_config.exclude_patterns,
            semantic_search_enabled=self._base_config.semantic_search_enabled,
            embedding_model=self._base_config.embedding_model,
            embedding_device=self._base_config.embedding_device,
            expand_dependency_docstrings=self._base_config.expand_dependency_docstrings,
        )

        # Ensure cache dir exists.
        os.makedirs(per_repo_cache, exist_ok=True)

        # Build the shared PathFilter once per repo.  Both the walker
        # (indirectly, via ``exclude_patterns``) and the FileWatcher use it
        # so their notions of "should this path be indexed?" are identical.
        path_filter = PathFilter(
            config.repo_root,
            extra_patterns=config.exclude_patterns,
        )

        # Load or build the AbstractIndex.
        from abstract_engine.index import AbstractIndex  # noqa: PLC0415

        index = await asyncio.to_thread(
            AbstractIndex.load_or_build,
            config.repo_root,
            {
                "include_private": config.include_private_functions,
                "exclude_patterns": config.exclude_patterns,
                "languages": config.languages,
                "index_path": config.abstract_index_path,
                "extra_extensions": config.extra_extensions,
            },
        )
        log.info(
            "RepoRegistry: AbstractIndex ready for %s — %d files",
            resolved,
            len(index.files),
        )

        # Build SemanticIndex (if enabled), passing the shared models.
        semantic: SemanticIndex | None = None
        if config.semantic_search_enabled:
            if self._embedder is not None:
                semantic = SemanticIndex(
                    config.lancedb_path,
                    embedder=self._embedder,
                    reranker=self._reranker,
                )
            else:
                # Fallback: lazy-load (stdio/test mode without pre-loaded models).
                semantic = SemanticIndex(
                    config.lancedb_path,
                    model=config.embedding_model,
                    device=config.embedding_device,
                )
            # Restore from disk if the fingerprint matches; only rebuild if stale.
            restored = await asyncio.to_thread(semantic.try_load_from_disk, index)
            if not restored:
                asyncio.ensure_future(semantic.async_build_from_index(index))
                log.info("RepoRegistry: SemanticIndex build scheduled for %s", resolved)
            else:
                log.info("RepoRegistry: SemanticIndex restored from disk for %s", resolved)

        # Start file watcher.
        watcher: FileWatcher | None = None
        if config.watch_files:
            watcher = FileWatcher(
                index,
                resolved,
                semantic_index=semantic,
                path_filter=path_filter,
            )
            watcher.start()

        return RepoBundle(
            repo_root=resolved,
            config=config,
            index=index,
            semantic=semantic,
            watcher=watcher,
            write_lock=asyncio.Lock(),
        )

    def _build_sync(self, resolved: str) -> RepoBundle:
        """Synchronous counterpart to ``_build`` for use before the event loop starts.

        Builds the AbstractIndex inline.  If the semantic index cannot be
        restored from disk, spawns a daemon thread to run ``build_from_index``
        so embedding happens concurrently with server startup.
        """
        log.info("RepoRegistry: building bundle (sync) for %s", resolved)

        per_repo_cache = repo_cache_dir(resolved, self._base_config.cache_root)
        config = ServerConfig(
            repo_root=resolved,
            cache_root=self._base_config.cache_root,
            repo_cache_dir=per_repo_cache,
            abstract_index_path=os.path.join(per_repo_cache, "abstract-index.json"),
            lancedb_path=os.path.join(per_repo_cache, "semantic-index"),
            watch_files=self._base_config.watch_files,
            log_level=self._base_config.log_level,
            log_file=self._base_config.log_file,
            include_private_functions=self._base_config.include_private_functions,
            languages=self._base_config.languages,
            extra_extensions=self._base_config.extra_extensions,
            exclude_patterns=self._base_config.exclude_patterns,
            semantic_search_enabled=self._base_config.semantic_search_enabled,
            embedding_model=self._base_config.embedding_model,
            embedding_device=self._base_config.embedding_device,
            expand_dependency_docstrings=self._base_config.expand_dependency_docstrings,
        )

        os.makedirs(per_repo_cache, exist_ok=True)

        path_filter = PathFilter(
            config.repo_root,
            extra_patterns=config.exclude_patterns,
        )

        from abstract_engine.index import AbstractIndex  # noqa: PLC0415

        index = AbstractIndex.load_or_build(
            config.repo_root,
            {
                "include_private": config.include_private_functions,
                "exclude_patterns": config.exclude_patterns,
                "languages": config.languages,
                "index_path": config.abstract_index_path,
                "extra_extensions": config.extra_extensions,
            },
        )
        log.info(
            "RepoRegistry: AbstractIndex (sync) ready for %s — %d files",
            resolved,
            len(index.files),
        )

        semantic: SemanticIndex | None = None
        if config.semantic_search_enabled:
            if self._embedder is not None:
                semantic = SemanticIndex(
                    config.lancedb_path,
                    embedder=self._embedder,
                    reranker=self._reranker,
                )
            else:
                semantic = SemanticIndex(
                    config.lancedb_path,
                    model=config.embedding_model,
                    device=config.embedding_device,
                )
            if not semantic.try_load_from_disk(index):
                t = threading.Thread(
                    target=semantic.build_from_index,
                    args=(index,),
                    daemon=True,
                    name=f"semantic-build-{Path(resolved).name}",
                )
                t.start()
                log.info(
                    "RepoRegistry: SemanticIndex build thread started for %s", resolved
                )
            else:
                log.info(
                    "RepoRegistry: SemanticIndex restored from disk for %s", resolved
                )

        watcher: FileWatcher | None = None
        if config.watch_files:
            watcher = FileWatcher(
                index,
                resolved,
                semantic_index=semantic,
                path_filter=path_filter,
            )
            watcher.start()

        return RepoBundle(
            repo_root=resolved,
            config=config,
            index=index,
            semantic=semantic,
            watcher=watcher,
            write_lock=asyncio.Lock(),
        )
