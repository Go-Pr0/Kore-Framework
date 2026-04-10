"""View generation for the abstract-fs server.

Implements Tier 1 (file outline), Tier 3 (function source), type_shape,
and project_map logic.
"""

from __future__ import annotations

import logging
import os

from abstract_engine.index import AbstractIndex
from abstract_engine.renderer import (
    render_overview,
    render_tier1_file_compact,
    render_tier2_function,
    render_type_shape,
)

from abstract_fs_server.config import ServerConfig
from abstract_fs_server.adapter._helpers import normalize_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# file_outline / file_names logic
# ---------------------------------------------------------------------------


def get_abstract_view(
    index: AbstractIndex,
    file_path: str,
    config: ServerConfig,
    compact: bool = False,
) -> str:
    """Return the Tier 1 abstract view for a file.

    Returns the file-level overview (all functions, public + private, with
    line ranges). Fast -- served from the index cache.

    When compact=True (file_names tool): returns only function names + line
    ranges without imports/constants/docstrings.

    For dependency context on a specific function, use function_trace.
    """
    rel = normalize_path(file_path, config.repo_root)
    file_entry = index.files.get(rel)

    if file_entry is None:
        abs_path = os.path.join(config.repo_root, rel)
        if os.path.isfile(abs_path):
            logger.info("File %s found on disk but not in index -- triggering update", rel)
            try:
                index.update_file(rel)
            except Exception:  # noqa: BLE001
                pass
            file_entry = index.files.get(rel)

    if file_entry is None:
        return (
            f"File not found: {rel}. "
            "Use file_find to list available files."
        )

    if file_entry.parse_error:
        detail = file_entry.parse_error_detail or "unknown parse error"
        return (
            f"Parse failed for {rel}: {detail}. "
            "Use function_read to read the raw source."
        )

    if compact:
        return render_tier1_file_compact(file_entry)
    return file_entry.tier1_text


# ---------------------------------------------------------------------------
# function_read logic (Tier 3)
# ---------------------------------------------------------------------------


def get_tier3(
    index: AbstractIndex,
    file_path: str,
    function_name: str,
    config: ServerConfig,
    include_trace: bool = False,
    trace_depth: int = 1,
    trace_direction: str = "both",
    trace_include: str = "contracts",
    trace_budget_lines: int = 150,
) -> str:
    """Return the full source of a single function with line numbers.

    Handles 'ClassName.method_name' syntax by splitting on the first dot.
    Implements the internal logic for the function_read tool.

    When include_trace=True, appends a dependency trace (via trace_dependencies)
    after the source using the supplied trace_* parameters.
    """
    rel = normalize_path(file_path, config.repo_root)

    # Resolve class_name / function split
    class_name: str | None = None
    bare_function = function_name
    if "." in function_name:
        class_name, _, bare_function = function_name.partition(".")

    # Fetch raw source from disk via the index
    raw = index.get_tier3(rel, bare_function, class_name)

    if raw.startswith("[ERROR]"):
        # Build a helpful message listing available functions
        file_entry = index.files.get(rel)
        if file_entry is not None:
            available: list[str] = list(file_entry.functions.keys())
            for cls in file_entry.classes.values():
                for mname in cls.methods:
                    available.append(f"{cls.name}.{mname}")
            available_str = ", ".join(available) if available else "(none)"
            return (
                f"Function {function_name} not found in {rel}. "
                f"Available functions: {available_str}."
            )
        return f"Function {function_name} not found in {rel}. File may not be indexed."

    # Determine start line for numbering.  The raw text is the function source;
    # try to look up the stored start_line from the index entry.
    start_line = 1
    file_entry = index.files.get(rel)
    if file_entry is not None:
        if class_name is not None:
            cls_entry = file_entry.classes.get(class_name)
            if cls_entry is not None:
                method = cls_entry.methods.get(bare_function)
                if method is not None:
                    start_line = method.start_line
        else:
            func_entry = file_entry.functions.get(bare_function)
            if func_entry is not None:
                start_line = func_entry.start_line
            else:
                # Check class methods as fallback
                for cls_entry in file_entry.classes.values():
                    method = cls_entry.methods.get(bare_function)
                    if method is not None:
                        start_line = method.start_line
                        break

    # Prepend line numbers
    source_lines = raw.splitlines()
    end_line = start_line + len(source_lines) - 1
    header = f"{rel}:{function_name} (lines {start_line}-{end_line}):"
    numbered_lines = [
        f"  {start_line + i:4d} | {line}"
        for i, line in enumerate(source_lines)
    ]
    result = header + "\n" + "\n".join(numbered_lines)

    if include_trace:
        from abstract_fs_server.adapter.tracer import trace_dependencies  # noqa: PLC0415
        trace_output = trace_dependencies(
            index,
            file_path,
            function_name,
            config,
            direction=trace_direction,
            depth=trace_depth,
            budget_lines=trace_budget_lines,
            include=trace_include,
        )
        result += "\n\n--- dependency trace ---\n" + trace_output

    return result


# ---------------------------------------------------------------------------
# type_shape logic
# ---------------------------------------------------------------------------


def get_type_shape_text(
    index: AbstractIndex,
    type_name: str,
    include_methods: bool,
) -> str:
    """Return a compact description of a type, class, interface, or enum.

    Implements the internal logic for the type_shape tool.
    """
    # 1. Check TypeEntry objects
    type_entry = index.type_lookup.get(type_name)
    if type_entry is not None:
        return render_type_shape(type_name, type_entry=type_entry)

    # 2. Search ClassEntry objects across all files
    for rel_path, file_entry in index.files.items():
        cls = file_entry.classes.get(type_name)
        if cls is None:
            continue
        return render_type_shape(
            type_name, cls=cls, rel_path=rel_path, include_methods=include_methods
        )

    return (
        f"Type {type_name} not found in index. "
        "If this is an external library type, check the library documentation. "
        "If it is a project type, it may not be parsed yet -- "
        "call file_find to confirm the file exists and was indexed."
    )


# ---------------------------------------------------------------------------
# project_map logic
# ---------------------------------------------------------------------------


def get_overview(
    index: AbstractIndex,
    include_types: bool,
    filter_path_prefix: str | None,
    config: ServerConfig,
    exclude_path_prefix: str | None = None,
    mode: str = "auto",
) -> str:
    """Return the Tier 1 abstract view of the entire project.

    Implements the internal logic for the project_map tool.
    Files are grouped by top-level directory with blank line separators.

    mode: "auto" | "modules" | "symbols"
      - auto: uses "modules" if >80 files and no filter, else "symbols"
      - modules: one line per top-level directory
      - symbols: full per-file function listings (current behavior)
    """
    entries = list(index.files.items())

    # Apply path prefix filter
    if filter_path_prefix:
        entries = [
            (rel, fe)
            for rel, fe in entries
            if rel.startswith(filter_path_prefix)
        ]

    # Apply exclusion filter (comma-separated prefixes)
    if exclude_path_prefix:
        exclude_prefixes = [p.strip() for p in exclude_path_prefix.split(",") if p.strip()]
        entries = [
            (rel, fe)
            for rel, fe in entries
            if not any(rel.startswith(ex) for ex in exclude_prefixes)
        ]

    # Sort by relative path
    entries.sort(key=lambda t: t[0])

    # Resolve mode
    effective_mode = mode
    auto_note = ""
    if mode == "auto":
        if len(entries) > 80 and not filter_path_prefix:
            effective_mode = "modules"
            auto_note = (
                "\n[NOTE: >80 files detected, showing module-level summary. "
                "Use mode='symbols' with filter_path_prefix for per-file detail.]\n"
            )
        else:
            effective_mode = "symbols"

    repo_name = os.path.basename(config.repo_root)
    result = render_overview(
        entries,
        repo_name,
        mode=effective_mode,
        include_types=include_types,
        type_lookup=index.type_lookup if include_types else None,
    )

    if auto_note:
        result = result.rstrip("\n") + "\n" + auto_note

    return result
