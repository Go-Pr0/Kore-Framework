"""Pure rendering functions for Tier 1 and Tier 2 abstract views.

These functions take model objects and return formatted strings. They have no
side effects and perform no file I/O. All rendering rules follow the spec in
the ticket's render_format_examples section.
"""

from __future__ import annotations

import re

from abstract_engine.models import (
    AttributeEntry,
    CallEntry,
    ClassEntry,
    FileEntry,
    FunctionEntry,
    FunctionLocator,
    ImportEntry,
)



def _format_param_types(
    parameters: list,
    is_method: bool = False,
) -> str:
    """Format parameter types for Tier 1 display.

    Rules:
    - Types only (not names) unless the name carries semantic meaning
    - Remove 'self' and 'cls' from method params
    - Show param name when it adds semantic clarity (heuristic: non-obvious names)
    """
    parts: list[str] = []
    for param in parameters:
        # Skip self/cls for methods
        if is_method and param.name in ("self", "cls"):
            continue

        # Skip *args and **kwargs variadic markers in the type-only view
        if param.is_variadic or param.is_keyword_variadic:
            prefix = "*" if param.is_variadic else "**"
            if param.type_annotation:
                parts.append(f"{prefix}{param.name}:{param.type_annotation}")
            else:
                parts.append(f"{prefix}{param.name}")
            continue

        type_str = param.type_annotation or "?"

        # Include param name when it carries semantic meaning.
        # Heuristic: include name unless it's a single generic word that
        # duplicates the type (e.g., 'text:str' is useful, 'str' alone is not).
        if param.name and param.name not in ("self", "cls"):
            display = f"{param.name}:{type_str}"
        else:
            display = type_str

        if param.has_default and param.default_value is not None:
            display += f"={param.default_value}"

        parts.append(display)

    return ", ".join(parts)


def _one_liner(func: FunctionEntry) -> str:
    """Truncate and clean docstring_first_line for Tier 1 display."""
    text = (func.docstring_first_line or "").strip().strip("\"'").strip()
    if len(text) > 100:
        text = text[:97] + "..."
    return text


def render_tier1_function(
    func: FunctionEntry,
    is_method: bool = False,
    include_full_docstring: bool = False,
) -> str:
    """Render a single function as a Tier 1 one-liner with line range.

    Format: function_name(param_types)->return_type: one-liner  L42-67
    Async functions are prefixed with 'async'.

    When include_full_docstring is True (Tier 1.5), the full docstring is
    rendered as indented comment lines below the signature instead of the
    truncated one-liner. Falls back to one-liner if docstring_full is absent.
    """
    prefix = "async " if func.is_async else ""
    params = _format_param_types(func.parameters, is_method=is_method)
    return_type = func.return_type or "None"
    range_str = f"  L{func.start_line}-{func.end_line}" if func.start_line else ""

    if include_full_docstring and func.docstring_full:
        sig_line = f"{prefix}{func.name}({params})->{return_type}{range_str}"
        doc_lines = func.docstring_full.strip().splitlines()
        doc_block = "\n".join(f"  # {line}" if line.strip() else "  #" for line in doc_lines)
        return f"{sig_line}\n{doc_block}"

    if func.docstring_first_line:
        one_liner = _one_liner(func)
        return f"{prefix}{func.name}({params})->{return_type}: {one_liner}{range_str}"
    return f"{prefix}{func.name}({params})->{return_type}{range_str}"


def _render_dataclass_tier1(cls: ClassEntry) -> str:
    """Render a type-like class as a compact type definition in Tier 1.

    Works for dataclasses, TypedDict, NamedTuple, and Pydantic models.
    Format: ClassName{field:type, field:type=default}
    """
    fields: list[str] = []
    # For TypedDict, fields are class_attributes; for dataclass/pydantic, instance_attributes
    type_bases = {"TypedDict"}
    if set(cls.base_classes) & type_bases:
        attrs = cls.class_attributes or cls.instance_attributes
    else:
        attrs = cls.instance_attributes or cls.class_attributes
    for attr in attrs:
        type_str = attr.type_annotation or "?"
        entry = f"{attr.name}:{type_str}"
        if attr.has_default and attr.default_value is not None:
            entry += f"={attr.default_value}"
        fields.append(entry)

    field_str = ", ".join(fields)
    return f"{cls.name}{{{field_str}}}"


def _is_type_like_class(cls: ClassEntry) -> bool:
    """Check if a class should be rendered with the compact type format.

    Returns True for dataclasses, TypedDict, NamedTuple, and Pydantic models.
    """
    if cls.is_dataclass:
        return True
    type_bases = {"TypedDict", "NamedTuple", "BaseModel", "pydantic.BaseModel"}
    return bool(set(cls.base_classes) & type_bases)


def render_tier1_class(cls: ClassEntry) -> str:
    """Render a class with its methods for Tier 1.

    Dataclasses, TypedDict, NamedTuple, and Pydantic models are rendered as
    compact type definitions: ClassName{field:type, ...}
    Protocol classes are annotated with [Protocol] suffix.
    Regular classes show their public methods indented.
    """
    lines: list[str] = []

    # Compact type rendering for dataclass, TypedDict, NamedTuple, Pydantic
    if _is_type_like_class(cls):
        dc_line = _render_dataclass_tier1(cls)
        if cls.docstring_first_line:
            dc_line += f": {cls.docstring_first_line}"
        lines.append(f"  {dc_line}")
        return "\n".join(lines)

    # Regular class header
    suffix = ""
    if cls.is_protocol:
        suffix = " [Protocol]"
    elif cls.is_abstract:
        suffix = " [Abstract]"

    all_attrs = cls.class_attributes + cls.instance_attributes
    if all_attrs:
        cap = 8
        shown_attrs = all_attrs[:cap]
        overflow = len(all_attrs) - cap
        attr_parts = []
        for attr in shown_attrs:
            entry = attr.name
            if attr.type_annotation:
                entry += f":{attr.type_annotation}"
            if attr.has_default and attr.default_value is not None:
                entry += f"={attr.default_value}"
            attr_parts.append(entry)
        if overflow > 0:
            attr_parts.append(f"+{overflow} more")
        attrs_str = "{" + ", ".join(attr_parts) + "}"
    else:
        attrs_str = ""

    header = f"  class {cls.name}{attrs_str}{suffix}"
    if cls.docstring_first_line:
        header += f": {cls.docstring_first_line}"
    lines.append(header)

    # Methods — public non-dunder only; dunders are implementation detail
    # and __init__ params are already captured in the inline {attrs}
    for method in cls.methods.values():
        if method.visibility != "public":
            continue
        if method.name.startswith("__") and method.name.endswith("__"):
            continue
        method_line = render_tier1_function(method, is_method=True)
        lines.append(f"    {method_line}")

    return "\n".join(lines)


def count_public_functions(file_entry: FileEntry) -> int:
    """Count total public functions + methods in a file."""
    count = 0
    for func in file_entry.functions.values():
        if func.visibility == "public":
            count += 1
    for cls in file_entry.classes.values():
        for method in cls.methods.values():
            if method.visibility == "public":
                count += 1
    return count


def file_one_liner(file_entry: FileEntry) -> str:
    """Get the one-liner for a file.

    Uses module_docstring if available, otherwise expands the filename.
    """
    if file_entry.module_docstring:
        text = file_entry.module_docstring.strip().strip("\"'").strip()
        if len(text) > 100:
            text = text[:97] + "..."
        return text

    # Derive from filename — expand snake_case to words
    name = file_entry.relative_path.rsplit("/", 1)[-1]
    name = re.sub(r"\.\w+$", "", name)  # Remove extension
    words = name.strip("_").split("_")
    return " ".join(words).capitalize() if words else name


def _render_imports_compressed(imports: list[ImportEntry]) -> str:
    """Render the import list as a single compact line.

    Format: # imports: logging, os | fastapi: Request,Response | .models: Session,User
    Plain 'import X' names come first, then from-imports grouped by module.
    Wildcard imports are shown as module: *.
    """
    plain: list[str] = []
    from_imports: dict[str, list[str]] = {}

    for entry in imports:
        if not entry.is_from_import:
            name = entry.alias if entry.alias else entry.module
            plain.append(name)
        else:
            mod = entry.module
            if entry.is_wildcard:
                from_imports.setdefault(mod, []).append("*")
            else:
                names = entry.names
                if entry.alias and len(names) == 1:
                    names = [f"{names[0]} as {entry.alias}"]
                from_imports.setdefault(mod, []).extend(names)

    segments: list[str] = []
    if plain:
        segments.append(", ".join(plain))
    for mod, names in from_imports.items():
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped = [n for n in names if not (n in seen or seen.add(n))]  # type: ignore[func-returns-value]
        segments.append(f"{mod}: {','.join(deduped)}")

    return "# imports: " + " | ".join(segments)


def render_tier1_file(file_entry: FileEntry) -> str:
    """Render a complete file-level Tier 1 view.

    Format:
      relative/path/to/file.py [Nfn, NL]: one-liner module purpose
        function_name(param_types)->return_type: one-liner purpose
        class ClassName: ...
    """
    # Handle parse errors
    if file_entry.parse_error:
        detail = file_entry.parse_error_detail or "Could not parse file"
        return f"{file_entry.relative_path} [PARSE ERROR]: {detail}"

    fn_count = count_public_functions(file_entry)
    one_liner = file_one_liner(file_entry)
    header = (
        f"{file_entry.relative_path} "
        f"[{fn_count}fn, {file_entry.line_count}L]: "
        f"{one_liner}"
    )

    lines: list[str] = [header]

    # Compact imports line (one line, no matter how many imports)
    if file_entry.imports:
        lines.append(_render_imports_compressed(file_entry.imports))

    # Constants line — only when the file defines module-level constants
    if file_entry.constants:
        const_items = list(file_entry.constants.items())
        cap = 8
        shown = const_items[:cap]
        overflow = len(const_items) - cap
        const_parts = []
        for name, const in shown:
            entry = name
            if const.value is not None:
                val = const.value
                if len(val) > 80:
                    # Determine the type of the value for the summary
                    stripped = val.strip()
                    if stripped.startswith(("b'", 'b"', "b'''", 'b"""')):
                        entry += f"=<bytes len={len(val)}>"
                    else:
                        entry += f"=<str len={len(val)}>"
                else:
                    entry += f"={val}"
            const_parts.append(entry)
        suffix = f", +{overflow} more" if overflow > 0 else ""
        lines.append("# consts: " + ", ".join(const_parts) + suffix)

    # Top-level functions — public only; private are implementation details
    for func in file_entry.functions.values():
        if func.visibility == "public":
            lines.append(f"  {render_tier1_function(func)}")

    # Classes
    for cls in file_entry.classes.values():
        lines.append(render_tier1_class(cls))

    return "\n".join(lines)


def render_tier2_function(
    func: FunctionEntry,
    file_path: str = "",
    function_lookup: dict[str, list[FunctionLocator]] | None = None,
) -> str:
    """Render enriched Tier 2 dependency context for a function.

    New compact format showing dependency graph with line ranges, unresolved
    calls, and suggested next reads. Omits info already in Tier 1 (signature,
    async flag, docstring one-liner).

    Args:
        func: The function to render context for.
        file_path: The file path for display.
        function_lookup: Optional cross-file lookup for showing all candidates
            on ambiguous calls.
    """
    fp = file_path or func.file_path or ""
    range_str = f":L{func.start_line}-{func.end_line}" if func.start_line else ""
    header = f"context for {func.qualified_name or func.name} in {fp}{range_str}"

    lines: list[str] = [header]

    # Resolved and unresolved calls
    resolved_calls = [c for c in func.calls if c.resolved_file and not c.is_external]
    unresolved_calls = [c for c in func.calls if c.is_external or not c.resolved_file]

    if resolved_calls:
        lines.append("")
        lines.append("depends_on:")
        for call in resolved_calls:
            name = call.resolved_qualified_name or call.callee_name
            loc = call.resolved_file or ""
            if call.resolved_start_line:
                loc += f":L{call.resolved_start_line}-{call.resolved_end_line}"
            confidence_note = ""
            if call.resolution_confidence == "ambiguous" and call.match_count > 1:
                confidence_note = f"  [ambiguous: {call.match_count} matches; verify source]"
            elif call.resolution_confidence == "preferred" and call.match_count > 1:
                confidence_note = f"  [preferred among {call.match_count} matches]"
            lines.append(f"  {name}  {loc}{confidence_note}")

    if func.called_by:
        lines.append("")
        lines.append("called_by:")
        for caller in func.called_by:
            name = caller.caller_qualified_name or caller.caller_name
            loc = caller.caller_file or ""
            if caller.start_line:
                loc += f":L{caller.start_line}-{caller.end_line}"
            lines.append(f"  {name}  {loc}")

    # Flags: decorators and raises (skip if both empty)
    has_flags = func.decorators or func.raises
    if has_flags:
        lines.append("")
        lines.append("flags:")
        if func.decorators:
            dec_str = ", ".join(f"@{d}" for d in func.decorators)
            lines.append(f"  decorators: {dec_str}")
        if func.raises:
            lines.append(f"  raises: {', '.join(func.raises)}")

    if unresolved_calls:
        lines.append("")
        lines.append("unresolved:")
        for call in unresolved_calls:
            if call.match_count > 1:
                # Show all candidates if function_lookup is available
                bare_name = call.callee_name.split(".")[-1] if "." in call.callee_name else call.callee_name
                candidates = function_lookup.get(bare_name, []) if function_lookup else []
                if candidates and len(candidates) > 1:
                    lines.append(f"  {call.callee_name} — {call.match_count} matches:")
                    for loc in candidates:
                        marker = " (used)" if (
                            loc.file_path == call.resolved_file
                            and loc.qualified_name == call.resolved_qualified_name
                        ) else ""
                        lines.append(f"    {loc.qualified_name}  {loc.file_path}{marker}")
                else:
                    lines.append(f"  {call.callee_name} — {call.match_count} matches; used {call.resolved_file}:{call.resolved_qualified_name}")
            else:
                lines.append(f"  {call.callee_name} — could not resolve statically")

    # Suggested reads: prioritize unresolved + cross-file resolved calls
    suggestions: list[str] = []
    for call in unresolved_calls[:2]:
        if call.resolved_file:
            suggestions.append(f"  function_read({call.resolved_file!r}, {call.callee_name!r})")
    cross_file = [
        c for c in resolved_calls
        if c.resolved_file and c.resolved_file != fp
    ]
    for call in cross_file[:3 - len(suggestions)]:
        func_name = call.resolved_qualified_name or call.callee_name
        suggestions.append(f"  function_read({call.resolved_file!r}, {func_name!r})")

    if suggestions:
        lines.append("")
        lines.append("suggested_reads:")
        lines.extend(suggestions[:3])

    return "\n".join(lines)


def render_tier1_file_compact(file_entry: FileEntry) -> str:
    """Render a compact file view showing only function names + line ranges.

    Format:
      relative/path.py [Nfn, NL]
        function_name  L42-67
        ClassName.method_name  L100-130

    Omits imports, constants, docstrings, and parameter details.
    """
    if file_entry.parse_error:
        detail = file_entry.parse_error_detail or "Could not parse file"
        return f"{file_entry.relative_path} [PARSE ERROR]: {detail}"

    fn_count = count_public_functions(file_entry)
    header = f"{file_entry.relative_path} [{fn_count}fn, {file_entry.line_count}L]"
    lines: list[str] = [header]

    for func in file_entry.functions.values():
        if func.visibility == "public":
            range_str = f"L{func.start_line}-{func.end_line}" if func.start_line else ""
            lines.append(f"  {func.name}  {range_str}")

    for cls in file_entry.classes.values():
        for method in cls.methods.values():
            if method.visibility == "public":
                if method.name.startswith("__") and method.name.endswith("__"):
                    continue
                range_str = f"L{method.start_line}-{method.end_line}" if method.start_line else ""
                lines.append(f"  {cls.name}.{method.name}  {range_str}")

    return "\n".join(lines)


def render_overview(
    entries: list[tuple[str, FileEntry]],
    repo_name: str,
    mode: str = "symbols",
    include_types: bool = False,
    type_lookup: dict | None = None,
) -> str:
    """Render the project overview.

    Args:
        entries: Sorted list of (relative_path, FileEntry) tuples.
        repo_name: Name of the repository for the header.
        mode: "symbols" for full per-file listings, "modules" for directory summaries.
        include_types: Whether to append a TYPES section.
        type_lookup: The type_lookup dict (needed if include_types is True).
    """
    total_files = len(entries)
    total_functions = sum(count_public_functions(fe) for _, fe in entries)

    header = (
        f"PROJECT OVERVIEW — {total_files} files, "
        f"{total_functions} functions, {repo_name}:"
    )

    # Group by top-level directory
    groups: dict[str, list[tuple[str, FileEntry]]] = {}
    for rel, fe in entries:
        top = rel.split("/")[0] if "/" in rel else ""
        groups.setdefault(top, []).append((rel, fe))

    output_lines: list[str] = [header, ""]

    if mode == "modules":
        for group_key in sorted(groups.keys()):
            group_entries = groups[group_key]
            n_files = len(group_entries)
            n_funcs = sum(count_public_functions(fe) for _, fe in group_entries)
            # Get a description from the first file's module docstring if available
            desc = ""
            for _, fe in group_entries:
                if fe.module_docstring:
                    desc = fe.module_docstring.strip().strip("\"'").strip()
                    if len(desc) > 80:
                        desc = desc[:77] + "..."
                    break
            label = f"{group_key}/" if group_key else "(root)"
            line = f"{label} [{n_files} files, {n_funcs} functions]"
            if desc:
                line += f": {desc}"
            output_lines.append(line)
        output_lines.append("")
    else:
        # "symbols" mode — full per-file function listings
        for group_key in sorted(groups.keys()):
            if group_key:
                output_lines.append(f"{group_key}/")
            for _rel, fe in groups[group_key]:
                tier1 = fe.tier1_text or render_tier1_file(fe)
                for line in tier1.splitlines():
                    prefix = "  " if group_key else ""
                    output_lines.append(f"{prefix}{line}")
            output_lines.append("")

    # Optional types section
    if include_types and type_lookup:
        output_lines.append("TYPES:")
        for type_name, type_entry in sorted(type_lookup.items()):
            output_lines.append(
                f"  {type_name} [{type_entry.kind}] "
                f"({type_entry.file_path}, line {type_entry.start_line})"
            )
            if type_entry.source_text:
                for src_line in type_entry.source_text.splitlines()[:5]:
                    output_lines.append(f"    {src_line}")
        output_lines.append("")

    return "\n".join(output_lines).rstrip() + "\n"


def render_type_shape(
    type_name: str,
    type_entry: object | None = None,
    cls: ClassEntry | None = None,
    rel_path: str = "",
    include_methods: bool = True,
) -> str:
    """Render a compact description of a type, class, or interface.

    Accepts either a TypeEntry or a ClassEntry. Returns the formatted string.
    """
    if type_entry is not None:
        lines = [
            f"{type_name} ({type_entry.file_path}, line {type_entry.start_line}):",
            f"  [{type_entry.kind}]",
        ]
        if type_entry.source_text:
            for src_line in type_entry.source_text.splitlines():
                lines.append(f"  {src_line}")
        return "\n".join(lines)

    if cls is None:
        return f"Type {type_name} not found."

    # Render class shape
    tags: list[str] = []
    if cls.is_dataclass:
        tags.append("dataclass")
    if cls.is_protocol:
        tags.append("Protocol")
    if cls.is_abstract:
        tags.append("abstract")

    header = f"{type_name} ({rel_path}, line {cls.start_line}):"
    lines = [header]
    if tags:
        lines.append(f"  [{', '.join(tags)}]")
    if cls.docstring_first_line:
        lines.append(f'  "{cls.docstring_first_line}"')

    if cls.base_classes:
        lines.append(f"  bases: {', '.join(cls.base_classes)}")

    all_attrs = list(cls.class_attributes) + list(cls.instance_attributes)
    if all_attrs:
        field_parts: list[str] = []
        for attr in all_attrs:
            type_str = attr.type_annotation or "?"
            part = f"{attr.name}:{type_str}"
            if attr.has_default and attr.default_value is not None:
                part += f"={attr.default_value}"
            field_parts.append(part)
        lines.append(f"  fields: {', '.join(field_parts)}")

    if include_methods and cls.methods:
        lines.append("  methods:")
        for method in cls.methods.values():
            if method.visibility != "public":
                continue
            method_line = render_tier1_function(method, is_method=True)
            lines.append(f"    {method_line}")

    return "\n".join(lines)


def render_all_tier1(files: dict[str, FileEntry]) -> str:
    """Render the full project Tier 1 view across all files.

    Files are sorted by relative path for deterministic output.
    Each file is separated by a blank line.
    """
    blocks: list[str] = []
    for path in sorted(files.keys()):
        file_entry = files[path]
        blocks.append(render_tier1_file(file_entry))

    return "\n\n".join(blocks)
