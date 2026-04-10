"""Thin adapter over AbstractIndex — re-export layer.

All logic has been split into focused sub-modules:
  - _helpers.py       : normalize_path + function-entry resolution helpers
  - view_generator.py : Tier 1/3 views, type_shape, project_map
  - search_engine.py  : file_find (glob) and find_code (grep-index)
  - tracer.py         : budgeted BFS dependency trace

Public API is preserved here for backwards compatibility.
"""

from __future__ import annotations

# Re-export everything that callers (server.py, tools/) previously imported
# directly from are_adapter.
from abstract_fs_server.adapter._helpers import (
    normalize_path,
    _resolve_function_entry,
    _find_function_entry_by_call,
    _find_function_entry_by_caller,
)
from abstract_fs_server.adapter.view_generator import (
    get_abstract_view,
    get_tier3,
    get_type_shape_text,
    get_overview,
)
from abstract_fs_server.adapter.search_engine import (
    glob_files,
    grep_index,
    _match_function,
    _build_signature_str,
)
from abstract_fs_server.adapter.tracer import (
    trace_dependencies,
    _extract_full_source,
)

__all__ = [
    # helpers
    "normalize_path",
    "_resolve_function_entry",
    "_find_function_entry_by_call",
    "_find_function_entry_by_caller",
    # view_generator
    "get_abstract_view",
    "get_tier3",
    "get_type_shape_text",
    "get_overview",
    # search_engine
    "glob_files",
    "grep_index",
    "_match_function",
    "_build_signature_str",
    # tracer
    "trace_dependencies",
    "_extract_full_source",
]
