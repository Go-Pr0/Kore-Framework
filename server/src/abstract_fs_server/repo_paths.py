"""Repo-root and cache-path helpers for the semantic MCP server."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_ROOT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "Makefile",
)

_PROCESS_ENV_PATH_KEYS = (
    "REPO_ROOT",
    "WORKSPACE_ROOT",
    "WORKSPACE",
    "PROJECT_ROOT",
    "PWD",
    "INIT_CWD",
)


def _read_parent_pid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        return None
    return None


def _read_proc_environ_paths(pid: int) -> list[Path]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return []

    paths: list[Path] = []
    for entry in raw.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        if key.decode("utf-8", errors="ignore") not in _PROCESS_ENV_PATH_KEYS:
            continue
        text = value.decode("utf-8", errors="ignore").strip()
        if text:
            paths.append(Path(text).expanduser())
    return paths


def _read_proc_cwd(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).expanduser()
    except OSError:
        return None


def _iter_probe_paths(start_path: str | None = None) -> list[Path]:
    probes: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        candidate = path if path.is_dir() else path.parent
        try:
            probes.append(candidate.resolve())
        except OSError:
            return

    if start_path:
        add(Path(start_path).expanduser())

    for key in _PROCESS_ENV_PATH_KEYS:
        value = os.environ.get(key)
        if value:
            add(Path(value).expanduser())

    add(Path(os.getcwd()).expanduser())

    pid = os.getpid()
    seen_pids: set[int] = set()
    for _ in range(12):
        if pid in seen_pids or pid <= 1:
            break
        seen_pids.add(pid)

        for env_path in _read_proc_environ_paths(pid):
            add(env_path)
        add(_read_proc_cwd(pid))

        parent_pid = _read_parent_pid(pid)
        if parent_pid is None or parent_pid == pid:
            break
        pid = parent_pid

    unique: list[Path] = []
    seen_paths: set[Path] = set()
    for probe in probes:
        if probe in seen_paths:
            continue
        seen_paths.add(probe)
        unique.append(probe)
    return unique


def _find_marked_root(path: Path, home: Path | None = None) -> Path | None:
    resolved_home = (home or Path.home()).expanduser().resolve()
    for candidate in (path, *path.parents):
        if candidate.resolve() == resolved_home:
            continue
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return None


def _is_disfavored_fallback(path: Path, home: Path) -> bool:
    path = path.resolve()
    home = home.resolve()
    if path == home:
        return True
    if path in {
        home / ".claude",
        home / ".codex",
        home / ".gemini",
        home / ".cache",
        home / ".config",
        home / ".local",
    }:
        return True
    return False


def _resolve_repo_root_from_probes(probes: list[Path], home: Path | None = None) -> str:
    home = (home or Path.home()).expanduser().resolve()
    fallback: Path | None = None

    for probe in probes:
        marked_root = _find_marked_root(probe, home=home)
        if marked_root is not None:
            return str(marked_root.resolve())
        if fallback is None and not _is_disfavored_fallback(probe, home):
            fallback = probe.resolve()

    if fallback is not None:
        return str(fallback)
    if probes:
        return str(probes[0].resolve())
    return str(Path.cwd().resolve())


def resolve_repo_root(start_path: str | None = None) -> str:
    """Infer the active repo root from process context unless REPO_ROOT is set."""

    explicit = os.environ.get("REPO_ROOT")
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    return _resolve_repo_root_from_probes(_iter_probe_paths(start_path))


def repo_cache_dir(repo_root: str, cache_root: str) -> str:
    """Return a stable per-repo cache directory."""

    resolved = str(Path(repo_root).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    leaf = Path(resolved).name or "repo"
    return str(Path(cache_root).expanduser().resolve() / f"{leaf}-{digest}")
