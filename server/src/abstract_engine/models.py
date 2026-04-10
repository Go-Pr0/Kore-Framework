"""Data models for the Abstract Representation Engine.

All dataclass definitions for the abstract codebase index. These models are
language-agnostic — no Python-specific or TypeScript-specific logic belongs here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 3


@dataclass
class ParameterEntry:
    """A single function/method parameter."""

    name: str
    type_annotation: str | None = None
    has_default: bool = False
    default_value: str | None = None
    is_variadic: bool = False
    is_keyword_variadic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_annotation": self.type_annotation,
            "has_default": self.has_default,
            "default_value": self.default_value,
            "is_variadic": self.is_variadic,
            "is_keyword_variadic": self.is_keyword_variadic,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParameterEntry:
        return cls(
            name=data["name"],
            type_annotation=data.get("type_annotation"),
            has_default=data.get("has_default", False),
            default_value=data.get("default_value"),
            is_variadic=data.get("is_variadic", False),
            is_keyword_variadic=data.get("is_keyword_variadic", False),
        )


@dataclass
class CallEntry:
    """A function call made from within a function body."""

    callee_name: str
    resolved_file: str | None = None
    resolved_qualified_name: str | None = None
    is_external: bool = False
    call_count: int = 1
    resolution_confidence: str = "exact"
    match_count: int = 1
    resolved_start_line: int = 0
    resolved_end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "callee_name": self.callee_name,
            "resolved_file": self.resolved_file,
            "resolved_qualified_name": self.resolved_qualified_name,
            "is_external": self.is_external,
            "call_count": self.call_count,
            "resolution_confidence": self.resolution_confidence,
            "match_count": self.match_count,
            "resolved_start_line": self.resolved_start_line,
            "resolved_end_line": self.resolved_end_line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CallEntry:
        return cls(
            callee_name=data["callee_name"],
            resolved_file=data.get("resolved_file"),
            resolved_qualified_name=data.get("resolved_qualified_name"),
            is_external=data.get("is_external", False),
            call_count=data.get("call_count", 1),
            resolution_confidence=data.get("resolution_confidence", "exact"),
            match_count=data.get("match_count", 1),
            resolved_start_line=data.get("resolved_start_line", 0),
            resolved_end_line=data.get("resolved_end_line", 0),
        )


@dataclass
class CallerEntry:
    """A reference to a function that calls this one."""

    caller_name: str
    caller_file: str
    caller_qualified_name: str | None = None
    start_line: int = 0
    end_line: int = 0
    resolution_confidence: str = "exact"  # mirrors CallEntry.resolution_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller_name": self.caller_name,
            "caller_file": self.caller_file,
            "caller_qualified_name": self.caller_qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "resolution_confidence": self.resolution_confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CallerEntry:
        return cls(
            caller_name=data["caller_name"],
            caller_file=data["caller_file"],
            caller_qualified_name=data.get("caller_qualified_name"),
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            resolution_confidence=data.get("resolution_confidence", "exact"),
        )


@dataclass
class ImportEntry:
    """An import statement in a file."""

    module: str
    names: list[str] = field(default_factory=list)
    is_from_import: bool = False
    is_wildcard: bool = False
    alias: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "names": self.names,
            "is_from_import": self.is_from_import,
            "is_wildcard": self.is_wildcard,
            "alias": self.alias,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImportEntry:
        return cls(
            module=data["module"],
            names=data.get("names", []),
            is_from_import=data.get("is_from_import", False),
            is_wildcard=data.get("is_wildcard", False),
            alias=data.get("alias"),
        )


@dataclass
class ConstantEntry:
    """A module-level constant (ALL_CAPS pattern)."""

    name: str
    value: str | None = None
    type_annotation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "type_annotation": self.type_annotation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConstantEntry:
        return cls(
            name=data["name"],
            value=data.get("value"),
            type_annotation=data.get("type_annotation"),
        )


@dataclass
class FunctionEntry:
    """A function or method extracted from source code."""

    name: str
    qualified_name: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    start_byte: int = 0
    end_byte: int = 0
    is_async: bool = False
    is_generator: bool = False
    is_property: bool = False
    is_classmethod: bool = False
    is_staticmethod: bool = False
    is_abstract: bool = False
    visibility: str = "public"
    decorators: list[str] = field(default_factory=list)
    parameters: list[ParameterEntry] = field(default_factory=list)
    return_type: str | None = None
    docstring_first_line: str | None = None
    docstring_full: str | None = None
    calls: list[CallEntry] = field(default_factory=list)
    called_by: list[CallerEntry] = field(default_factory=list)
    raises: list[str] = field(default_factory=list)
    tier2_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "is_async": self.is_async,
            "is_generator": self.is_generator,
            "is_property": self.is_property,
            "is_classmethod": self.is_classmethod,
            "is_staticmethod": self.is_staticmethod,
            "is_abstract": self.is_abstract,
            "visibility": self.visibility,
            "decorators": self.decorators,
            "parameters": [p.to_dict() for p in self.parameters],
            "return_type": self.return_type,
            "docstring_first_line": self.docstring_first_line,
            "docstring_full": self.docstring_full,
            "calls": [c.to_dict() for c in self.calls],
            "called_by": [c.to_dict() for c in self.called_by],
            "raises": self.raises,
            "tier2_text": self.tier2_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionEntry:
        return cls(
            name=data["name"],
            qualified_name=data.get("qualified_name", ""),
            file_path=data.get("file_path", ""),
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            start_byte=data.get("start_byte", 0),
            end_byte=data.get("end_byte", 0),
            is_async=data.get("is_async", False),
            is_generator=data.get("is_generator", False),
            is_property=data.get("is_property", False),
            is_classmethod=data.get("is_classmethod", False),
            is_staticmethod=data.get("is_staticmethod", False),
            is_abstract=data.get("is_abstract", False),
            visibility=data.get("visibility", "public"),
            decorators=data.get("decorators", []),
            parameters=[ParameterEntry.from_dict(p) for p in data.get("parameters", [])],
            return_type=data.get("return_type"),
            docstring_first_line=data.get("docstring_first_line"),
            docstring_full=data.get("docstring_full"),
            calls=[CallEntry.from_dict(c) for c in data.get("calls", [])],
            called_by=[CallerEntry.from_dict(c) for c in data.get("called_by", [])],
            raises=data.get("raises", []),
            tier2_text=data.get("tier2_text", ""),
        )


@dataclass
class AttributeEntry:
    """A class or instance attribute."""

    name: str
    type_annotation: str | None = None
    has_default: bool = False
    default_value: str | None = None
    visibility: str = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_annotation": self.type_annotation,
            "has_default": self.has_default,
            "default_value": self.default_value,
            "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttributeEntry:
        return cls(
            name=data["name"],
            type_annotation=data.get("type_annotation"),
            has_default=data.get("has_default", False),
            default_value=data.get("default_value"),
            visibility=data.get("visibility", "public"),
        )


@dataclass
class ClassEntry:
    """A class definition extracted from source code."""

    name: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    base_classes: list[str] = field(default_factory=list)
    is_dataclass: bool = False
    is_protocol: bool = False
    is_abstract: bool = False
    docstring_first_line: str | None = None
    methods: dict[str, FunctionEntry] = field(default_factory=dict)
    class_attributes: list[AttributeEntry] = field(default_factory=list)
    instance_attributes: list[AttributeEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "base_classes": self.base_classes,
            "is_dataclass": self.is_dataclass,
            "is_protocol": self.is_protocol,
            "is_abstract": self.is_abstract,
            "docstring_first_line": self.docstring_first_line,
            "methods": {k: v.to_dict() for k, v in self.methods.items()},
            "class_attributes": [a.to_dict() for a in self.class_attributes],
            "instance_attributes": [a.to_dict() for a in self.instance_attributes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassEntry:
        return cls(
            name=data["name"],
            file_path=data.get("file_path", ""),
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
            base_classes=data.get("base_classes", []),
            is_dataclass=data.get("is_dataclass", False),
            is_protocol=data.get("is_protocol", False),
            is_abstract=data.get("is_abstract", False),
            docstring_first_line=data.get("docstring_first_line"),
            methods={
                k: FunctionEntry.from_dict(v) for k, v in data.get("methods", {}).items()
            },
            class_attributes=[
                AttributeEntry.from_dict(a) for a in data.get("class_attributes", [])
            ],
            instance_attributes=[
                AttributeEntry.from_dict(a) for a in data.get("instance_attributes", [])
            ],
        )


@dataclass
class TypeEntry:
    """A type definition (dataclass, TypedDict, NamedTuple, Pydantic, Protocol, type alias, or TS interface/enum)."""

    name: str
    kind: str = ""  # 'dataclass', 'typeddict', 'namedtuple', 'pydantic', 'protocol', 'type_alias', 'interface', 'enum'
    file_path: str = ""
    start_line: int = 0
    source_text: str = ""
    fields: list[AttributeEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "source_text": self.source_text,
            "fields": [f.to_dict() for f in self.fields],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TypeEntry:
        return cls(
            name=data["name"],
            kind=data.get("kind", ""),
            file_path=data.get("file_path", ""),
            start_line=data.get("start_line", 0),
            source_text=data.get("source_text", ""),
            fields=[AttributeEntry.from_dict(f) for f in data.get("fields", [])],
        )


@dataclass
class FunctionLocator:
    """Lightweight reference to a function's location for cross-file lookup."""

    file_path: str
    class_name: str | None = None
    function_name: str = ""
    qualified_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "class_name": self.class_name,
            "function_name": self.function_name,
            "qualified_name": self.qualified_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FunctionLocator:
        return cls(
            file_path=data["file_path"],
            class_name=data.get("class_name"),
            function_name=data.get("function_name", ""),
            qualified_name=data.get("qualified_name", ""),
        )


@dataclass
class SemanticRegionEntry:
    """A normalized semantic chunk extracted from a file."""

    kind: str
    title: str
    text: str
    start_line: int = 0
    end_line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "text": self.text,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticRegionEntry:
        return cls(
            kind=data.get("kind", "region"),
            title=data.get("title", ""),
            text=data.get("text", ""),
            start_line=data.get("start_line", 0),
            end_line=data.get("end_line", 0),
        )


@dataclass
class FileEntry:
    """A single source file in the abstract index."""

    relative_path: str
    language: str = ""
    extraction_mode: str = "generalized"
    line_count: int = 0
    last_modified: float = 0.0
    content_hash: str = ""
    module_docstring: str | None = None
    imports: list[ImportEntry] = field(default_factory=list)
    classes: dict[str, ClassEntry] = field(default_factory=dict)
    functions: dict[str, FunctionEntry] = field(default_factory=dict)
    constants: dict[str, ConstantEntry] = field(default_factory=dict)
    types: dict[str, TypeEntry] = field(default_factory=dict)
    tier1_text: str = ""
    semantic_regions: list[SemanticRegionEntry] = field(default_factory=list)
    parse_error: bool = False
    parse_error_detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "language": self.language,
            "extraction_mode": self.extraction_mode,
            "line_count": self.line_count,
            "last_modified": self.last_modified,
            "content_hash": self.content_hash,
            "module_docstring": self.module_docstring,
            "imports": [i.to_dict() for i in self.imports],
            "classes": {k: v.to_dict() for k, v in self.classes.items()},
            "functions": {k: v.to_dict() for k, v in self.functions.items()},
            "constants": {k: v.to_dict() for k, v in self.constants.items()},
            "types": {k: v.to_dict() for k, v in self.types.items()},
            "tier1_text": self.tier1_text,
            "semantic_regions": [region.to_dict() for region in self.semantic_regions],
            "parse_error": self.parse_error,
            "parse_error_detail": self.parse_error_detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileEntry:
        return cls(
            relative_path=data["relative_path"],
            language=data.get("language", ""),
            extraction_mode=data.get("extraction_mode", "generalized"),
            line_count=data.get("line_count", 0),
            last_modified=data.get("last_modified", 0.0),
            content_hash=data.get("content_hash", ""),
            module_docstring=data.get("module_docstring"),
            imports=[ImportEntry.from_dict(i) for i in data.get("imports", [])],
            classes={
                k: ClassEntry.from_dict(v) for k, v in data.get("classes", {}).items()
            },
            functions={
                k: FunctionEntry.from_dict(v)
                for k, v in data.get("functions", {}).items()
            },
            constants={
                k: ConstantEntry.from_dict(v)
                for k, v in data.get("constants", {}).items()
            },
            types={
                k: TypeEntry.from_dict(v)
                for k, v in data.get("types", {}).items()
            },
            tier1_text=data.get("tier1_text", ""),
            semantic_regions=[
                SemanticRegionEntry.from_dict(region)
                for region in data.get("semantic_regions", [])
            ],
            parse_error=data.get("parse_error", False),
            parse_error_detail=data.get("parse_error_detail"),
        )
