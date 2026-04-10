#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


HOME = Path.home().resolve()
KEEP_PATHS = {
    HOME / ".claude-oracle",
    HOME / ".claude",
    HOME / ".codex",
    HOME / ".gemini",
    HOME / ".claude.json",
}
TARGET_NAMES = {".claude", ".codex", ".gemini", ".claude.json"}
GUIDANCE_FILE_NAMES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md"}
SKIP_DIR_NAMES = {
    ".cache",
    ".cargo",
    ".config",
    ".local",
    ".npm",
    ".rustup",
    "node_modules",
    "snap",
}


def depth_from_home(path: Path) -> int:
    try:
        return len(path.relative_to(HOME).parts)
    except ValueError:
        return 0


def should_skip_dir(path: Path) -> bool:
    return path.name in SKIP_DIR_NAMES or any(path == keep for keep in KEEP_PATHS if keep.is_dir())


def is_under_keep_path(path: Path) -> bool:
    return any(path == keep or keep in path.parents for keep in KEEP_PATHS)


def iter_targets(include_guidance_files: bool, max_depth: int) -> list[Path]:
    targets: list[Path] = []
    for root, dirs, files in os.walk(HOME, topdown=True):
        root_path = Path(root).resolve()
        root_depth = depth_from_home(root_path)

        if root_depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [
                name for name in dirs
                if depth_from_home(root_path / name) <= max_depth
                and not should_skip_dir(root_path / name)
            ]

        for dirname in dirs:
            path = (root_path / dirname).resolve()
            if dirname in TARGET_NAMES and not is_under_keep_path(path):
                targets.append(path)

        for filename in files:
            path = (root_path / filename).resolve()
            if is_under_keep_path(path):
                continue
            if filename in TARGET_NAMES:
                targets.append(path)
            elif include_guidance_files and filename in GUIDANCE_FILE_NAMES:
                targets.append(path)

    return sorted(set(targets))


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove project-local assistant state while preserving oracle/global locations."
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete the matched paths.")
    parser.add_argument(
        "--include-guidance-files",
        action="store_true",
        help="Also remove project-local CLAUDE.md, AGENTS.md, and GEMINI.md files.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="Maximum depth under $HOME to scan. Default: 4.",
    )
    args = parser.parse_args()

    targets = iter_targets(
        include_guidance_files=args.include_guidance_files,
        max_depth=args.max_depth,
    )
    if not targets:
        print("No non-global assistant state found.")
        return

    print("Matched paths:")
    for path in targets:
        print(path)

    if not args.apply:
        print("\nDry run only. Re-run with --apply to delete these paths.")
        return

    for path in targets:
        remove_path(path)

    print(f"\nDeleted {len(targets)} paths.")


if __name__ == "__main__":
    main()
