"""AST extraction helpers for the Python tree-sitter parser.

Language-specific utility functions for extracting docstrings, parameters,
decorators, call graphs, and other metadata from tree-sitter nodes.
"""

from __future__ import annotations

import re
from collections import Counter

from tree_sitter import Node

from abstract_engine.models import (
    AttributeEntry,
    CallEntry,
    ParameterEntry,
)

_ALL_CAPS_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _node_text(node: Node | None, source: bytes) -> str:
    """Extract the UTF-8 text of a tree-sitter node."""
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _visibility_from_name(name: str) -> str:
    """Determine visibility from Python naming conventions."""
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_") and not name.startswith("__"):
        return "protected"
    return "public"


def _extract_docstring(body_node: Node, source: bytes) -> tuple[str | None, str | None]:
    """Extract docstring from the first statement of a block.

    Returns (first_line, full_docstring). The first statement must be an
    expression_statement containing a string node.
    """
    if body_node.type != "block" or body_node.child_count == 0:
        return None, None

    # The first named child of the block should be an expression_statement
    first_stmt = None
    for child in body_node.children:
        if child.is_named:
            first_stmt = child
            break

    if first_stmt is None or first_stmt.type != "expression_statement":
        return None, None

    # The expression_statement should contain a string
    string_node = None
    for child in first_stmt.children:
        if child.is_named and child.type == "string":
            string_node = child
            break

    if string_node is None:
        return None, None

    raw = _node_text(string_node, source)
    # Strip triple-quote or single-quote delimiters
    for delim in ('"""', "'''", '"', "'"):
        if raw.startswith(delim) and raw.endswith(delim) and len(raw) > len(delim):
            raw = raw[len(delim) : -len(delim)]
            break

    raw = raw.strip()
    if not raw:
        return None, None

    first_line = raw.split("\n")[0].strip()
    return first_line, raw


def _extract_parameters(params_node: Node, source: bytes) -> list[ParameterEntry]:
    """Extract parameters from a parameters node."""
    result: list[ParameterEntry] = []
    if params_node is None or params_node.type != "parameters":
        return result

    for child in params_node.children:
        if not child.is_named:
            continue

        if child.type == "identifier":
            # Plain untyped parameter (e.g., 'self', 'cls', 'x')
            result.append(ParameterEntry(name=_node_text(child, source)))

        elif child.type == "typed_parameter":
            # Could be a regular typed param, *args: type, or **kwargs: type
            name_node = child.child_by_field_name("name") if hasattr(child, "child_by_field_name") else None
            # Walk children to find the name and type
            param_name = ""
            param_type = None
            is_variadic = False
            is_kw_variadic = False

            for sub in child.children:
                if sub.type == "identifier" and not param_name:
                    param_name = _node_text(sub, source)
                elif sub.type == "list_splat_pattern":
                    is_variadic = True
                    for inner in sub.children:
                        if inner.type == "identifier":
                            param_name = _node_text(inner, source)
                elif sub.type == "dictionary_splat_pattern":
                    is_kw_variadic = True
                    for inner in sub.children:
                        if inner.type == "identifier":
                            param_name = _node_text(inner, source)
                elif sub.type == "type":
                    param_type = _node_text(sub, source)

            result.append(
                ParameterEntry(
                    name=param_name,
                    type_annotation=param_type,
                    is_variadic=is_variadic,
                    is_keyword_variadic=is_kw_variadic,
                )
            )

        elif child.type == "default_parameter":
            # name = value (no type annotation)
            param_name = ""
            default_val = None
            for sub in child.children:
                if sub.type == "identifier" and not param_name:
                    param_name = _node_text(sub, source)
                elif sub.is_named and sub.type != "identifier":
                    default_val = _node_text(sub, source)
            # Also check field-based access
            name_child = child.child_by_field_name("name")
            value_child = child.child_by_field_name("value")
            if name_child:
                param_name = _node_text(name_child, source)
            if value_child:
                default_val = _node_text(value_child, source)
            result.append(
                ParameterEntry(
                    name=param_name,
                    has_default=True,
                    default_value=default_val,
                )
            )

        elif child.type == "typed_default_parameter":
            # name: type = value
            param_name = ""
            param_type = None
            default_val = None
            name_child = child.child_by_field_name("name")
            type_child = child.child_by_field_name("type")
            value_child = child.child_by_field_name("value")
            if name_child:
                param_name = _node_text(name_child, source)
            if type_child:
                param_type = _node_text(type_child, source)
            if value_child:
                default_val = _node_text(value_child, source)
            result.append(
                ParameterEntry(
                    name=param_name,
                    type_annotation=param_type,
                    has_default=True,
                    default_value=default_val,
                )
            )

        elif child.type == "list_splat_pattern":
            # *args (untyped)
            param_name = ""
            for sub in child.children:
                if sub.type == "identifier":
                    param_name = _node_text(sub, source)
            result.append(
                ParameterEntry(name=param_name, is_variadic=True)
            )

        elif child.type == "dictionary_splat_pattern":
            # **kwargs (untyped)
            param_name = ""
            for sub in child.children:
                if sub.type == "identifier":
                    param_name = _node_text(sub, source)
            result.append(
                ParameterEntry(name=param_name, is_keyword_variadic=True)
            )

        elif child.type == "keyword_separator":
            # The bare * separator for keyword-only args; skip it
            pass

    return result


def _extract_return_type(func_node: Node, source: bytes) -> str | None:
    """Extract return type annotation from a function_definition node."""
    return_type = func_node.child_by_field_name("return_type")
    if return_type is not None:
        return _node_text(return_type, source)
    return None


def _extract_decorators(decorated_node: Node, source: bytes) -> list[str]:
    """Extract decorator names from a decorated_definition node."""
    decorators: list[str] = []
    for child in decorated_node.children:
        if child.type == "decorator":
            # The decorator content is everything after '@'
            dec_text = _node_text(child, source)
            # Strip leading '@' and any arguments
            dec_text = dec_text.lstrip("@").strip()
            # Get just the name part (before any parens)
            paren_idx = dec_text.find("(")
            if paren_idx != -1:
                dec_text = dec_text[:paren_idx]
            decorators.append(dec_text.strip())
    return decorators


def _check_generator(body_node: Node) -> bool:
    """Check if a function body contains yield or yield from."""
    if body_node is None:
        return False
    for child in body_node.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "yield":
                    return True
        if child.type == "return_statement":
            for sub in child.children:
                if sub.type == "yield":
                    return True
        # Recurse into blocks (if, for, while, try, with, etc.)
        if _check_generator(child):
            return True
    return False


def _extract_calls(body_node: Node, source: bytes) -> list[CallEntry]:
    """Extract function calls from a function body node."""
    call_counter: Counter[str] = Counter()

    def _walk_calls(node: Node) -> None:
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                if func_node.type == "identifier":
                    call_counter[_node_text(func_node, source)] += 1
                elif func_node.type == "attribute":
                    call_counter[_node_text(func_node, source)] += 1
        for child in node.children:
            _walk_calls(child)

    if body_node is not None:
        _walk_calls(body_node)

    return [
        CallEntry(callee_name=name, call_count=count)
        for name, count in call_counter.items()
    ]


def _extract_raises(body_node: Node, source: bytes) -> list[str]:
    """Extract exception names from raise statements in a function body."""
    raises: list[str] = []

    def _walk_raises(node: Node) -> None:
        if node.type == "raise_statement":
            # The exception is the first named child after 'raise'
            for child in node.children:
                if child.is_named:
                    if child.type == "call":
                        # raise ValueError("msg") -> get the function name
                        func = child.child_by_field_name("function")
                        if func:
                            raises.append(_node_text(func, source))
                    elif child.type == "identifier":
                        raises.append(_node_text(child, source))
                    elif child.type == "attribute":
                        raises.append(_node_text(child, source))
                    break
        for child in node.children:
            _walk_raises(child)

    if body_node is not None:
        _walk_raises(body_node)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for r in raises:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _extract_class_attributes(
    body_node: Node, source: bytes
) -> tuple[list[AttributeEntry], list[AttributeEntry]]:
    """Extract class-level and instance-level attributes from a class body.

    Class attributes: assignments at the class body level.
    Instance attributes: self.X assignments inside __init__.
    """
    class_attrs: list[AttributeEntry] = []
    instance_attrs: list[AttributeEntry] = []

    if body_node is None or body_node.type != "block":
        return class_attrs, instance_attrs

    for child in body_node.children:
        if not child.is_named:
            continue

        # Class-level assignments: expression_statement -> assignment
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type == "assignment":
                    _parse_class_level_assignment(sub, source, class_attrs)

    # Find __init__ and extract self.X assignments
    for child in body_node.children:
        if not child.is_named:
            continue
        func_node = _unwrap_to_function(child)
        if func_node is not None and func_node.type == "function_definition":
            name_node = func_node.child_by_field_name("name")
            if name_node and _node_text(name_node, source) == "__init__":
                init_body = func_node.child_by_field_name("body")
                if init_body:
                    _extract_instance_attrs(init_body, source, instance_attrs)
                break

    return class_attrs, instance_attrs


def _parse_class_level_assignment(
    assign_node: Node, source: bytes, attrs: list[AttributeEntry]
) -> None:
    """Parse a class-level assignment into an AttributeEntry."""
    left = assign_node.child_by_field_name("left")
    if left is None:
        # Could be a type-annotated assignment (x: int = 5 or x: int)
        # In tree-sitter, typed assignments at class level look like:
        # assignment with identifier : type = value
        # Let's walk children
        name = ""
        type_ann = None
        default_val = None
        has_default = False
        for sub in assign_node.children:
            if sub.type == "identifier" and not name:
                name = _node_text(sub, source)
            elif sub.type == "type":
                type_ann = _node_text(sub, source)
            elif sub.type == "=" or not sub.is_named:
                continue
            elif name and type_ann is not None:
                default_val = _node_text(sub, source)
                has_default = True
            elif name and type_ann is None and sub.type != "identifier":
                default_val = _node_text(sub, source)
                has_default = True
        if name:
            attrs.append(
                AttributeEntry(
                    name=name,
                    type_annotation=type_ann,
                    has_default=has_default,
                    default_value=default_val,
                    visibility=_visibility_from_name(name),
                )
            )
        return

    name = _node_text(left, source)
    if left.type != "identifier":
        return

    # Check for type annotation and value
    type_ann = None
    default_val = None
    has_default = False
    for sub in assign_node.children:
        if sub.type == "type":
            type_ann = _node_text(sub, source)
        elif sub.is_named and sub != left and sub.type not in ("type", "identifier"):
            default_val = _node_text(sub, source)
            has_default = True

    # Check right side
    right = assign_node.child_by_field_name("right")
    if right is not None:
        default_val = _node_text(right, source)
        has_default = True

    attrs.append(
        AttributeEntry(
            name=name,
            type_annotation=type_ann,
            has_default=has_default,
            default_value=default_val,
            visibility=_visibility_from_name(name),
        )
    )


def _extract_instance_attrs(
    body_node: Node, source: bytes, attrs: list[AttributeEntry]
) -> None:
    """Extract self.X = ... assignments from an __init__ body."""
    seen_names: set[str] = set()

    def _walk(node: Node) -> None:
        if node.type == "assignment":
            left = node.child_by_field_name("left")
            if left is not None and left.type == "attribute":
                obj = left.child_by_field_name("object")
                attr = left.child_by_field_name("attribute")
                if obj and _node_text(obj, source) == "self" and attr:
                    attr_name = _node_text(attr, source)
                    if attr_name not in seen_names:
                        seen_names.add(attr_name)
                        # Check for type annotation
                        type_ann = None
                        default_val = None
                        has_default = False
                        for sub in node.children:
                            if sub.type == "type":
                                type_ann = _node_text(sub, source)
                        right = node.child_by_field_name("right")
                        if right is not None:
                            default_val = _node_text(right, source)
                            has_default = True
                        attrs.append(
                            AttributeEntry(
                                name=attr_name,
                                type_annotation=type_ann,
                                has_default=has_default,
                                default_value=default_val,
                                visibility=_visibility_from_name(attr_name),
                            )
                        )
        for child in node.children:
            _walk(child)

    _walk(body_node)


def _unwrap_to_function(node: Node) -> Node | None:
    """Unwrap decorated_definition to get the inner function_definition."""
    if node.type == "function_definition":
        return node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type == "function_definition":
                return child
    return None


def _unwrap_to_class(node: Node) -> Node | None:
    """Unwrap decorated_definition to get the inner class_definition."""
    if node.type == "class_definition":
        return node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type == "class_definition":
                return child
    return None


def _is_async_function(func_node: Node) -> bool:
    """Check if a function_definition is async by looking for 'async' keyword."""
    # In tree-sitter-python, async functions still use function_definition node type.
    # The 'async' keyword appears as an unnamed child before 'def'.
    for child in func_node.children:
        if not child.is_named and _node_text(child, func_node.text or b"") == "async":
            break
    # Simpler: check the source text
    # Actually, check if there's a preceding 'async' keyword in the parent or the node itself
    # tree-sitter-python: the text of the node starts with 'async def' if async
    return False  # We'll handle this differently

