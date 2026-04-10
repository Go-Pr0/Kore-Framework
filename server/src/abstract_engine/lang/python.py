"""Python language configuration and extractor functions.

Reshapes existing extraction logic from py_utils.py and python_parser.py into
standalone extractor functions, then creates and registers PYTHON_CONFIG.
"""

from __future__ import annotations

from tree_sitter import Language, Node

from abstract_engine.lang_config import LanguageConfig, NodeTypeMap, register
from abstract_engine.models import (
    AttributeEntry,
    CallEntry,
    ConstantEntry,
    FileEntry,
    ImportEntry,
    ParameterEntry,
    TypeEntry,
)
from abstract_engine.py_utils import (
    _ALL_CAPS_RE,
    _check_generator,
    _extract_calls,
    _extract_class_attributes,
    _extract_decorators,
    _extract_docstring,
    _extract_parameters,
    _extract_raises,
    _extract_return_type,
    _node_text,
    _visibility_from_name,
)

# ---------------------------------------------------------------------------
# Grammar loader (lazy — avoids importing tree_sitter_python at module level)
# ---------------------------------------------------------------------------


def _load_python_grammar() -> Language:
    import tree_sitter_python as tspython  # noqa: PLC0415

    return Language(tspython.language())


# ---------------------------------------------------------------------------
# Extractor functions matching LanguageConfig callable signatures
# ---------------------------------------------------------------------------


def py_extract_imports(root: Node, source: bytes) -> list[ImportEntry]:
    """Extract all import statements from the module root node."""
    imports: list[ImportEntry] = []
    for child in root.children:
        if child.type == "import_statement":
            imports.extend(_parse_import_statement(child, source))
        elif child.type == "import_from_statement":
            imports.extend(_parse_import_from_statement(child, source))
    return imports


def py_extract_constants(root: Node, source: bytes) -> dict[str, ConstantEntry]:
    """Extract module-level constants (ALL_CAPS pattern) from root node."""
    constants: dict[str, ConstantEntry] = {}
    for child in root.children:
        if child.type != "expression_statement":
            continue
        for sub in child.children:
            if sub.type != "assignment":
                continue
            left = sub.child_by_field_name("left")
            if left is None:
                first_ident = None
                for inner in sub.children:
                    if inner.type == "identifier":
                        first_ident = inner
                        break
                if first_ident is None:
                    continue
                left = first_ident

            if left.type != "identifier":
                continue

            name = _node_text(left, source)
            if not _ALL_CAPS_RE.match(name):
                continue

            right = sub.child_by_field_name("right")
            type_ann = None
            value = None
            for inner in sub.children:
                if inner.type == "type":
                    type_ann = _node_text(inner, source)
            if right is not None:
                value = _node_text(right, source)
            else:
                found_eq = False
                for inner in sub.children:
                    if not inner.is_named and _node_text(inner, source) == "=":
                        found_eq = True
                        continue
                    if found_eq and inner.is_named and inner.type != "type":
                        value = _node_text(inner, source)
                        break

            constants[name] = ConstantEntry(
                name=name, value=value, type_annotation=type_ann
            )
    return constants


def py_extract_parameters(params_node: Node, source: bytes) -> list[ParameterEntry]:
    """Extract parameters — delegates to py_utils._extract_parameters."""
    return _extract_parameters(params_node, source)


def py_extract_return_type(func_node: Node, source: bytes) -> str | None:
    """Extract return type — delegates to py_utils._extract_return_type."""
    return _extract_return_type(func_node, source)


def py_extract_docstring(body_node: Node, source: bytes) -> tuple[str | None, str | None]:
    """Extract docstring — delegates to py_utils._extract_docstring."""
    return _extract_docstring(body_node, source)


def py_extract_calls(body_node: Node, source: bytes) -> list[CallEntry]:
    """Extract calls — delegates to py_utils._extract_calls."""
    return _extract_calls(body_node, source)


def py_extract_raises(body_node: Node, source: bytes) -> list[str]:
    """Extract raises — delegates to py_utils._extract_raises."""
    return _extract_raises(body_node, source)


def py_extract_visibility(name: str) -> str:
    """Determine visibility — delegates to py_utils._visibility_from_name."""
    return _visibility_from_name(name)


def py_extract_decorators(decorated_node: Node, source: bytes) -> list[str]:
    """Extract decorators — delegates to py_utils._extract_decorators."""
    return _extract_decorators(decorated_node, source)


def py_check_generator(body_node: Node) -> bool:
    """Check generator — delegates to py_utils._check_generator."""
    return _check_generator(body_node)


def py_check_async(func_node: Node, source: bytes) -> bool:
    """Check if function is async by inspecting source text start."""
    func_start = source[func_node.start_byte: func_node.start_byte + 10].decode(
        "utf-8", errors="replace"
    )
    return func_start.startswith("async")


def py_extract_module_docstring(root: Node, source: bytes) -> str | None:
    """Extract the module-level docstring (first statement if it's a string)."""
    for child in root.children:
        if not child.is_named:
            continue
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "string":
                    raw = _node_text(sub, source)
                    for delim in ('"""', "'''", '"', "'"):
                        if raw.startswith(delim) and raw.endswith(delim) and len(raw) > len(delim):
                            raw = raw[len(delim): -len(delim)]
                            break
                    first_line = raw.strip().split("\n")[0].strip()
                    return first_line if first_line else None
            return None
        return None
    return None


def py_extract_class_attributes(
    body_node: Node, source: bytes
) -> tuple[list[AttributeEntry], list[AttributeEntry]]:
    """Extract class/instance attributes — delegates to py_utils._extract_class_attributes."""
    return _extract_class_attributes(body_node, source)


def py_extract_types(entry: FileEntry, file_path: str) -> dict[str, TypeEntry]:
    """Create TypeEntry objects for type-like classes (dataclass, TypedDict, etc.)."""
    types: dict[str, TypeEntry] = {}

    for cls in entry.classes.values():
        kind: str | None = None

        if cls.is_dataclass:
            kind = "dataclass"
        elif cls.is_protocol:
            kind = "protocol"
        elif any(b in ("TypedDict",) for b in cls.base_classes):
            kind = "typeddict"
        elif any(b in ("NamedTuple",) for b in cls.base_classes):
            kind = "namedtuple"
        elif any(b in ("BaseModel", "pydantic.BaseModel") for b in cls.base_classes):
            kind = "pydantic"

        if kind is None:
            continue

        if kind in ("dataclass", "pydantic", "namedtuple"):
            attrs = cls.instance_attributes or cls.class_attributes
        elif kind == "protocol":
            attrs = []
            for method in cls.methods.values():
                params_str = ", ".join(
                    f"{p.name}:{p.type_annotation or '?'}"
                    for p in method.parameters
                    if p.name not in ("self", "cls")
                )
                ret = method.return_type or "None"
                attrs.append(AttributeEntry(
                    name=method.name,
                    type_annotation=f"({params_str})->{ret}",
                ))
        else:
            attrs = cls.class_attributes or cls.instance_attributes

        field_parts: list[str] = []
        for attr in attrs:
            type_str = attr.type_annotation or "?"
            part = f"{attr.name}:{type_str}"
            if attr.has_default and attr.default_value is not None:
                part += f"={attr.default_value}"
            field_parts.append(part)
        source_text = f"{cls.name}{{{', '.join(field_parts)}}}"

        types[cls.name] = TypeEntry(
            name=cls.name,
            kind=kind,
            file_path=file_path,
            start_line=cls.start_line,
            source_text=source_text,
            fields=list(attrs),
        )

    return types


def py_extract_type_aliases(
    root: Node, source: bytes, file_path: str, entry: FileEntry
) -> None:
    """Extract Python 3.12+ type alias statements (type Foo = Bar | Baz)."""
    for child in root.children:
        if child.type == "type_alias_statement":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None:
                for sub in child.children:
                    if sub.type == "type" or sub.type == "identifier":
                        name_node = sub
                        break
            if name_node is None:
                continue
            name = _node_text(name_node, source)
            value_text = _node_text(value_node, source) if value_node else _node_text(child, source)
            source_text = f"{name} = {value_text}"

            entry.types[name] = TypeEntry(
                name=name,
                kind="type_alias",
                file_path=file_path,
                start_line=child.start_point[0] + 1,
                source_text=source_text,
                fields=[],
            )


def py_collect_parse_errors(root: Node, source: bytes) -> list[str]:
    """Recursively collect all ERROR and MISSING nodes in the tree."""
    errors: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR":
            line = node.start_point[0] + 1
            col = node.start_point[1] + 1
            snippet = source[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace"
            )[:40]
            if len(snippet) < (node.end_byte - node.start_byte):
                snippet += "..."
            errors.append(f"syntax error at L{line}:{col}: {snippet!r}")
        elif node.is_missing:
            line = node.start_point[0] + 1
            col = node.start_point[1] + 1
            errors.append(
                f"missing {node.type!r} at L{line}:{col}"
            )
        stack.extend(node.children)
    return errors


# ---------------------------------------------------------------------------
# Import statement helpers (moved from PythonFileParser)
# ---------------------------------------------------------------------------


def _parse_import_statement(node: Node, source: bytes) -> list[ImportEntry]:
    """Parse 'import X' or 'import X as Y' statements."""
    results: list[ImportEntry] = []
    for child in node.children:
        if child.type == "dotted_name":
            results.append(
                ImportEntry(module=_node_text(child, source))
            )
        elif child.type == "aliased_import":
            module_name = ""
            alias = None
            for sub in child.children:
                if sub.type == "dotted_name":
                    module_name = _node_text(sub, source)
                elif sub.type == "identifier" and module_name:
                    alias = _node_text(sub, source)
            results.append(
                ImportEntry(module=module_name, alias=alias)
            )
    return results


def _parse_import_from_statement(node: Node, source: bytes) -> list[ImportEntry]:
    """Parse 'from X import Y, Z' or 'from X import *' statements."""
    module_name = ""
    imported_items: list[tuple[str, str | None]] = []
    is_wildcard = False

    found_from = False
    found_import = False
    for child in node.children:
        if not child.is_named and _node_text(child, source) == "from":
            found_from = True
            continue
        if not child.is_named and _node_text(child, source) == "import":
            found_import = True
            continue
        if child.type == "dotted_name" and found_from and not found_import:
            module_name = _node_text(child, source)
        elif child.type == "relative_import" and found_from and not found_import:
            module_name = _node_text(child, source)
        elif found_import:
            if child.type == "dotted_name":
                imported_items.append((_node_text(child, source), None))
            elif child.type == "wildcard_import":
                is_wildcard = True
            elif child.type == "aliased_import":
                original_name = ""
                alias = None
                for sub in child.children:
                    if sub.type == "dotted_name" and not original_name:
                        original_name = _node_text(sub, source)
                    elif sub.type == "identifier" and original_name:
                        alias = _node_text(sub, source)
                if original_name:
                    imported_items.append((original_name, alias))

    if not imported_items:
        return [
            ImportEntry(
                module=module_name,
                names=[],
                is_from_import=True,
                is_wildcard=is_wildcard,
            )
        ]

    if all(alias is None for _, alias in imported_items):
        return [
            ImportEntry(
                module=module_name,
                names=[name for name, _ in imported_items],
                is_from_import=True,
                is_wildcard=is_wildcard,
            )
        ]

    results: list[ImportEntry] = []
    unaliased_names = [name for name, alias in imported_items if alias is None]
    if unaliased_names:
        results.append(
            ImportEntry(
                module=module_name,
                names=unaliased_names,
                is_from_import=True,
                is_wildcard=is_wildcard,
            )
        )
    for name, alias in imported_items:
        if alias is not None:
            results.append(
                ImportEntry(
                    module=module_name,
                    names=[name],
                    is_from_import=True,
                    alias=alias,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Node type map
# ---------------------------------------------------------------------------

PYTHON_NODE_TYPES = NodeTypeMap(
    function_def="function_definition",
    class_def="class_definition",
    method_def="function_definition",
    function_name="identifier",
    class_name="identifier",
    method_name="identifier",
    params_node="parameters",
    body_node="block",
    class_body="block",
    decorated_def="decorated_definition",
    export_wrapper=None,
    docstring_style="python",
)

# ---------------------------------------------------------------------------
# The config instance — registered on import
# ---------------------------------------------------------------------------

PYTHON_CONFIG = LanguageConfig(
    language_id="python",
    extensions=frozenset({".py", ".pyi"}),
    grammar_loader=_load_python_grammar,
    node_types=PYTHON_NODE_TYPES,
    extract_imports=py_extract_imports,
    extract_constants=py_extract_constants,
    extract_parameters=py_extract_parameters,
    extract_return_type=py_extract_return_type,
    extract_docstring=py_extract_docstring,
    extract_calls=py_extract_calls,
    extract_raises=py_extract_raises,
    extract_visibility=py_extract_visibility,
    extract_decorators=py_extract_decorators,
    extract_types=py_extract_types,
    extract_type_aliases=py_extract_type_aliases,
    check_generator=py_check_generator,
    check_async=py_check_async,
    extract_module_docstring=py_extract_module_docstring,
    extract_class_attributes=py_extract_class_attributes,
    collect_parse_errors=py_collect_parse_errors,
)

register(PYTHON_CONFIG)
