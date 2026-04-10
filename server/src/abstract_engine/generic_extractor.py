"""Generic tree-sitter symbol extractor for arbitrary languages.

One stop shop that turns raw source bytes into function / class
``FunctionEntry`` and ``ClassEntry`` records for any language covered by
``tree_sitter_language_pack``.  Uses hand-written tree-sitter queries per
language — the same query machinery tree-sitter itself ships with — so
adding a new language means adding a ``(extension, query)`` entry, not a
parser class.

The extractor is intentionally lightweight: it pulls function names,
class/struct/interface/trait/enum names, and their line ranges.  Rich
metadata (parameters, docstrings, decorators, call graph) is out of
scope here — callers that need it should layer a language-specific
extractor on top.

Public entry point: :func:`extract_symbols`.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Language, Parser

from abstract_engine.models import ClassEntry, FunctionEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extension → tree-sitter language-pack language name
# ---------------------------------------------------------------------------

EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".lua": "lua",
}


# ---------------------------------------------------------------------------
# Per-language tree-sitter queries
#
# Capture conventions:
#   @function.def / @function.name  — any callable (function, method, ctor)
#   @class.def    / @class.name     — any type container (class, interface,
#                                     struct, enum, trait, type alias, object)
# ---------------------------------------------------------------------------

QUERIES: dict[str, str] = {
    "python": """
(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
""",
    "javascript": """
(function_declaration name: (identifier) @function.name) @function.def
(method_definition name: (property_identifier) @function.name) @function.def
(class_declaration name: (identifier) @class.name) @class.def
""",
    "typescript": """
(function_declaration name: (identifier) @function.name) @function.def
(method_definition name: (property_identifier) @function.name) @function.def
(method_signature name: (property_identifier) @function.name) @function.def
(class_declaration name: (type_identifier) @class.name) @class.def
(interface_declaration name: (type_identifier) @class.name) @class.def
(type_alias_declaration name: (type_identifier) @class.name) @class.def
(enum_declaration name: (identifier) @class.name) @class.def
""",
    "tsx": """
(function_declaration name: (identifier) @function.name) @function.def
(method_definition name: (property_identifier) @function.name) @function.def
(class_declaration name: (type_identifier) @class.name) @class.def
(interface_declaration name: (type_identifier) @class.name) @class.def
(type_alias_declaration name: (type_identifier) @class.name) @class.def
(enum_declaration name: (identifier) @class.name) @class.def
""",
    "go": """
(function_declaration name: (identifier) @function.name) @function.def
(method_declaration name: (field_identifier) @function.name) @function.def
(type_declaration (type_spec name: (type_identifier) @class.name)) @class.def
""",
    "rust": """
(function_item name: (identifier) @function.name) @function.def
(struct_item name: (type_identifier) @class.name) @class.def
(enum_item name: (type_identifier) @class.name) @class.def
(trait_item name: (type_identifier) @class.name) @class.def
(union_item name: (type_identifier) @class.name) @class.def
(type_item name: (type_identifier) @class.name) @class.def
""",
    "java": """
(method_declaration name: (identifier) @function.name) @function.def
(constructor_declaration name: (identifier) @function.name) @function.def
(class_declaration name: (identifier) @class.name) @class.def
(interface_declaration name: (identifier) @class.name) @class.def
(enum_declaration name: (identifier) @class.name) @class.def
(record_declaration name: (identifier) @class.name) @class.def
""",
    "c": """
(function_definition declarator: (function_declarator declarator: (identifier) @function.name)) @function.def
(struct_specifier name: (type_identifier) @class.name) @class.def
(union_specifier name: (type_identifier) @class.name) @class.def
(enum_specifier name: (type_identifier) @class.name) @class.def
(type_definition declarator: (type_identifier) @class.name) @class.def
""",
    "cpp": """
(function_definition declarator: (function_declarator declarator: [(identifier) (qualified_identifier) (field_identifier) (destructor_name)] @function.name)) @function.def
(class_specifier name: (type_identifier) @class.name) @class.def
(struct_specifier name: (type_identifier) @class.name) @class.def
(union_specifier name: (type_identifier) @class.name) @class.def
(enum_specifier name: (type_identifier) @class.name) @class.def
""",
    "ruby": """
(method name: (identifier) @function.name) @function.def
(singleton_method name: (identifier) @function.name) @function.def
(class name: (constant) @class.name) @class.def
(module name: (constant) @class.name) @class.def
""",
    "php": """
(function_definition name: (name) @function.name) @function.def
(method_declaration name: (name) @function.name) @function.def
(class_declaration name: (name) @class.name) @class.def
(interface_declaration name: (name) @class.name) @class.def
(trait_declaration name: (name) @class.name) @class.def
(enum_declaration name: (name) @class.name) @class.def
""",
    "bash": """
(function_definition name: (word) @function.name) @function.def
""",
    "lua": """
(function_declaration name: [(identifier) (dot_index_expression) (method_index_expression)] @function.name) @function.def
""",
    "kotlin": """
(function_declaration (simple_identifier) @function.name) @function.def
(class_declaration (type_identifier) @class.name) @class.def
(object_declaration (type_identifier) @class.name) @class.def
""",
    "csharp": """
(method_declaration name: (identifier) @function.name) @function.def
(constructor_declaration name: (identifier) @function.name) @function.def
(class_declaration name: (identifier) @class.name) @class.def
(interface_declaration name: (identifier) @class.name) @class.def
(struct_declaration name: (identifier) @class.name) @class.def
(enum_declaration name: (identifier) @class.name) @class.def
(record_declaration name: (identifier) @class.name) @class.def
""",
    "scala": """
(function_definition name: (identifier) @function.name) @function.def
(class_definition name: (identifier) @class.name) @class.def
(object_definition name: (identifier) @class.name) @class.def
(trait_definition name: (identifier) @class.name) @class.def
""",
    "swift": """
(function_declaration name: (simple_identifier) @function.name) @function.def
(class_declaration name: (type_identifier) @class.name) @class.def
(protocol_declaration name: (type_identifier) @class.name) @class.def
""",
}


# ---------------------------------------------------------------------------
# Cache for parsers and compiled queries
# ---------------------------------------------------------------------------

_parser_cache: dict[str, "Parser"] = {}
_query_cache: dict[str, object] = {}  # Query object per language


def _get_parser(lang_name: str):
    """Return a cached tree-sitter Parser for *lang_name* (or ``None``)."""
    if lang_name in _parser_cache:
        return _parser_cache[lang_name]
    try:
        from tree_sitter_language_pack import get_parser  # noqa: PLC0415

        parser = get_parser(lang_name)
        _parser_cache[lang_name] = parser
        return parser
    except Exception as exc:  # noqa: BLE001
        log.debug("generic_extractor: no parser for %s: %s", lang_name, exc)
        _parser_cache[lang_name] = None  # type: ignore[assignment]
        return None


def _get_query(lang_name: str):
    """Return a cached compiled Query for *lang_name* (or ``None``)."""
    if lang_name in _query_cache:
        return _query_cache[lang_name]
    query_src = QUERIES.get(lang_name)
    if not query_src:
        _query_cache[lang_name] = None  # type: ignore[assignment]
        return None
    try:
        from tree_sitter import Query  # noqa: PLC0415
        from tree_sitter_language_pack import get_language  # noqa: PLC0415

        language = get_language(lang_name)
        query = Query(language, query_src)
        _query_cache[lang_name] = query
        return query
    except Exception as exc:  # noqa: BLE001
        log.warning("generic_extractor: failed to compile query for %s: %s", lang_name, exc)
        _query_cache[lang_name] = None  # type: ignore[assignment]
        return None


@lru_cache(maxsize=1)
def supported_extensions() -> frozenset[str]:
    """Return the set of extensions this extractor can parse."""
    return frozenset(EXT_TO_LANG.keys())


def language_for_ext(ext: str) -> str | None:
    """Return the tree-sitter language name for *ext*, or None."""
    return EXT_TO_LANG.get(ext.lower())


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_symbols(
    relative_path: str,
    source_bytes: bytes,
    *,
    include_private: bool = False,
) -> tuple[dict[str, FunctionEntry], dict[str, ClassEntry], str | None] | None:
    """Extract functions and classes from *source_bytes* using tree-sitter.

    Args:
        relative_path: The file path (used for entry ``file_path``).
        source_bytes: Raw UTF-8 file contents.
        include_private: If False, names starting with ``_`` (Python-style
            private) are dropped.

    Returns:
        A tuple ``(functions, classes, language_name)`` on success, or
        ``None`` if the file extension is not covered by tree-sitter.
        ``functions`` and ``classes`` are dicts keyed by symbol name.
    """
    ext = os.path.splitext(relative_path)[1].lower()
    lang_name = EXT_TO_LANG.get(ext)
    if lang_name is None:
        return None

    parser = _get_parser(lang_name)
    query = _get_query(lang_name)
    if parser is None or query is None:
        return None

    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        log.debug("generic_extractor: parse failed for %s: %s", relative_path, exc)
        return None

    try:
        from tree_sitter import QueryCursor  # noqa: PLC0415

        cursor = QueryCursor(query)
        matches = cursor.matches(tree.root_node)
    except Exception as exc:  # noqa: BLE001
        log.debug("generic_extractor: query failed for %s: %s", relative_path, exc)
        return None

    functions: dict[str, FunctionEntry] = {}
    classes: dict[str, ClassEntry] = {}

    for _pattern_idx, captures in matches:
        name_node = None
        def_node = None
        kind: str | None = None

        for cap_name, nodes in captures.items():
            if not nodes:
                continue
            if cap_name == "function.name":
                name_node = nodes[0]
                kind = "function"
            elif cap_name == "class.name":
                name_node = nodes[0]
                kind = "class"
            elif cap_name == "function.def":
                def_node = nodes[0]
                if kind is None:
                    kind = "function"
            elif cap_name == "class.def":
                def_node = nodes[0]
                if kind is None:
                    kind = "class"

        if name_node is None or def_node is None or kind is None:
            continue

        try:
            name = source_bytes[name_node.start_byte : name_node.end_byte].decode(
                "utf-8", errors="replace"
            )
        except Exception:  # noqa: BLE001
            continue
        if not name:
            continue
        if not include_private and name.startswith("_") and lang_name == "python":
            # Python convention: leading underscore means private / internal.
            continue

        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1

        if kind == "function":
            if name not in functions:
                functions[name] = FunctionEntry(
                    name=name,
                    qualified_name=name,
                    file_path=relative_path,
                    start_line=start_line,
                    end_line=end_line,
                    start_byte=def_node.start_byte,
                    end_byte=def_node.end_byte,
                )
        else:
            if name not in classes:
                classes[name] = ClassEntry(
                    name=name,
                    file_path=relative_path,
                    start_line=start_line,
                    end_line=end_line,
                )

    return functions, classes, lang_name
