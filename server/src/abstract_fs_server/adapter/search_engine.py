"""Index-based searching for the abstract-fs server.

Implements file_find (glob) and find_code (grep-index) logic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from abstract_engine.index import AbstractIndex
from abstract_engine.renderer import count_public_functions, file_one_liner

from abstract_fs_server.config import ServerConfig


# ---------------------------------------------------------------------------
# file_find logic
# ---------------------------------------------------------------------------


def glob_files(
    index: AbstractIndex,
    pattern: str,
    include_tier1: bool,
    config: ServerConfig,
) -> str:
    """Find files matching a glob pattern and return abstract metadata.

    Implements the internal logic for the file_find tool.
    """
    repo_path = Path(config.repo_root)

    # Support absolute paths by stripping the repo_root prefix.
    if os.path.isabs(pattern):
        try:
            pattern = os.path.relpath(pattern, config.repo_root)
        except ValueError:
            return f"Error: pattern '{pattern}' is outside repo root '{config.repo_root}'."

    try:
        matched_paths = sorted(repo_path.glob(pattern))
    except Exception as exc:  # noqa: BLE001
        return f"Error evaluating glob pattern '{pattern}': {exc}"

    results: list[str] = []
    for abs_path in matched_paths:
        if not abs_path.is_file():
            continue
        try:
            rel = os.path.relpath(str(abs_path), config.repo_root)
        except ValueError:
            rel = str(abs_path)

        file_entry = index.files.get(rel)
        if file_entry is None:
            # Not in index -- skip silently (binaries, node_modules, etc.)
            continue

        fn_count = count_public_functions(file_entry)
        one_liner = file_one_liner(file_entry)
        line = f"{rel} [{fn_count}fn, {file_entry.line_count}L]: {one_liner}"
        results.append(line)

        if include_tier1 and file_entry.tier1_text:
            # Indent tier1 text under the metadata line
            for tier1_line in file_entry.tier1_text.splitlines():
                results.append(f"  {tier1_line}")

    if not results:
        # Collect top-level directories for the helpful error message
        top_dirs = sorted(
            {
                p.name
                for p in repo_path.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            }
        )
        dirs_str = ", ".join(top_dirs) if top_dirs else "(none)"
        return (
            f"No files matching pattern: {pattern}. "
            f"Available top-level directories: {dirs_str}."
        )

    return "\n".join(results)


# ---------------------------------------------------------------------------
# find_code logic
# ---------------------------------------------------------------------------


def grep_index(
    index: AbstractIndex,
    pattern: str,
    case_sensitive: bool,
    search_in: str,
) -> str:
    """Search abstract index for functions/types matching a pattern.

    Implements the internal logic for the find_code tool.
    Returns up to 50 matches; appends a truncation note if more exist.
    """
    import re  # noqa: PLC0415

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return f"Invalid regex pattern '{pattern}': {exc}"

    matches: list[tuple[str, int, str]] = []  # (file_path, start_line, display_line)

    for rel_path, file_entry in index.files.items():
        if search_in == "types":
            # Search TypeEntry objects
            for type_name, type_entry in index.type_lookup.items():
                if compiled.search(type_name) or compiled.search(
                    type_entry.source_text
                ):
                    loc = f":{type_entry.start_line}" if type_entry.start_line else ""
                    line = f"{rel_path}{loc} {type_entry.kind} {type_name}"
                    matches.append((rel_path, type_entry.start_line, line))
            continue

        # Iterate all functions in file (top-level + class methods)
        for func in file_entry.functions.values():
            display = _match_function(
                func, rel_path, None, compiled, search_in
            )
            if display is not None:
                matches.append((rel_path, func.start_line, display))

        for cls in file_entry.classes.values():
            for method in cls.methods.values():
                display = _match_function(
                    method, rel_path, cls.name, compiled, search_in
                )
                if display is not None:
                    matches.append((rel_path, method.start_line, display))

    # Sort by (file_path, start_line)
    matches.sort(key=lambda t: (t[0], t[1]))

    cap = 50
    overflow = len(matches) - cap
    display_matches = matches[:cap]

    lines = [m[2] for m in display_matches]
    if overflow > 0:
        lines.append(
            f"...and {overflow} more. "
            "Narrow your search or use a more specific pattern."
        )

    if not lines:
        return f"No matches for pattern: {pattern}"

    return "\n".join(lines)


_SIGNATURE_RANGE_TAIL = re.compile(r"\s+L\d+-\d+\s*$")


def _match_function(func, rel_path, class_name, compiled, search_in) -> str | None:  # type: ignore[no-untyped-def]
    """Return a formatted match line or None if no match."""
    from abstract_engine.renderer import render_tier1_function  # noqa: PLC0415

    if search_in == "names":
        if not compiled.search(func.name):
            return None
    elif search_in == "signatures":
        sig = _build_signature_str(func)
        if not compiled.search(sig):
            return None
    elif search_in == "descriptions":
        doc = func.docstring_first_line or ""
        if not compiled.search(doc):
            return None
    else:  # "all" -- search the tier1 representation
        tier1_line = render_tier1_function(func, is_method=(class_name is not None))
        if not compiled.search(tier1_line):
            return None

    tier1_line = render_tier1_function(func, is_method=(class_name is not None))
    # Range is emitted in the location prefix — strip the trailing "  L42-67".
    signature = _SIGNATURE_RANGE_TAIL.sub("", tier1_line)

    if func.start_line and func.end_line and func.end_line > func.start_line:
        loc = f":{func.start_line}-{func.end_line}"
    elif func.start_line:
        loc = f":{func.start_line}"
    else:
        loc = ""

    if class_name is not None:
        return f"{rel_path}{loc} {class_name}.{signature}"
    return f"{rel_path}{loc} {signature}"


def _build_signature_str(func) -> str:  # type: ignore[no-untyped-def]
    parts = []
    for param in func.parameters:
        if param.name in ("self", "cls"):
            continue
        parts.append(param.type_annotation or param.name)
    ret = func.return_type or ""
    return f"({', '.join(parts)})->{ret}"
