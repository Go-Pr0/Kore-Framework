"""Call graph resolution and called_by index building.

Second pass over all FunctionEntry objects to resolve call names to file paths
and build the reverse caller index. Runs after all files have been parsed.
"""

from __future__ import annotations

from abstract_engine.models import CallerEntry, ClassEntry, FileEntry, FunctionEntry, FunctionLocator


def build_function_lookup(
    files: dict[str, FileEntry],
) -> dict[str, list[FunctionLocator]]:
    """Build the cross-file function name lookup index.

    Scans all files, including top-level functions and class methods, and
    returns a mapping from function name to all locations where that name
    is defined. Multiple entries occur when the same name exists in different
    files or classes.

    Args:
        files: All parsed FileEntry objects keyed by relative path.

    Returns:
        A dict mapping function names to lists of FunctionLocator objects.
    """
    lookup: dict[str, list[FunctionLocator]] = {}

    for file_path, file_entry in files.items():
        # Top-level functions
        for func_name, func_entry in file_entry.functions.items():
            locator = FunctionLocator(
                file_path=file_path,
                class_name=None,
                function_name=func_name,
                qualified_name=func_entry.qualified_name or func_name,
            )
            if func_name not in lookup:
                lookup[func_name] = []
            lookup[func_name].append(locator)

        # Class methods
        for class_name, class_entry in file_entry.classes.items():
            for method_name, method_entry in class_entry.methods.items():
                locator = FunctionLocator(
                    file_path=file_path,
                    class_name=class_name,
                    function_name=method_name,
                    qualified_name=method_entry.qualified_name or f"{class_name}.{method_name}",
                )
                if method_name not in lookup:
                    lookup[method_name] = []
                lookup[method_name].append(locator)

    return lookup


def _get_imported_module_names(file_entry: FileEntry) -> set[str]:
    """Collect all module aliases and imported names from a file's imports."""
    names: set[str] = set()
    for imp in file_entry.imports:
        if imp.alias:
            names.add(imp.alias)
        elif imp.module:
            # For 'import foo.bar', the usable name is 'foo'
            top = imp.module.split(".")[0]
            names.add(top)
        if not imp.alias:
            for name in imp.names:
                names.add(name)
    return names


def _build_import_file_map(file_entry: FileEntry) -> dict[str, str]:
    """Build a mapping from imported name/alias to likely source file path.

    For 'from src.repo import Repo', maps 'Repo' -> 'src/repo.py'.
    For 'import os.path as osp', maps 'osp' -> 'os/path.py' (best effort).
    """
    name_to_file: dict[str, str] = {}
    for imp in file_entry.imports:
        if not imp.module:
            continue
        # Convert module path to file path: src.repo -> src/repo.py
        module_as_path = imp.module.lstrip(".").replace(".", "/") + ".py"
        if imp.is_from_import:
            for name in imp.names:
                key = imp.alias if (imp.alias and len(imp.names) == 1) else name
                name_to_file[key] = module_as_path
        else:
            key = imp.alias if imp.alias else imp.module.split(".")[0]
            name_to_file[key] = module_as_path
    return name_to_file


def _build_imported_name_alias_map(file_entry: FileEntry) -> dict[str, str]:
    """Map local from-import aliases back to their original imported names."""
    alias_to_original: dict[str, str] = {}
    for imp in file_entry.imports:
        if imp.is_from_import and imp.alias and len(imp.names) == 1:
            alias_to_original[imp.alias] = imp.names[0]
    return alias_to_original


def _find_func_entry_in_files(
    files: dict[str, FileEntry],
    locator: FunctionLocator,
) -> FunctionEntry | None:
    """Look up the FunctionEntry for a resolved locator."""
    callee_file = files.get(locator.file_path)
    if callee_file is None:
        return None
    if locator.class_name is not None:
        callee_class = callee_file.classes.get(locator.class_name)
        if callee_class is not None:
            return callee_class.methods.get(locator.function_name)
    else:
        return callee_file.functions.get(locator.function_name)


def _find_enclosing_class(
    caller_func: FunctionEntry,
    file_entry: FileEntry,
) -> ClassEntry | None:
    """Find the ClassEntry that contains the caller function.

    Uses the qualified_name (e.g., 'ClassName.method') to identify the class.
    """
    qn = caller_func.qualified_name or ""
    if "." not in qn:
        return None
    class_name = qn.rsplit(".", 1)[0]
    return file_entry.classes.get(class_name)


def _resolve_typed_attribute(
    obj_prefix: str,
    enclosing_class: ClassEntry,
    files: dict[str, FileEntry],
) -> str | None:
    """Look up the type of an instance attribute to resolve obj.method() calls.

    Given obj_prefix (e.g., '_config'), find it in enclosing_class.instance_attributes,
    read its type_annotation, and return the type name (e.g., 'ServerConfig').
    Returns None if the attribute or its type is not found.
    """
    for attr in enclosing_class.instance_attributes:
        if attr.name == obj_prefix and attr.type_annotation:
            # Strip Optional[], list[], etc. to get the base type name
            type_name = attr.type_annotation
            # Handle Optional[X] -> X, X | None -> X
            if type_name.startswith("Optional[") and type_name.endswith("]"):
                type_name = type_name[9:-1]
            if " | None" in type_name:
                type_name = type_name.replace(" | None", "").strip()
            if "None | " in type_name:
                type_name = type_name.replace("None | ", "").strip()
            # Strip generic params: Dict[str, Any] -> Dict
            bracket = type_name.find("[")
            if bracket != -1:
                type_name = type_name[:bracket]
            # Get the simple name (last component of dotted path)
            if "." in type_name:
                type_name = type_name.rsplit(".", 1)[-1]
            return type_name
    return None


def resolve_call_graph(
    files: dict[str, FileEntry],
    function_lookup: dict[str, list[FunctionLocator]],
) -> None:
    """Resolve call names to file paths and build called_by reverse index.

    Mutates FunctionEntry.calls in place — sets resolved_file,
    resolved_qualified_name, and is_external on each CallEntry. Also
    populates FunctionEntry.called_by on the target functions.

    Resolution priority:
    1. Same file (prefer local definitions)
    2. Imported modules (names that match imported symbols)
    3. Any match across all files

    After resolution, re-renders tier2_text for all affected functions.

    Args:
        files: All parsed FileEntry objects keyed by relative path.
        function_lookup: Cross-file function name index for resolution.
    """
    # Import renderer here to avoid circular imports at module load time
    from abstract_engine.renderer import render_tier2_function  # noqa: PLC0415

    # Clear all existing called_by entries before rebuilding
    for file_entry in files.values():
        for func in file_entry.functions.values():
            func.called_by = []
        for cls in file_entry.classes.values():
            for method in cls.methods.values():
                method.called_by = []

    # For each caller function, resolve its calls
    for caller_file_path, file_entry in files.items():
        imported_names = _get_imported_module_names(file_entry)
        import_file_map = _build_import_file_map(file_entry)
        import_name_alias_map = _build_imported_name_alias_map(file_entry)

        all_caller_funcs = list(file_entry.functions.items())
        for cls in file_entry.classes.values():
            for method_name, method in cls.methods.items():
                all_caller_funcs.append((method_name, method))

        for _func_name, caller_func in all_caller_funcs:
            for call in caller_func.calls:
                call.resolved_file = None
                call.resolved_qualified_name = None
                call.resolved_start_line = None
                call.resolved_end_line = None
                call.is_external = False
                call.match_count = 0

                # Extract the base call name (strip object prefix like 'self.foo' -> 'foo')
                raw_name = call.callee_name
                # Handle attribute calls: 'obj.method' -> look up 'method'
                if "." in raw_name:
                    parts = raw_name.split(".")
                    base_name = parts[-1]
                    obj_prefix = ".".join(parts[:-1])
                else:
                    base_name = raw_name
                    obj_prefix = ""

                lookup_name = (
                    import_name_alias_map.get(base_name, base_name)
                    if not obj_prefix
                    else base_name
                )
                locators = function_lookup.get(lookup_name, [])
                call.match_count = len(locators)

                resolved_locator: FunctionLocator | None = None
                confidence = "exact"

                if locators:
                    if len(locators) == 1:
                        resolved_locator = locators[0]
                        confidence = "exact"
                    else:
                        # Priority 0: self/cls method resolution — most reliable signal
                        if obj_prefix in ("self", "cls"):
                            enclosing_class = _find_enclosing_class(caller_func, file_entry)
                            if enclosing_class is not None:
                                self_match = [
                                    loc for loc in locators
                                    if loc.class_name == enclosing_class.name
                                    and loc.file_path == caller_file_path
                                ]
                                if self_match:
                                    resolved_locator = self_match[0]
                                    confidence = "exact"

                        # Priority 0b: typed attribute resolution
                        # Handles both `attr.method()` (obj_prefix="attr") and
                        # `self.attr.method()` (obj_prefix="self.attr")
                        if resolved_locator is None and obj_prefix and obj_prefix not in ("self", "cls"):
                            # Extract the attribute name to look up
                            attr_name = obj_prefix
                            if obj_prefix.startswith("self.") or obj_prefix.startswith("cls."):
                                attr_name = obj_prefix.split(".", 1)[1]
                                # Only handle single-level (self.attr), not self.a.b
                                if "." in attr_name:
                                    attr_name = ""
                            elif "." in obj_prefix:
                                attr_name = ""  # Skip multi-dot prefixes without self/cls

                            if attr_name:
                                enclosing_class = _find_enclosing_class(caller_func, file_entry)
                                if enclosing_class is not None:
                                    type_name = _resolve_typed_attribute(attr_name, enclosing_class, files)
                                    if type_name:
                                        typed_match = [
                                            loc for loc in locators
                                            if loc.class_name == type_name
                                        ]
                                        if typed_match:
                                            resolved_locator = typed_match[0]
                                            confidence = "exact"

                        # Priority 1: same file
                        if resolved_locator is None:
                            same_file = [loc for loc in locators if loc.file_path == caller_file_path]
                            if same_file:
                                resolved_locator = same_file[0]
                                confidence = "preferred"

                        if resolved_locator is None:
                            # Priority 2: import-based resolution
                            # If obj_prefix maps to a specific imported module file, prefer that
                            if obj_prefix and obj_prefix in import_file_map:
                                target_file = import_file_map[obj_prefix]
                                # Match locators whose file path ends with the target
                                import_file_match = [
                                    loc for loc in locators
                                    if loc.file_path == target_file
                                    or loc.file_path.endswith("/" + target_file)
                                    or target_file.endswith("/" + loc.file_path)
                                ]
                                if import_file_match:
                                    resolved_locator = import_file_match[0]
                                    confidence = "preferred"

                            # Direct from-import call: `from mod import fn as alias`;
                            # the callable has no object prefix, but the local name
                            # still maps to a concrete source module.
                            if resolved_locator is None and not obj_prefix and base_name in import_file_map:
                                target_file = import_file_map[base_name]
                                import_file_match = [
                                    loc for loc in locators
                                    if loc.file_path == target_file
                                    or loc.file_path.endswith("/" + target_file)
                                    or target_file.endswith("/" + loc.file_path)
                                ]
                                if import_file_match:
                                    resolved_locator = import_file_match[0]
                                    confidence = "preferred"

                            # Priority 2b: obj_prefix is any imported name
                            if resolved_locator is None and obj_prefix and obj_prefix in imported_names:
                                # Filter to locators from files plausibly matching
                                # the import; fall back to first match
                                resolved_locator = locators[0]
                                confidence = "preferred"

                            if resolved_locator is None:
                                # Priority 3: any match — ambiguous
                                resolved_locator = locators[0]
                                confidence = "ambiguous"

                call.resolution_confidence = confidence

                if resolved_locator is not None:
                    call.resolved_file = resolved_locator.file_path
                    call.resolved_qualified_name = resolved_locator.qualified_name
                    call.is_external = False

                    # Look up line numbers of the resolved callee
                    callee_func_entry = _find_func_entry_in_files(files, resolved_locator)
                    if callee_func_entry is not None:
                        call.resolved_start_line = callee_func_entry.start_line
                        call.resolved_end_line = callee_func_entry.end_line

                    # Build reverse index: add caller to the callee's called_by
                    callee_file = files.get(resolved_locator.file_path)
                    if callee_file is not None:
                        caller_entry = CallerEntry(
                            caller_name=caller_func.name,
                            caller_file=caller_file_path,
                            caller_qualified_name=caller_func.qualified_name,
                            start_line=caller_func.start_line,
                            end_line=caller_func.end_line,
                            resolution_confidence=confidence,
                        )
                        # Find the callee function entry
                        if resolved_locator.class_name is not None:
                            callee_class = callee_file.classes.get(resolved_locator.class_name)
                            if callee_class is not None:
                                callee_func = callee_class.methods.get(resolved_locator.function_name)
                                if callee_func is not None:
                                    # Avoid duplicate entries
                                    existing = {
                                        (c.caller_name, c.caller_file)
                                        for c in callee_func.called_by
                                    }
                                    if (caller_func.name, caller_file_path) not in existing:
                                        callee_func.called_by.append(caller_entry)
                        else:
                            callee_func = callee_file.functions.get(resolved_locator.function_name)
                            if callee_func is not None:
                                existing = {
                                    (c.caller_name, c.caller_file)
                                    for c in callee_func.called_by
                                }
                                if (caller_func.name, caller_file_path) not in existing:
                                    callee_func.called_by.append(caller_entry)
                else:
                    # Could not resolve — mark as external
                    call.is_external = True

    # Re-render tier2_text for all functions after resolution
    for rel_path, file_entry in files.items():
        for func in file_entry.functions.values():
            func.tier2_text = render_tier2_function(func, rel_path, function_lookup)
        for cls in file_entry.classes.values():
            for method in cls.methods.values():
                method.tier2_text = render_tier2_function(method, rel_path, function_lookup)
