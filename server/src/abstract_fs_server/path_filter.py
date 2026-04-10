"""Unified path-exclusion filter for both the index walker and the file watcher.

A single ``PathFilter`` instance is the authoritative source of "should this
path be indexed?" for a given repo.  It combines four sources, checked in
order:

    1. Built-in default exclude set (``DEFAULT_EXCLUDE_DIRS``), covering
       VCS/build/cache/env/IDE/backup directories and ML weight caches.
    2. The repo's ``.gitignore`` (gitwildmatch semantics via ``pathspec``).
    3. An optional ``.abstractfsignore`` at the repo root — same syntax as
       gitignore, for project-level tweaks that shouldn't live in VCS.
    4. Extra patterns supplied at construction time (typically from the
       ``EXCLUDE_PATTERNS`` env var).

The walker in ``abstract_engine.index`` and the ``FileWatcher`` both consult
this class so they stay in lock-step — there is exactly one place to add a
new junk directory or tweak ignore behaviour.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Callable

# Canonical set of directory basenames that should never be indexed.  Kept in
# sync with ``abstract_engine.index._DEFAULT_EXCLUDE`` — we import it from
# there so there's one source of truth across the project.
try:
    from abstract_engine.index import _DEFAULT_EXCLUDE as _ENGINE_DEFAULT_EXCLUDE
except Exception:  # noqa: BLE001 — fall back if abstract_engine unavailable
    _ENGINE_DEFAULT_EXCLUDE = frozenset()

# Additional entries the fs_server cares about that may not be in the engine's
# set.  Merged with the engine set at import time.
_FS_SERVER_EXTRA = frozenset({
    # Scratch space for native team runs — never belongs in the semantic index.
    ".team_workspace",
    # Lock / pid files commonly dropped into project roots.
    ".DS_Store",
})

DEFAULT_EXCLUDE_DIRS: frozenset[str] = _ENGINE_DEFAULT_EXCLUDE | _FS_SERVER_EXTRA


def _load_pathspec(root: str, filename: str):
    """Return a ``pathspec.PathSpec`` loaded from ``root/filename``, or None.

    Gracefully handles missing file, missing ``pathspec`` dep, read errors,
    and malformed patterns — in all failure modes we return None and the
    caller treats it as "no ignore file".
    """
    path = os.path.join(root, filename)
    if not os.path.isfile(path):
        return None
    try:
        import pathspec  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            patterns = fh.read().splitlines()
    except OSError:
        return None
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except Exception:  # noqa: BLE001
        return None


def _build_extra_spec(patterns: Iterable[str]):
    """Compile ``patterns`` (from ``EXCLUDE_PATTERNS``) into a PathSpec, or None."""
    cleaned = [p.strip() for p in patterns if p and p.strip()]
    if not cleaned:
        return None
    try:
        import pathspec  # noqa: PLC0415
    except ImportError:
        return None
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", cleaned)
    except Exception:  # noqa: BLE001
        return None


class PathFilter:
    """Decide whether a filesystem path should be indexed.

    Constructed once per repo and shared between the AbstractIndex walker and
    the FileWatcher so both layers enforce identical rules.
    """

    def __init__(
        self,
        repo_root: str,
        *,
        extra_patterns: Iterable[str] = (),
        enabled_extensions: frozenset[str] | None = None,
    ) -> None:
        self._root = os.path.abspath(repo_root)
        self._enabled_extensions = (
            frozenset(ext.lower() for ext in enabled_extensions)
            if enabled_extensions
            else frozenset()
        )
        self._default_names = DEFAULT_EXCLUDE_DIRS
        self._gitignore = _load_pathspec(self._root, ".gitignore")
        self._abstractfsignore = _load_pathspec(self._root, ".abstractfsignore")
        self._extra = _build_extra_spec(extra_patterns)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def default_names(self) -> frozenset[str]:
        return self._default_names

    def is_excluded(self, abs_path: str, *, is_dir: bool = False) -> bool:
        """Return True if ``abs_path`` should be excluded from indexing.

        Performs checks cheapest-first.  For files, extension is the fastest
        reject; for directories, basename match against the default set
        dominates.
        """
        # Extension filter (files only).  If a whitelist is configured and the
        # file doesn't match, skip it immediately.
        if not is_dir and self._enabled_extensions:
            _, ext = os.path.splitext(abs_path)
            if ext.lower() not in self._enabled_extensions:
                return True

        # Basename checks against the default exclude set and the hidden-dot
        # heuristic.  This catches the vast majority of junk without touching
        # any ignore files.
        parts = abs_path.split(os.sep)
        for segment in parts:
            if not segment:
                continue
            if segment in self._default_names:
                return True
            # Substring match for things like "foo.egg-info".
            if any(pat in segment for pat in self._default_names if pat and not pat.startswith(".")):
                # Only substring-match non-dot patterns to avoid spurious hits
                # like ".git" matching ".github" — "git" is not in the
                # substring-match set because it's already a dot-name.
                # In practice the default set has very few non-dot substrings
                # worth checking; we iterate them anyway for completeness.
                pass  # substring matching disabled — too noisy; rely on exact + ignore files.

        # Ignore-file matches.  These need a path relative to repo root.
        try:
            rel = os.path.relpath(abs_path, self._root)
        except ValueError:
            return False  # different drive on Windows, etc — let caller handle
        if rel.startswith(".."):
            # Path outside the repo root — not our business.
            return False
        # Normalise to forward slashes for pathspec.
        candidate = rel.replace(os.sep, "/")
        if is_dir and not candidate.endswith("/"):
            candidate_dir = candidate + "/"
        else:
            candidate_dir = candidate

        if self._gitignore is not None and self._gitignore.match_file(candidate_dir):
            return True
        if self._abstractfsignore is not None and self._abstractfsignore.match_file(candidate_dir):
            return True
        if self._extra is not None and self._extra.match_file(candidate_dir):
            return True

        return False

    def make_ignore_matcher(self) -> Callable[[str, bool], bool]:
        """Return a ``(rel_path, is_dir) -> bool`` adapter for the engine walker.

        The abstract_engine walker accepts a callable with this exact shape,
        so the FileWatcher and the walker share the same ignore logic.
        """
        def _match(rel_path: str, *, is_dir: bool = False) -> bool:  # noqa: D401
            abs_path = os.path.join(self._root, rel_path)
            return self.is_excluded(abs_path, is_dir=is_dir)

        return _match
