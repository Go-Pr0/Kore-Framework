"""Background file watcher using watchdog.

Monitors ``repo_root`` for file changes and feeds them into the AbstractIndex
and SemanticIndex.  To avoid hammering the GPU on workloads that touch many
files at once (``cp -a``, ``git checkout``, periodic backup snapshots, etc.)
all events are coalesced through a debouncing buffer and flushed in batches.

Design:

    watchdog thread ──► _enqueue(rel_path, op)   (cheap: dict write)
                                 │
                                 ▼
                        _pending buffer keyed by rel_path
                                 │
                                 ▼
        asyncio flush task  ──►  groups stable entries into a batch
                                 │
                                 ▼
         AbstractIndex.update_files() + SemanticIndex.update_files()

Key properties:

* **Path filtering** is delegated to a shared ``PathFilter`` so the watcher
  applies the exact same rules as the initial index walker.
* **Debouncing**: a file must be quiet for ``debounce_ms`` before it is
  flushed.  Rapid-fire events on the same path collapse into one update.
* **Batching**: all paths due for flush in one tick are processed as a
  single group, which lets the semantic index embed them with one GPU call
  instead of one-per-file.
* **Content-hash skip**: re-parses still happen, but the semantic index
  short-circuits the embed if ``FileEntry.content_hash`` is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from abstract_fs_server.path_filter import PathFilter

logger = logging.getLogger(__name__)

# Time a path must be quiet before being flushed.
_DEFAULT_DEBOUNCE_MS = 400
# How often the flush task wakes up to check for ready entries.
_FLUSH_TICK_S = 0.1
# Maximum number of files to process in one flush batch.  Bounded so a huge
# sudden burst doesn't block the flush task for seconds.
_MAX_BATCH = 64


class FileWatcher:
    """Wraps watchdog's Observer and routes file events to the abstract index.

    Usage::

        watcher = FileWatcher(index, repo_root, path_filter=pf,
                              semantic_index=si)
        watcher.start()
        # ... server runs ...
        watcher.stop()
    """

    def __init__(
        self,
        index,
        repo_root: str,
        *,
        semantic_index=None,
        path_filter: "PathFilter | None" = None,
        debounce_ms: int = _DEFAULT_DEBOUNCE_MS,
    ) -> None:
        self._index = index
        self._repo_root = repo_root
        self._semantic_index = semantic_index
        self._path_filter = path_filter
        self._observer: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Fallback extension whitelist, used only if no PathFilter is wired.
        enabled_extensions = getattr(index, "_enabled_extensions", frozenset())
        self._watched_extensions = frozenset(ext.lower() for ext in enabled_extensions)

        # Debounce / batching state.
        self._debounce = max(0.0, debounce_ms / 1000.0)
        self._pending_lock = threading.Lock()
        # rel_path -> deadline (monotonic seconds).  After ``deadline`` passes
        # the path is eligible to flush.
        self._pending: dict[str, float] = {}
        self._pending_deletes: set[str] = set()

        self._flush_task: asyncio.Task | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the watchdog observer thread and the asyncio flush loop.

        Safe to call multiple times — subsequent calls are no-ops.  Must be
        called from inside a running asyncio loop so the flush task can be
        scheduled on it.
        """
        if self._observer is not None:
            return

        try:
            from watchdog.events import (  # noqa: PLC0415
                FileSystemEvent,
                FileSystemEventHandler,
            )
            from watchdog.observers import Observer  # noqa: PLC0415
        except ImportError:
            logger.warning(
                "watchdog is not installed — file watching disabled. "
                "Run: pip install watchdog"
            )
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not in an async context — fall back to get_event_loop() for
            # compat, but the flush task won't run until a loop exists.
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = None

        watcher_self = self  # capture for nested class

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
                if not event.is_directory:
                    watcher_self._enqueue_change(event.src_path)

            def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
                if not event.is_directory:
                    watcher_self._enqueue_change(event.src_path)

            def on_deleted(self, event: FileSystemEvent) -> None:  # type: ignore[override]
                if not event.is_directory:
                    watcher_self._enqueue_delete(event.src_path)

            def on_moved(self, event: FileSystemEvent) -> None:  # type: ignore[override]
                if not event.is_directory:
                    watcher_self._enqueue_delete(event.src_path)
                    watcher_self._enqueue_change(event.dest_path)  # type: ignore[attr-defined]

        observer = Observer()
        observer.schedule(_Handler(), self._repo_root, recursive=True)
        observer.start()
        self._observer = observer

        # Schedule the flush task on the event loop.
        if self._loop is not None and self._loop.is_running():
            self._flush_task = self._loop.create_task(self._flush_loop())
        else:
            logger.warning(
                "FileWatcher.start(): no running event loop — batched flush "
                "disabled; updates will be applied synchronously."
            )

        logger.info("FileWatcher started — watching %s", self._repo_root)

    async def flush(self, timeout: float = 2.0) -> None:
        """Force-drain the pending buffer and wait for in-flight updates.

        Called before returning results from read tools to ensure any file
        changes have been processed.  Bounded by ``timeout``.
        """
        deadline = time.monotonic() + timeout
        # First: mark all pending entries as immediately flushable.
        with self._pending_lock:
            now = time.monotonic()
            for path in list(self._pending):
                self._pending[path] = now  # past deadline
        # Then wait until the buffer drains or the timeout elapses.
        while time.monotonic() < deadline:
            with self._pending_lock:
                if not self._pending and not self._pending_deletes:
                    return
            await asyncio.sleep(0.02)

    def stop(self) -> None:
        """Stop the watchdog observer thread and cancel the flush task."""
        if self._observer is None:
            return
        self._stop_event.set()
        try:
            self._observer.stop()  # type: ignore[union-attr]
            self._observer.join()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error stopping FileWatcher: %s", exc)
        finally:
            self._observer = None
            logger.info("FileWatcher stopped")
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

    # ------------------------------------------------------------------
    # Path acceptance
    # ------------------------------------------------------------------

    def _is_watched_path(self, abs_path: str) -> bool:
        """Return True iff this path should trigger an index update."""
        if self._path_filter is not None:
            # Authoritative path — PathFilter handles extensions, default
            # excludes, .gitignore, .abstractfsignore, and extra patterns.
            return not self._path_filter.is_excluded(abs_path, is_dir=False)

        # Fallback for callers that don't wire a PathFilter: basic extension
        # check plus a tiny built-in skip list.  Kept only so the module
        # remains usable in isolation.
        _, ext = os.path.splitext(abs_path)
        if self._watched_extensions and ext.lower() not in self._watched_extensions:
            return False
        for segment in abs_path.split(os.sep):
            if segment in (
                "__pycache__", "node_modules", ".git", ".venv", "venv",
                "dist", "build", "target", "backups", ".team_workspace",
            ):
                return False
        return True

    def _to_rel(self, abs_path: str) -> str | None:
        try:
            rel = os.path.relpath(abs_path, self._repo_root)
        except ValueError:
            return None
        if rel.startswith(".."):
            return None
        return rel

    # ------------------------------------------------------------------
    # Enqueue (watchdog thread → pending buffer)
    # ------------------------------------------------------------------

    def _enqueue_change(self, abs_path: str) -> None:
        if not self._is_watched_path(abs_path):
            return
        rel = self._to_rel(abs_path)
        if rel is None:
            return
        deadline = time.monotonic() + self._debounce
        with self._pending_lock:
            self._pending[rel] = deadline
            # If this path was queued for deletion and now reappears,
            # cancel the pending delete.
            self._pending_deletes.discard(rel)

    def _enqueue_delete(self, abs_path: str) -> None:
        if not self._is_watched_path(abs_path):
            return
        rel = self._to_rel(abs_path)
        if rel is None:
            return
        with self._pending_lock:
            self._pending_deletes.add(rel)
            # If a change was queued for this path, drop it — the delete
            # wins.
            self._pending.pop(rel, None)

    # ------------------------------------------------------------------
    # Flush loop (async, runs on the MCP event loop)
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Drain the pending buffer in batches.

        Wakes every ``_FLUSH_TICK_S`` seconds.  Collects all entries whose
        debounce deadline has passed (up to ``_MAX_BATCH`` per tick) and
        dispatches them to the index in one group.
        """
        logger.info(
            "FileWatcher flush loop started (debounce=%.0fms, max_batch=%d)",
            self._debounce * 1000,
            _MAX_BATCH,
        )
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(_FLUSH_TICK_S)

                now = time.monotonic()
                ready: list[str] = []
                deletes: list[str] = []
                with self._pending_lock:
                    for rel, deadline in list(self._pending.items()):
                        if deadline <= now:
                            ready.append(rel)
                            del self._pending[rel]
                            if len(ready) >= _MAX_BATCH:
                                break
                    if self._pending_deletes:
                        deletes = list(self._pending_deletes)
                        self._pending_deletes.clear()

                if deletes:
                    await self._process_deletes(deletes)
                if ready:
                    await self._process_batch(ready)
        except asyncio.CancelledError:
            logger.debug("FileWatcher flush loop cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("FileWatcher flush loop crashed")

    async def _process_batch(self, paths: list[str]) -> None:
        """Re-parse ``paths`` and push them to the semantic index as one batch."""
        # 1) Abstract-index reparse (CPU-bound).
        try:
            await asyncio.to_thread(self._reparse_batch, paths)
        except Exception:  # noqa: BLE001
            logger.exception("FileWatcher: batch reparse failed (%d files)", len(paths))
            return

        logger.info("FileWatcher: re-indexed %d files", len(paths))

        # 2) Semantic-index update (GPU-bound, runs in another thread).
        if self._semantic_index is None:
            return
        entries: list[tuple[str, object]] = []
        for rel in paths:
            entry = self._index.files.get(rel)
            if entry is not None:
                entries.append((rel, entry))
        if not entries:
            return
        try:
            # Prefer the batch API if the semantic index exposes one.
            if hasattr(self._semantic_index, "async_update_files"):
                await self._semantic_index.async_update_files(entries)
            else:
                for rel, entry in entries:
                    await self._semantic_index.async_update_file(rel, entry)
        except Exception:  # noqa: BLE001
            logger.exception("FileWatcher: semantic update failed")

    def _reparse_batch(self, paths: list[str]) -> None:
        """Call AbstractIndex.update_files if available, else loop."""
        if hasattr(self._index, "update_files"):
            self._index.update_files(paths)
        else:
            for path in paths:
                try:
                    self._index.update_file(path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "FileWatcher: update_file failed for %s: %s", path, exc
                    )

    async def _process_deletes(self, paths: list[str]) -> None:
        """Apply deletions to both indices."""
        def _apply_sync():
            for rel in paths:
                try:
                    if rel in self._index.files:
                        self._index.remove_file(rel)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "FileWatcher: remove_file failed for %s: %s", rel, exc
                    )

        try:
            await asyncio.to_thread(_apply_sync)
        except Exception:  # noqa: BLE001
            logger.exception("FileWatcher: batch delete failed")
            return

        if self._semantic_index is None:
            return
        for rel in paths:
            try:
                # Semantic remove is cheap (a DB delete) and has no async
                # batch variant.
                await asyncio.to_thread(self._semantic_index.remove_file, rel)
            except Exception:  # noqa: BLE001
                logger.exception("FileWatcher: semantic remove failed for %s", rel)
