"""Language configuration data structures and registry.

Defines LanguageConfig and NodeTypeMap — the configuration-driven approach to
multi-language tree-sitter parsing. Language differences are expressed as
configuration + extractor callables, not inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tree_sitter import Language, Node

    from abstract_engine.models import (
        AttributeEntry,
        CallEntry,
        ConstantEntry,
        FileEntry,
        ImportEntry,
        ParameterEntry,
        TypeEntry,
    )


@dataclass(frozen=True)
class NodeTypeMap:
    """Maps abstract structural roles to language-specific tree-sitter node type strings."""

    function_def: str          # "function_definition" for Python
    class_def: str             # "class_definition" for Python
    method_def: str            # "function_definition" for Python (methods are same node type)
    function_name: str         # "identifier" for Python
    class_name: str            # "identifier" for Python
    method_name: str           # "identifier" for Python
    params_node: str           # "parameters" for Python
    body_node: str             # "block" for Python
    class_body: str            # "block" for Python
    decorated_def: str | None  # "decorated_definition" for Python
    export_wrapper: str | None  # None for Python
    docstring_style: str       # "python" for triple-quote, "jsdoc" for /** */


@dataclass
class LanguageConfig:
    """Complete configuration for parsing one language with TreeSitterParser.

    Each field is either a static value or a callable that extracts language-
    specific information from tree-sitter nodes.  Adding a new language is a
    config task: instantiate this dataclass with the right extractors and call
    ``register()``.
    """

    language_id: str                           # "python"
    extensions: frozenset[str]                 # frozenset({".py", ".pyi"})
    grammar_loader: Callable[[], Language]     # lazy loader to avoid import at module level
    node_types: NodeTypeMap

    # --- Extractor callables ---
    # Each takes specific args and returns model objects.

    extract_imports: Callable[[Node, bytes], list[ImportEntry]]
    extract_constants: Callable[[Node, bytes], dict[str, ConstantEntry]]
    extract_parameters: Callable[[Node, bytes], list[ParameterEntry]]
    extract_return_type: Callable[[Node, bytes], str | None]
    extract_docstring: Callable[[Node, bytes], tuple[str | None, str | None]]
    extract_calls: Callable[[Node, bytes], list[CallEntry]]
    extract_raises: Callable[[Node, bytes], list[str]] | None
    extract_visibility: Callable[[str], str]
    extract_decorators: Callable[[Node, bytes], list[str]] | None
    extract_types: Callable[[FileEntry, str], dict[str, TypeEntry]] | None
    extract_type_aliases: Callable[[Node, bytes, str, FileEntry], None] | None
    check_generator: Callable[[Node], bool] | None
    check_async: Callable[[Node, bytes], bool]
    extract_module_docstring: Callable[[Node, bytes], str | None]
    extract_class_attributes: Callable[[Node, bytes], tuple[list[AttributeEntry], list[AttributeEntry]]] | None
    collect_parse_errors: Callable[[Node, bytes], list[str]] | None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, LanguageConfig] = {}  # extension -> config
_LANG_REGISTRY: dict[str, LanguageConfig] = {}  # language_id -> config


def register(config: LanguageConfig) -> None:
    """Register a LanguageConfig for all its extensions and its language_id."""
    _LANG_REGISTRY[config.language_id] = config
    for ext in config.extensions:
        _REGISTRY[ext] = config


def config_for_ext(ext: str) -> LanguageConfig | None:
    """Return the LanguageConfig for a file extension, or None."""
    return _REGISTRY.get(ext)


def config_for_language(lang_id: str) -> LanguageConfig | None:
    """Return the LanguageConfig for a language id, or None."""
    return _LANG_REGISTRY.get(lang_id)


def all_extensions() -> frozenset[str]:
    """Return all registered file extensions."""
    return frozenset(_REGISTRY.keys())


def all_language_ids() -> list[str]:
    """Return all registered language ids."""
    return list(_LANG_REGISTRY.keys())
