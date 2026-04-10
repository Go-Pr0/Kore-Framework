"""Dependency tracing for the abstract-fs server.

Implements the budgeted BFS call-graph walk used by function_trace.
"""

from __future__ import annotations

from collections import deque

from abstract_engine.index import AbstractIndex
from abstract_engine.models import FunctionEntry
from abstract_engine.renderer import render_tier2_function

from abstract_fs_server.config import ServerConfig
from abstract_fs_server.adapter._helpers import (
    normalize_path,
    _resolve_function_entry,
    _find_function_entry_by_call,
    _find_function_entry_by_caller,
)


# ---------------------------------------------------------------------------
# function_trace logic (Phase 1 — budgeted dependency walk)
# ---------------------------------------------------------------------------


_CONFIDENCE_RANK = {"exact": 2, "preferred": 1, "ambiguous": 0}


def trace_dependencies(
    index: AbstractIndex,
    file_path: str,
    function_name: str,
    config: ServerConfig,
    direction: str = "both",
    depth: int = 2,
    budget_lines: int = 300,
    include: str = "contracts",
    min_confidence: str = "preferred",
) -> str:
    """Perform a budgeted BFS walk over the call graph.

    Returns a dependency tree, caller tree, ambiguous calls, and optional
    function bodies.

    Args:
        index: The abstract index.
        file_path: Path to the file containing the target function.
        function_name: Name of the function (supports ClassName.method).
        config: Server config (for repo_root).
        direction: "callees", "callers", or "both".
        depth: Maximum BFS depth (1-3).
        budget_lines: Total source lines budget for bodies.
        include: "contracts" | "full" | "mixed".
    """
    rel = normalize_path(file_path, config.repo_root)
    depth = max(1, min(3, depth))

    # Resolve the target function
    func, err = _resolve_function_entry(index, rel, function_name)
    if err:
        return err

    range_str = f":L{func.start_line}-{func.end_line}" if func.start_line else ""
    display_name = func.qualified_name or function_name
    header = f"trace for {display_name} in {rel}{range_str}"
    output_lines: list[str] = [header]

    # Track all visited functions for body collection
    # Each entry: (FunctionEntry, depth_level, file_path)
    visited_callees: list[tuple[FunctionEntry, int, str]] = []
    visited_callers: list[tuple[FunctionEntry, int, str]] = []
    ambiguous_calls: list[tuple[str, int, str, str]] = []  # (name, count, used_file, used_qname)

    # --- Callee BFS ---
    callee_tree_lines: list[str] = []
    if direction in ("callees", "both"):
        visited_keys: set[str] = {f"{rel}:{display_name}"}
        # BFS queue: (func_entry, current_depth, indent_level)
        queue: deque[tuple[FunctionEntry, int, int]] = deque()

        # Seed with the target function's calls
        for call in func.calls:
            if call.is_external or not call.resolved_file:
                continue
            callee = _find_function_entry_by_call(index, call)
            if callee is None:
                continue
            key = f"{call.resolved_file}:{call.resolved_qualified_name or call.callee_name}"
            if key in visited_keys:
                continue
            visited_keys.add(key)
            queue.append((callee, 1, 1))
            visited_callees.append((callee, 1, call.resolved_file or ""))

            # Format tree line
            name = call.resolved_qualified_name or call.callee_name
            loc = (call.resolved_file or "").rsplit("/", 1)[-1]
            line_ref = f":L{call.resolved_start_line}" if call.resolved_start_line else ""
            conf = f"[{call.resolution_confidence}]"
            callee_tree_lines.append(f"  {'  ' * 0}\u2192 {name}  {loc}{line_ref}  {conf}")

            if call.match_count > 1:
                ambiguous_calls.append((
                    call.callee_name,
                    call.match_count,
                    call.resolved_file or "",
                    call.resolved_qualified_name or "",
                ))

        while queue:
            current_func, current_depth, indent = queue.popleft()
            if current_depth >= depth:
                continue

            for call in current_func.calls:
                if call.is_external or not call.resolved_file:
                    continue
                callee = _find_function_entry_by_call(index, call)
                if callee is None:
                    continue
                key = f"{call.resolved_file}:{call.resolved_qualified_name or call.callee_name}"
                if key in visited_keys:
                    continue
                visited_keys.add(key)
                queue.append((callee, current_depth + 1, indent + 1))
                visited_callees.append((callee, current_depth + 1, call.resolved_file or ""))

                name = call.resolved_qualified_name or call.callee_name
                loc = (call.resolved_file or "").rsplit("/", 1)[-1]
                line_ref = f":L{call.resolved_start_line}" if call.resolved_start_line else ""
                conf = f"[{call.resolution_confidence}]"
                callee_tree_lines.append(f"  {'  ' * indent}\u2192 {name}  {loc}{line_ref}  {conf}")

                if call.match_count > 1:
                    ambiguous_calls.append((
                        call.callee_name,
                        call.match_count,
                        call.resolved_file or "",
                        call.resolved_qualified_name or "",
                    ))

    # --- Caller BFS ---
    caller_tree_lines: list[str] = []
    if direction in ("callers", "both"):
        visited_keys_callers: set[str] = {f"{rel}:{display_name}"}
        caller_queue: deque[tuple[FunctionEntry, int, int]] = deque()

        min_rank = _CONFIDENCE_RANK.get(min_confidence, 1)
        for caller in func.called_by:
            if _CONFIDENCE_RANK.get(caller.resolution_confidence, 0) < min_rank:
                continue
            caller_func = _find_function_entry_by_caller(index, caller)
            if caller_func is None:
                continue
            key = f"{caller.caller_file}:{caller.caller_qualified_name or caller.caller_name}"
            if key in visited_keys_callers:
                continue
            visited_keys_callers.add(key)
            caller_queue.append((caller_func, 1, 1))
            visited_callers.append((caller_func, 1, caller.caller_file))

            name = caller.caller_qualified_name or caller.caller_name
            loc = caller.caller_file.rsplit("/", 1)[-1]
            line_ref = f":L{caller.start_line}" if caller.start_line else ""
            conf = f"[{caller.resolution_confidence}]"
            caller_tree_lines.append(f"  \u2190 {name}  {loc}{line_ref}  {conf}")

        while caller_queue:
            current_func, current_depth, indent = caller_queue.popleft()
            if current_depth >= depth:
                continue

            for caller in current_func.called_by:
                if _CONFIDENCE_RANK.get(caller.resolution_confidence, 0) < min_rank:
                    continue
                caller_func = _find_function_entry_by_caller(index, caller)
                if caller_func is None:
                    continue
                key = f"{caller.caller_file}:{caller.caller_qualified_name or caller.caller_name}"
                if key in visited_keys_callers:
                    continue
                visited_keys_callers.add(key)
                caller_queue.append((caller_func, current_depth + 1, indent + 1))
                visited_callers.append((caller_func, current_depth + 1, caller.caller_file))

                name = caller.caller_qualified_name or caller.caller_name
                loc = caller.caller_file.rsplit("/", 1)[-1]
                line_ref = f":L{caller.start_line}" if caller.start_line else ""
                conf = f"[{caller.resolution_confidence}]"
                caller_tree_lines.append(f"  {'  ' * indent}\u2190 {name}  {loc}{line_ref}  {conf}")

    # --- Output dependency_graph ---
    if ambiguous_calls:
        output_lines.append("")
        output_lines.append(
            "WARNING: call graph contains ambiguous name-based resolutions. "
            "Treat the trace as a hypothesis; read real source and use find_code/raw search before fixing."
        )

    if callee_tree_lines:
        output_lines.append("")
        output_lines.append("dependency_graph:")
        output_lines.extend(callee_tree_lines)

    # --- Output caller_graph ---
    if caller_tree_lines:
        output_lines.append("")
        output_lines.append("caller_graph:")
        output_lines.extend(caller_tree_lines)

    # --- Output ambiguous_calls ---
    if ambiguous_calls:
        output_lines.append("")
        output_lines.append("ambiguous_calls:")
        seen: set[str] = set()
        for call_name, count, used_file, used_qname in ambiguous_calls:
            if call_name in seen:
                continue
            seen.add(call_name)
            used_display = used_qname or call_name
            output_lines.append(
                f"  {call_name} \u2014 {count} matches; "
                f"used {used_display} ({used_file})"
            )

    # --- Collect bodies ---
    all_graph_funcs = visited_callees + visited_callers
    if all_graph_funcs and include != "none":
        lines_used = 0
        body_sections: list[str] = []

        for graph_func, depth_level, func_file in all_graph_funcs:
            if lines_used >= budget_lines:
                break

            func_display = graph_func.qualified_name or graph_func.name
            loc_display = f"{func_file}:{graph_func.start_line}-{graph_func.end_line}"

            # Decide whether to show full source or contract
            use_full = (
                include == "full"
                or (include == "mixed" and depth_level == 1)
            )

            if use_full:
                # Extract full source from disk
                body = _extract_full_source(
                    index, func_file, graph_func, config
                )
                if body:
                    body_line_count = body.count("\n") + 1
                    if lines_used + body_line_count > budget_lines:
                        # Over budget -- fall back to contract
                        contract = graph_func.tier2_text or render_tier2_function(graph_func, func_file)
                        contract_lines = contract.count("\n") + 1
                        lines_used += contract_lines
                        body_sections.append(f"# {func_display} ({loc_display}) [contract — over budget]:")
                        body_sections.append(contract)
                    else:
                        lines_used += body_line_count
                        body_sections.append(f"# {func_display} ({loc_display}):")
                        body_sections.append(body)
                else:
                    # Could not extract -- use contract
                    contract = graph_func.tier2_text or render_tier2_function(graph_func, func_file)
                    lines_used += contract.count("\n") + 1
                    body_sections.append(f"# {func_display} ({loc_display}) [contract]:")
                    body_sections.append(contract)
            else:
                # Contract view
                contract = graph_func.tier2_text or render_tier2_function(graph_func, func_file)
                lines_used += contract.count("\n") + 1
                body_sections.append(f"# {func_display} ({loc_display}) [contract]:")
                body_sections.append(contract)

        output_lines.append("")
        output_lines.append(f"bodies (budget: {budget_lines} lines, used: {lines_used}):")
        for section in body_sections:
            output_lines.append(f"  {section}")

    if not callee_tree_lines and not caller_tree_lines:
        output_lines.append("")
        output_lines.append("(no resolved dependencies or callers found)")

    return "\n".join(output_lines)


def _extract_full_source(
    index: AbstractIndex,
    file_path: str,
    func: FunctionEntry,
    config: ServerConfig,
) -> str | None:
    """Extract the full source of a function from disk.

    Returns the source string or None if extraction fails.
    """
    class_name: str | None = None
    bare_name = func.name
    qn = func.qualified_name or ""
    if "." in qn:
        class_name = qn.rsplit(".", 1)[0]

    try:
        raw = index.get_tier3(file_path, bare_name, class_name)
        if raw.startswith("[ERROR]"):
            return None
        return raw
    except Exception:  # noqa: BLE001
        return None
