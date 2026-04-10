"""Generalized tree-sitter parser driven by LanguageConfig.

Replaces language-specific parser classes (PythonFileParser, etc.) with a single
TreeSitterParser that delegates all language-specific behavior to a LanguageConfig
instance. Adding a new language is a config task, not a coding task.
"""

from __future__ import annotations

import logging

from tree_sitter import Node, Parser, Query, QueryCursor

from abstract_engine.lang_config import LanguageConfig
from abstract_engine.models import (
    ClassEntry,
    FileEntry,
    FunctionEntry,
)
from abstract_engine.parser import FileParser
from abstract_engine.py_utils import _node_text
from abstract_engine.renderer import render_tier1_file, render_tier2_function

logger = logging.getLogger(__name__)


class TreeSitterParser(FileParser):
    """Language-agnostic file parser driven by a LanguageConfig.

    All language-specific logic — node types, extraction of imports, parameters,
    docstrings, calls, etc. — comes from the config's extractor callables.
    Structural assembly of FunctionEntry/ClassEntry is shared across languages.
    """

    def __init__(self, config: LanguageConfig) -> None:
        self._config = config
        self._language = config.grammar_loader()
        self._parser = Parser(self._language)
        self._query_cache: dict[str, Query] = {}

    # ------------------------------------------------------------------
    # FileParser ABC
    # ------------------------------------------------------------------

    def parse_file(self, file_path: str, source_bytes: bytes) -> FileEntry:
        """Parse a source file into a FileEntry using the language config."""
        cfg = self._config
        ntm = cfg.node_types
        tree = self._parser.parse(source_bytes)
        root = tree.root_node

        # 1. Collect parse errors
        if cfg.collect_parse_errors is not None:
            parse_errors = cfg.collect_parse_errors(root, source_bytes)
        else:
            parse_errors = self._default_collect_parse_errors(root, source_bytes)

        # 2. Create FileEntry shell
        entry = FileEntry(
            relative_path=file_path,
            language=cfg.language_id,
            line_count=source_bytes.count(b"\n") + (
                1 if source_bytes and not source_bytes.endswith(b"\n") else 0
            ),
        )

        if parse_errors:
            entry.parse_error = True
            detail = "; ".join(parse_errors[:5])
            if len(parse_errors) > 5:
                detail += f" (+{len(parse_errors) - 5} more)"
            entry.parse_error_detail = detail

        # 3. Extract module docstring
        entry.module_docstring = cfg.extract_module_docstring(root, source_bytes)

        # 4. Extract imports
        entry.imports = cfg.extract_imports(root, source_bytes)

        # 5. Extract constants
        entry.constants = cfg.extract_constants(root, source_bytes)

        # 6. Walk root children for functions, classes, decorated defs, exports
        for child in root.children:
            if child.type == ntm.function_def:
                func = self._build_function_entry(
                    child, source_bytes, file_path, decorators=[]
                )
                entry.functions[func.name] = func

            elif ntm.decorated_def is not None and child.type == ntm.decorated_def:
                decorators = (
                    cfg.extract_decorators(child, source_bytes)
                    if cfg.extract_decorators is not None
                    else []
                )
                inner = None
                for sub in child.children:
                    if sub.type == ntm.function_def:
                        inner = sub
                        break
                    if sub.type == ntm.class_def:
                        inner = sub
                        break

                if inner is not None and inner.type == ntm.function_def:
                    func = self._build_function_entry(
                        inner, source_bytes, file_path, decorators=decorators,
                        decorated_node=child,
                    )
                    entry.functions[func.name] = func
                elif inner is not None and inner.type == ntm.class_def:
                    cls = self._build_class_entry(
                        inner, source_bytes, file_path, decorators=decorators,
                        decorated_node=child,
                    )
                    entry.classes[cls.name] = cls

            elif child.type == ntm.class_def:
                cls = self._build_class_entry(
                    child, source_bytes, file_path, decorators=[]
                )
                entry.classes[cls.name] = cls

            elif ntm.export_wrapper is not None and child.type == ntm.export_wrapper:
                # Unwrap export wrappers (e.g., TS export statements)
                for sub in child.children:
                    if sub.type == ntm.function_def:
                        func = self._build_function_entry(
                            sub, source_bytes, file_path, decorators=[]
                        )
                        entry.functions[func.name] = func
                    elif sub.type == ntm.class_def:
                        cls_entry = self._build_class_entry(
                            sub, source_bytes, file_path, decorators=[]
                        )
                        entry.classes[cls_entry.name] = cls_entry

        # 7. Extract types
        if cfg.extract_types is not None:
            entry.types = cfg.extract_types(entry, file_path)

        # 8. Extract type aliases
        if cfg.extract_type_aliases is not None:
            cfg.extract_type_aliases(root, source_bytes, file_path, entry)

        # 9. Pre-render tier text
        entry.tier1_text = render_tier1_file(entry)
        for func in entry.functions.values():
            func.tier2_text = render_tier2_function(func)
        for cls in entry.classes.values():
            for method in cls.methods.values():
                method.tier2_text = render_tier2_function(method)

        return entry

    def extract_function_source(
        self,
        source_bytes: bytes,
        function_name: str,
        class_name: str | None = None,
    ) -> str | None:
        """Extract the full source of a function by name."""
        tree = self._parser.parse(source_bytes)
        root = tree.root_node
        ntm = self._config.node_types

        if class_name is not None:
            class_node = self._find_class_node(root, class_name, source_bytes)
            if class_node is None:
                return None
            body = class_node.child_by_field_name("body")
            if body is None:
                return None
            return self._find_function_source(body, function_name, source_bytes)

        return self._find_function_source(root, function_name, source_bytes)

    def supported_extensions(self) -> list[str]:
        """Return the file extensions this parser handles."""
        return list(self._config.extensions)

    # ------------------------------------------------------------------
    # Query-based node finders (for write-pipeline integration)
    # ------------------------------------------------------------------

    def find_function_node(
        self, tree: object, function_name: str
    ) -> tuple[Node, Node] | None:
        """Find a top-level function by name using tree-sitter queries.

        Returns (function_definition_node, outermost_node) where outermost
        includes decorators if present. Returns None if not found.
        """
        ntm = self._config.node_types
        query_src = f"({ntm.function_def} name: ({ntm.function_name}) @fn.name) @fn.def"

        if query_src not in self._query_cache:
            self._query_cache[query_src] = Query(self._language, query_src)
        query = self._query_cache[query_src]
        captures = QueryCursor(query).captures(tree.root_node)

        fn_defs = captures.get("fn.def", [])
        fn_names = captures.get("fn.name", [])

        found: list[tuple[Node, Node]] = []
        for fn_def, fn_name in zip(fn_defs, fn_names):
            if fn_name.text.decode("utf-8") == function_name:
                parent = fn_def.parent
                outermost = fn_def
                if parent and ntm.decorated_def and parent.type == ntm.decorated_def:
                    outermost = parent
                found.append((fn_def, outermost))

        if not found:
            return None

        if len(found) > 1:
            logger.warning(
                "Multiple definitions of %s found; using shallowest at line %d.",
                function_name,
                found[0][1].start_point[0] + 1,
            )

        def _nesting_depth(node: Node) -> int:
            depth = 0
            p = node.parent
            while p is not None:
                if p.type in (ntm.function_def, ntm.class_def):
                    depth += 1
                p = p.parent
            return depth

        found.sort(key=lambda pair: _nesting_depth(pair[0]))
        return found[0]

    def find_method_node(
        self, tree: object, class_name: str, method_name: str
    ) -> tuple[Node, Node] | None:
        """Find a method inside a specific class using tree-sitter queries.

        Returns (method_definition_node, outermost_node) or None.
        """
        ntm = self._config.node_types
        query_src = (
            f"({ntm.class_def}\n"
            f"  name: ({ntm.class_name}) @class.name\n"
            f"  body: ({ntm.class_body}\n"
            f"    ({ntm.method_def}\n"
            f"      name: ({ntm.method_name}) @method.name) @method.def))"
        )

        if query_src not in self._query_cache:
            self._query_cache[query_src] = Query(self._language, query_src)
        query = self._query_cache[query_src]
        captures = QueryCursor(query).captures(tree.root_node)

        method_names = captures.get("method.name", [])
        method_defs = captures.get("method.def", [])

        for m_def, m_name in zip(method_defs, method_names):
            if m_name.text.decode("utf-8") != method_name:
                continue
            enclosing = self._find_enclosing_class_name(m_def)
            if enclosing == class_name:
                parent = m_def.parent
                outermost = m_def
                if parent and ntm.decorated_def and parent.type == ntm.decorated_def:
                    outermost = parent
                return m_def, outermost

        # Fallback: check for decorated methods
        if ntm.decorated_def:
            query_src_dec = (
                f"({ntm.class_def}\n"
                f"  name: ({ntm.class_name}) @class.name\n"
                f"  body: ({ntm.class_body}\n"
                f"    ({ntm.decorated_def}\n"
                f"      definition: ({ntm.method_def}\n"
                f"        name: ({ntm.method_name}) @method.name) @method.def) @decorated))"
            )
            try:
                if query_src_dec not in self._query_cache:
                    self._query_cache[query_src_dec] = Query(self._language, query_src_dec)
                query_dec = self._query_cache[query_src_dec]
                captures_dec = QueryCursor(query_dec).captures(tree.root_node)

                dec_method_names = captures_dec.get("method.name", [])
                dec_method_defs = captures_dec.get("method.def", [])
                dec_decorated = captures_dec.get("decorated", [])

                for m_name, m_def, decorated in zip(dec_method_names, dec_method_defs, dec_decorated):
                    if m_name.text.decode("utf-8") != method_name:
                        continue
                    enclosing = self._find_enclosing_class_name(m_def)
                    if enclosing == class_name:
                        return m_def, decorated
            except Exception:  # noqa: BLE001
                pass

        return None

    # ------------------------------------------------------------------
    # Internal: build FunctionEntry
    # ------------------------------------------------------------------

    def _build_function_entry(
        self,
        func_node: Node,
        source: bytes,
        file_path: str,
        decorators: list[str],
        class_name: str | None = None,
        decorated_node: Node | None = None,
    ) -> FunctionEntry:
        """Build a FunctionEntry from a function definition node."""
        cfg = self._config
        ntm = cfg.node_types

        name_node = func_node.child_by_field_name("name")
        name = _node_text(name_node, source) if name_node else ""

        params_node = func_node.child_by_field_name(ntm.params_node)
        parameters = cfg.extract_parameters(params_node, source) if params_node else []

        return_type = cfg.extract_return_type(func_node, source)

        body_node = func_node.child_by_field_name("body")
        docstring_first, docstring_full = (
            cfg.extract_docstring(body_node, source) if body_node else (None, None)
        )

        # Detect async
        is_async = cfg.check_async(func_node, source)

        # Detect generator
        is_generator = (
            cfg.check_generator(body_node) if cfg.check_generator and body_node else False
        )

        # Decorator flags
        dec_lower = [d.lower() for d in decorators]
        is_property = "property" in dec_lower
        is_classmethod = "classmethod" in dec_lower
        is_staticmethod = "staticmethod" in dec_lower
        is_abstract = "abstractmethod" in dec_lower or "abc.abstractmethod" in dec_lower

        # Qualified name
        qualified_name = f"{class_name}.{name}" if class_name else name

        # Visibility
        visibility = cfg.extract_visibility(name)

        # Calls and raises
        calls = cfg.extract_calls(body_node, source) if body_node else []
        raises = (
            cfg.extract_raises(body_node, source)
            if cfg.extract_raises is not None and body_node
            else []
        )

        # Byte/line range: use decorated_node if available for the full range
        range_node = decorated_node or func_node
        start_line = range_node.start_point[0] + 1  # 1-based
        end_line = range_node.end_point[0] + 1
        start_byte = range_node.start_byte
        end_byte = range_node.end_byte

        return FunctionEntry(
            name=name,
            qualified_name=qualified_name,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            start_byte=start_byte,
            end_byte=end_byte,
            is_async=is_async,
            is_generator=is_generator,
            is_property=is_property,
            is_classmethod=is_classmethod,
            is_staticmethod=is_staticmethod,
            is_abstract=is_abstract,
            visibility=visibility,
            decorators=decorators,
            parameters=parameters,
            return_type=return_type,
            docstring_first_line=docstring_first,
            docstring_full=docstring_full,
            calls=calls,
            called_by=[],
            raises=raises,
        )

    # ------------------------------------------------------------------
    # Internal: build ClassEntry
    # ------------------------------------------------------------------

    def _build_class_entry(
        self,
        class_node: Node,
        source: bytes,
        file_path: str,
        decorators: list[str],
        decorated_node: Node | None = None,
    ) -> ClassEntry:
        """Build a ClassEntry from a class definition node."""
        cfg = self._config
        ntm = cfg.node_types

        name_node = class_node.child_by_field_name("name")
        name = _node_text(name_node, source) if name_node else ""

        # Base classes from superclasses argument list
        base_classes: list[str] = []
        superclasses_node = class_node.child_by_field_name("superclasses")
        if superclasses_node is not None:
            for child in superclasses_node.children:
                if child.is_named:
                    base_classes.append(_node_text(child, source))

        # Docstring
        body_node = class_node.child_by_field_name("body")
        docstring_first, _ = (
            cfg.extract_docstring(body_node, source) if body_node else (None, None)
        )

        # Detect special class types
        dec_lower = [d.lower() for d in decorators]
        is_dataclass = "dataclass" in dec_lower or "dataclasses.dataclass" in dec_lower
        is_protocol = "Protocol" in base_classes
        is_abstract = "ABC" in base_classes or "ABCMeta" in base_classes

        # Extract methods
        methods: dict[str, FunctionEntry] = {}
        if body_node is not None:
            for child in body_node.children:
                if child.type == ntm.method_def:
                    func = self._build_function_entry(
                        child, source, file_path, decorators=[], class_name=name
                    )
                    methods[func.name] = func
                elif ntm.decorated_def is not None and child.type == ntm.decorated_def:
                    inner_decs = (
                        cfg.extract_decorators(child, source)
                        if cfg.extract_decorators is not None
                        else []
                    )
                    for sub in child.children:
                        if sub.type == ntm.method_def:
                            func = self._build_function_entry(
                                sub,
                                source,
                                file_path,
                                decorators=inner_decs,
                                class_name=name,
                                decorated_node=child,
                            )
                            methods[func.name] = func
                            break

        # Extract attributes
        class_attrs, instance_attrs = (
            cfg.extract_class_attributes(body_node, source)
            if cfg.extract_class_attributes is not None and body_node
            else ([], [])
        )

        # For dataclasses, class-level typed assignments are instance attributes
        if is_dataclass and class_attrs and not instance_attrs:
            instance_attrs = class_attrs
            class_attrs = []

        range_node = decorated_node or class_node
        return ClassEntry(
            name=name,
            file_path=file_path,
            start_line=range_node.start_point[0] + 1,
            end_line=range_node.end_point[0] + 1,
            base_classes=base_classes,
            is_dataclass=is_dataclass,
            is_protocol=is_protocol,
            is_abstract=is_abstract,
            docstring_first_line=docstring_first,
            methods=methods,
            class_attributes=class_attrs,
            instance_attributes=instance_attrs,
        )

    # ------------------------------------------------------------------
    # Internal: source extraction helpers
    # ------------------------------------------------------------------

    def _find_class_node(
        self, root: Node, class_name: str, source: bytes
    ) -> Node | None:
        """Find a class_definition node by name in the tree."""
        ntm = self._config.node_types
        for child in root.children:
            cls_node = self._unwrap_to_class(child)
            if cls_node is not None:
                name_node = cls_node.child_by_field_name("name")
                if name_node and _node_text(name_node, source) == class_name:
                    return cls_node
        return None

    def _find_function_source(
        self, parent: Node, function_name: str, source: bytes
    ) -> str | None:
        """Find a function by name and return its full source including decorators."""
        ntm = self._config.node_types
        for child in parent.children:
            if child.type == ntm.function_def:
                name_node = child.child_by_field_name("name")
                if name_node and _node_text(name_node, source) == function_name:
                    return source[child.start_byte: child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
            elif ntm.decorated_def is not None and child.type == ntm.decorated_def:
                for sub in child.children:
                    if sub.type == ntm.function_def:
                        name_node = sub.child_by_field_name("name")
                        if name_node and _node_text(name_node, source) == function_name:
                            return source[child.start_byte: child.end_byte].decode(
                                "utf-8", errors="replace"
                            )
                        break
        return None

    def _unwrap_to_class(self, node: Node) -> Node | None:
        """Unwrap decorated_definition to get the inner class_definition."""
        ntm = self._config.node_types
        if node.type == ntm.class_def:
            return node
        if ntm.decorated_def is not None and node.type == ntm.decorated_def:
            for child in node.children:
                if child.type == ntm.class_def:
                    return child
        return None

    def _find_enclosing_class_name(self, node: Node) -> str | None:
        """Walk up the tree to find the enclosing class name."""
        ntm = self._config.node_types
        p = node.parent
        while p is not None:
            if p.type == ntm.class_def:
                name_node = p.child_by_field_name("name")
                if name_node:
                    return name_node.text.decode("utf-8")
            p = p.parent
        return None

    @staticmethod
    def _default_collect_parse_errors(root: Node, source: bytes) -> list[str]:
        """Default recursive ERROR/MISSING node collector."""
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
                errors.append(f"missing {node.type!r} at L{line}:{col}")
            stack.extend(node.children)
        return errors


# ---------------------------------------------------------------------------
# Module-level parser cache (shared across write-pipeline and other callers)
# ---------------------------------------------------------------------------

_PARSER_CACHE: dict[str, "TreeSitterParser"] = {}


def get_cached_parser(config: LanguageConfig) -> "TreeSitterParser":
    """Return a cached TreeSitterParser for the given language config.

    Avoids re-compiling grammars and queries on every call site.
    """
    lang_id = config.language_id
    if lang_id not in _PARSER_CACHE:
        _PARSER_CACHE[lang_id] = TreeSitterParser(config)
    return _PARSER_CACHE[lang_id]
