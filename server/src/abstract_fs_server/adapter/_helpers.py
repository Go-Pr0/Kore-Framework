"""Shared helpers for the abstract-fs adapter modules.

Path normalization and function-entry resolution utilities used by
view_generator, search_engine, and tracer.
"""

from __future__ import annotations

import os

from abstract_engine.index import AbstractIndex
from abstract_engine.models import CallEntry, CallerEntry, FunctionEntry


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def normalize_path(file_path: str, repo_root: str) -> str:
    """Convert an absolute or ./relative path to a clean relative path.

    Strips any leading './' so the result matches the keys stored in
    AbstractIndex.files.
    """
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path, repo_root)
        except ValueError:
            # Can happen on Windows when paths are on different drives
            return file_path
    else:
        rel = file_path

    # Strip leading ./
    if rel.startswith("./") or rel.startswith(".\\"):
        rel = rel[2:]

    return rel


# ---------------------------------------------------------------------------
# Function resolution helpers
# ---------------------------------------------------------------------------


def _resolve_function_entry(
    index: AbstractIndex,
    file_path: str,
    function_name: str,
) -> tuple[FunctionEntry | None, str | None]:
    """Resolve a function name to a FunctionEntry.

    Handles ClassName.method syntax.

    Returns:
        (FunctionEntry or None, error_message or None)
    """
    file_entry = index.files.get(file_path)
    if file_entry is None:
        return None, f"File not found in index: {file_path}"

    class_name: str | None = None
    bare_name = function_name
    if "." in function_name:
        class_name, _, bare_name = function_name.partition(".")

    func = None
    if class_name:
        cls = file_entry.classes.get(class_name)
        if cls:
            func = cls.methods.get(bare_name)
    else:
        func = file_entry.functions.get(bare_name)
        if func is None:
            for cls in file_entry.classes.values():
                func = cls.methods.get(bare_name)
                if func:
                    break

    if func is None:
        available: list[str] = list(file_entry.functions.keys())
        for cls in file_entry.classes.values():
            for mname in cls.methods:
                available.append(f"{cls.name}.{mname}")
        return None, (
            f"Function '{function_name}' not found in {file_path}. "
            f"Available: {', '.join(available) or '(none)'}"
        )

    return func, None


def _find_function_entry_by_call(
    index: AbstractIndex,
    call: CallEntry,
) -> FunctionEntry | None:
    """Look up a FunctionEntry for a resolved CallEntry."""
    if not call.resolved_file:
        return None
    file_entry = index.files.get(call.resolved_file)
    if file_entry is None:
        return None
    qname = call.resolved_qualified_name or call.callee_name
    if "." in qname:
        class_name, _, method_name = qname.rpartition(".")
        cls = file_entry.classes.get(class_name)
        if cls:
            return cls.methods.get(method_name)
    else:
        func = file_entry.functions.get(qname)
        if func:
            return func
        for cls in file_entry.classes.values():
            m = cls.methods.get(qname)
            if m:
                return m
    return None


def _find_function_entry_by_caller(
    index: AbstractIndex,
    caller: CallerEntry,
) -> FunctionEntry | None:
    """Look up a FunctionEntry for a CallerEntry."""
    file_entry = index.files.get(caller.caller_file)
    if file_entry is None:
        return None
    qname = caller.caller_qualified_name or caller.caller_name
    if "." in qname:
        class_name, _, method_name = qname.rpartition(".")
        cls = file_entry.classes.get(class_name)
        if cls:
            return cls.methods.get(method_name)
    else:
        func = file_entry.functions.get(qname)
        if func:
            return func
        for cls in file_entry.classes.values():
            m = cls.methods.get(qname)
            if m:
                return m
    return None
