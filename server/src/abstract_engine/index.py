"""AbstractIndex — the orchestrating class for the full project index.

Manages load_or_build, incremental updates, disk persistence, and
cross-file lookup operations. The current runtime uses one generalized
extraction protocol across all indexed files so the same setup can be
shared across arbitrary codebases and file types.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from abstract_engine.lang_config import all_extensions, config_for_language
from abstract_engine.models import (
    ClassEntry,
    SCHEMA_VERSION,
    FileEntry,
    FunctionEntry,
    FunctionLocator,
    SemanticRegionEntry,
    TypeEntry,
)

# Default patterns to exclude during file discovery
_DEFAULT_EXCLUDE = frozenset({
    # VCS / package managers
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    "bower_components",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".tox",
    ".nox",
    ".eggs",
    "egg-info",  # catches any *.egg-info dir via substring match
    ".coverage",
    "migrations",
    # JS / TS build output
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".parcel-cache",
    ".turbo",
    ".cache",
    "out",
    "coverage",
    # Rust / Go / Java build output
    "target",
    "bin",
    "obj",
    # IDE / OS
    ".idea",
    ".vscode",
    ".DS_Store",
    # ML / model weights — critical: these contain gigabyte-scale tokenizer
    # and vocab files (e.g. merges.txt at 150k+ lines) that will wreck the
    # semantic index and trigger OOMs during embedding.
    "models",
    "checkpoints",
    "huggingface",
    ".huggingface",
    "hf-cache",
    # Backup / archive directories
    "backups",
    "backup",
    ".backups",
    # Index file itself
    ".abstract-index.json",
})

# File-size ceiling for any single indexed file.  Files larger than this are
# walked for tier1 metadata but have their semantic-region chunking suppressed
# (and tier1 text clipped) to keep the embedder from blowing up on vocab or
# generated blobs.
_LARGE_FILE_LINES = 3000
_LARGE_FILE_BYTES = 512 * 1024  # 512 KB

# Hard ceiling: any file larger than this is skipped entirely (not indexed at
# all).  Catches generated blobs like tokenizer merges.txt (~3 MB) that would
# otherwise dominate the semantic index and trigger OOMs during embedding.
_MAX_INDEXABLE_BYTES = 2 * 1024 * 1024  # 2 MB


def _load_gitignore_matcher(project_root: str):
    """Return a callable ``(rel_path, is_dir) -> bool`` implementing the
    repo's combined ignore rules, or ``None`` if there are no ignore files or
    ``pathspec`` isn't installed.

    Sources, layered (later ones extend earlier ones):
        1. ``<project_root>/.gitignore``
        2. ``<project_root>/.abstractfsignore``

    Uses ``pathspec`` (gitwildmatch) for correct semantics matching git's own
    behaviour: nested gitignores are not merged (only the top-level ones are
    honoured), which is adequate for the common case of a repo-root ignore.
    """
    try:
        import pathspec  # noqa: PLC0415
    except ImportError:
        return None

    patterns: list[str] = []
    for fname in (".gitignore", ".abstractfsignore"):
        path = os.path.join(project_root, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                patterns.extend(fh.read().splitlines())
        except OSError:
            continue

    if not patterns:
        return None
    try:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except Exception:  # noqa: BLE001
        return None

    def match(rel_path: str, *, is_dir: bool) -> bool:
        # pathspec matches directory entries with a trailing slash.
        candidate = rel_path.replace(os.sep, "/")
        if is_dir and not candidate.endswith("/"):
            candidate = candidate + "/"
        return spec.match_file(candidate)

    return match

_INDEX_FILENAME = ".abstract-index.json"
_GENERIC_CODE_EXTENSIONS = frozenset({
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".swift",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".lua",
    ".sh",
    ".bash",
    ".zsh",
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".xml",
    ".html",
    ".css",
    ".sql",
    ".graphql",
    ".proto",
})


def _file_hash(path: str) -> str:
    """Compute SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ext(path: str) -> str:
    """Return the lowercased file extension including the dot."""
    _, e = os.path.splitext(path)
    return e.lower()


@dataclass
class AbstractIndex:
    """In-memory index of the entire project's abstract representation.

    Provides O(1) lookup for file views, function details, and type definitions.
    Handles cold/warm startup, incremental updates, and disk persistence.
    """

    project_root: str = ""
    indexed_at: float = 0.0
    languages: list[str] = field(default_factory=list)
    files: dict[str, FileEntry] = field(default_factory=dict)
    function_lookup: dict[str, list[FunctionLocator]] = field(default_factory=dict)
    type_lookup: dict[str, TypeEntry] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    # Config (not serialized — reapplied from caller)
    _exclude_patterns: frozenset[str] = field(default_factory=lambda: _DEFAULT_EXCLUDE, repr=False)
    _include_private: bool = field(default=False, repr=False)
    _enabled_extensions: frozenset[str] = field(default_factory=all_extensions, repr=False)
    _index_path: str | None = field(default=None, repr=False)
    # ------------------------------------------------------------------ #
    # Public factory methods
    # ------------------------------------------------------------------ #

    @classmethod
    def load_or_build(
        cls,
        project_root: str,
        config: dict[str, Any] | None = None,
    ) -> AbstractIndex:
        """Load an existing index from disk or build a new one.

        Handles warm start (load + incremental update) and cold start
        (full parse of all files).

        Args:
            project_root: Absolute path to the project root directory.
            config: Optional configuration dict.  Supported keys:
                - exclude_patterns: list[str] — additional patterns to skip
                - include_private: bool — include private (_prefix) functions
                - languages: list[str] — language labels to index
                - index_path: str — custom on-disk JSON index path
        """
        cfg = config or {}
        exclude_extra: list[str] = cfg.get("exclude_patterns", [])
        include_private: bool = cfg.get("include_private", False)
        exclude = _DEFAULT_EXCLUDE | frozenset(exclude_extra)
        languages: list[str] = cfg.get("languages", [])
        extra_extensions = frozenset(
            ext if ext.startswith(".") else f".{ext}"
            for ext in cfg.get("extra_extensions", [])
            if ext
        )
        enabled_exts = cls._extensions_for_languages(languages) | extra_extensions

        index_path = cfg.get("index_path") or os.path.join(project_root, _INDEX_FILENAME)

        # Try warm start
        if os.path.isfile(index_path):
            try:
                idx = cls.load_from_disk(index_path)
                if (
                    idx.schema_version == SCHEMA_VERSION
                    and idx.project_root == project_root
                ):
                    idx._exclude_patterns = exclude
                    idx._include_private = include_private
                    idx._enabled_extensions = enabled_exts
                    idx._index_path = index_path
                    # Incremental update
                    discovered = idx._discover_files(project_root)
                    added, modified, deleted = idx._detect_changes(discovered)
                    for rel in deleted:
                        del idx.files[rel]
                    for rel in added + modified:
                        try:
                            entry = idx._parse_file(project_root, rel)
                            idx.files[rel] = entry
                        except Exception as exc:  # noqa: BLE001
                            idx.files[rel] = FileEntry(
                                relative_path=rel,
                                extraction_mode="error",
                                parse_error=True,
                                parse_error_detail=str(exc),
                            )
                    if added or modified or deleted:
                        idx._rebuild_lookups()
                    return idx
            except Exception:  # noqa: BLE001
                pass  # Fall through to cold start

        # Cold start
        idx = cls(project_root=project_root)
        idx._exclude_patterns = exclude
        idx._include_private = include_private
        idx._enabled_extensions = enabled_exts
        idx._index_path = index_path
        idx.indexed_at = time.time()

        discovered = idx._discover_files(project_root)
        for rel in discovered:
            try:
                entry = idx._parse_file(project_root, rel)
                idx.files[rel] = entry
            except Exception as exc:  # noqa: BLE001
                idx.files[rel] = FileEntry(
                    relative_path=rel,
                    extraction_mode="error",
                    parse_error=True,
                    parse_error_detail=str(exc),
                )

        idx._rebuild_lookups()
        return idx

    @classmethod
    def load_from_disk(cls, index_path: str) -> AbstractIndex:
        """Load a previously persisted index from JSON.

        Args:
            index_path: Absolute path to the .abstract-index.json file.
        """
        with open(index_path, encoding="utf-8") as fh:
            data = json.load(fh)

        idx = cls(
            project_root=data.get("project_root", ""),
            indexed_at=data.get("indexed_at", 0.0),
            languages=data.get("languages", []),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

        for rel_path, file_data in data.get("files", {}).items():
            idx.files[rel_path] = FileEntry.from_dict(file_data)

        for name, type_data in data.get("type_lookup", {}).items():
            idx.type_lookup[name] = TypeEntry.from_dict(type_data)

        idx._index_path = index_path
        # Normalize derived metadata from persisted files. This prevents stale
        # resolved call targets or called_by entries from being trusted after load.
        idx._rebuild_lookups()

        return idx

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    def update_file(self, path: str) -> None:
        """Re-parse a single file and update the index.

        Args:
            path: Relative path to the file within the project root.
        """
        self._reparse_file(path)
        self._rebuild_lookups()

    def _reparse_file(self, path: str) -> None:
        """Re-parse a single file without rebuilding cross-file lookups.

        Use this when updating multiple files in a batch — call _rebuild_lookups()
        once after all reparsing is done rather than once per file.

        Args:
            path: Relative path to the file within the project root.
        """
        try:
            entry = self._parse_file(self.project_root, path)
            self.files[path] = entry
        except Exception as exc:  # noqa: BLE001
            self.files[path] = FileEntry(
                relative_path=path,
                extraction_mode="error",
                parse_error=True,
                parse_error_detail=str(exc),
            )

    def update_files(self, paths: list[str]) -> None:
        """Re-parse multiple files and rebuild lookups once.

        More efficient than calling update_file() N times: all files are
        reparsed first, then _rebuild_lookups() runs exactly once instead
        of once per file.

        Args:
            paths: Relative paths to the files within the project root.
        """
        for path in paths:
            self._reparse_file(path)
        if paths:
            self._rebuild_lookups()

    def remove_file(self, path: str) -> None:
        """Remove a file from the index and rebuild cross-file lookups."""
        if path in self.files:
            del self.files[path]
            self._rebuild_lookups()

    def save_to_disk(self, output_path: str | None = None) -> None:
        """Persist the index to .abstract-index.json.

        Args:
            output_path: Path to write the JSON file. Defaults to
                <project_root>/.abstract-index.json.
        """
        if output_path is None:
            output_path = self._index_path or os.path.join(self.project_root, _INDEX_FILENAME)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_root": self.project_root,
            "indexed_at": self.indexed_at,
            "languages": self.languages,
            "files": {rel: entry.to_dict() for rel, entry in self.files.items()},
            "type_lookup": {
                name: te.to_dict() for name, te in self.type_lookup.items()
            },
            # function_lookup is rebuilt from files on load — no need to serialize
        }
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    def get_tier1(self, path: str) -> str:
        """Return the pre-rendered Tier 1 text for a file.

        Args:
            path: Relative path of the file within the project.
        """
        entry = self.files.get(path)
        if entry is None:
            return f"[ERROR] File not found in index: {path}"
        return entry.tier1_text

    def get_tier2(
        self,
        path: str,
        function_name: str,
        class_name: str | None = None,
    ) -> str:
        """Return the pre-rendered Tier 2 text for a function.

        Args:
            path: Relative path of the file.
            function_name: Name of the function or method.
            class_name: If a method, the name of its containing class.
        """
        entry = self.files.get(path)
        if entry is None:
            return f"[ERROR] File not found in index: {path}"

        if class_name is not None:
            cls = entry.classes.get(class_name)
            if cls is None:
                return f"[ERROR] Class not found: {class_name} in {path}"
            method = cls.methods.get(function_name)
            if method is None:
                return f"[ERROR] Method not found: {class_name}.{function_name} in {path}"
            return method.tier2_text

        func = entry.functions.get(function_name)
        if func is None:
            # Try searching methods across all classes
            for cls in entry.classes.values():
                method = cls.methods.get(function_name)
                if method is not None:
                    return method.tier2_text
            return f"[ERROR] Function not found: {function_name} in {path}"
        return func.tier2_text

    def get_tier3(
        self,
        path: str,
        function_name: str,
        class_name: str | None = None,
    ) -> str:
        """Return the full source of a function (Tier 3 escape hatch).

        Reads fresh from disk every time — always reflects the current state
        of the file, even if the index is stale.

        Args:
            path: Relative path of the file.
            function_name: Name of the function or method.
            class_name: If a method, the name of its containing class.
        """
        abs_path = os.path.join(self.project_root, path)
        if not os.path.isfile(abs_path):
            return f"[ERROR] File not found on disk: {abs_path}"

        with open(abs_path, "rb") as fh:
            source_bytes = fh.read()

        parser = self._get_parser(path)
        if parser is None:
            return f"[ERROR] No parser for file: {path}"

        result = parser.extract_function_source(source_bytes, function_name, class_name)
        if result is None:
            return f"[ERROR] Function not found: {function_name} in {path}"
        return result

    def get_all_tier1(self) -> str:
        """Return the full project Tier 1 view across all files."""
        from abstract_engine.renderer import render_all_tier1  # noqa: PLC0415
        return render_all_tier1(self.files)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _discover_files(self, root: str) -> list[str]:
        """Walk the directory tree and find all supported source files.

        Returns relative paths (relative to root), skipping:
        - the built-in + caller-supplied ``_exclude_patterns``
        - any path matched by the repo's ``.gitignore`` (if present)
        - hidden top-level dot-directories (``.git``, ``.venv`` etc.)
        - large files that would poison the semantic index
        """
        result: list[str] = []
        gitignore = _load_gitignore_matcher(root)
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded directories in-place.  Match by name, by substring,
            # and by hidden-dot prefix.
            pruned: list[str] = []
            for d in dirnames:
                if d in self._exclude_patterns:
                    continue
                if any(pat in d for pat in self._exclude_patterns):
                    continue
                # Check gitignore for directory relative path
                if gitignore is not None:
                    rel_dir = os.path.relpath(os.path.join(dirpath, d), root)
                    if gitignore(rel_dir, is_dir=True):
                        continue
                pruned.append(d)
            dirnames[:] = pruned

            for fname in filenames:
                if fname in self._exclude_patterns:
                    continue
                if _ext(fname) not in self._enabled_extensions:
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel = os.path.relpath(abs_path, root)
                # Respect .gitignore for files.
                if gitignore is not None and gitignore(rel, is_dir=False):
                    continue
                # Skip files that would pollute the index — huge generated
                # blobs, tokenizer vocab, etc.
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    continue
                if size > _MAX_INDEXABLE_BYTES:
                    continue
                result.append(rel)
        return result

    def _detect_changes(
        self, discovered_files: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        """Compare discovered files against stored index.

        Returns:
            (added, modified, deleted) — each a list of relative paths.
        """
        discovered_set = set(discovered_files)
        indexed_set = set(self.files.keys())

        added = [f for f in discovered_files if f not in indexed_set]
        deleted = [f for f in indexed_set if f not in discovered_set]
        modified: list[str] = []

        for rel in discovered_files:
            if rel in indexed_set:
                abs_path = os.path.join(self.project_root, rel)
                try:
                    stat = os.stat(abs_path)
                    stored = self.files[rel]
                    if stat.st_mtime != stored.last_modified:
                        # mtime changed — verify with hash
                        current_hash = _file_hash(abs_path)
                        if current_hash != stored.content_hash:
                            modified.append(rel)
                        else:
                            # Content unchanged — update mtime only
                            stored.last_modified = stat.st_mtime
                except OSError:
                    modified.append(rel)

        return added, modified, deleted

    def _parse_file(self, project_root: str, relative_path: str) -> FileEntry:
        """Parse a single source file into a FileEntry.

        Dispatch order:
        1. ``generic_extractor`` — tree-sitter based for any supported language
           (Python, JS/TS, Go, Rust, Java, C/C++, Ruby, PHP, Bash, etc.).
           Yields real function and class entries with line ranges.
        2. ``_build_generalized_file_entry`` — regex fallback for anything else
           (markdown, text, config, JSON, YAML, TOML, ...).  Produces tier1 text
           and semantic regions but no structured symbols.
        """
        abs_path = os.path.join(project_root, relative_path)
        with open(abs_path, "rb") as fh:
            source_bytes = fh.read()

        stat = os.stat(abs_path)
        content_hash = hashlib.sha256(source_bytes).hexdigest()

        # Try tree-sitter first.
        try:
            from abstract_engine.generic_extractor import extract_symbols  # noqa: PLC0415
        except ImportError:
            extract_symbols = None  # type: ignore[assignment]

        if extract_symbols is not None:
            result = extract_symbols(
                relative_path,
                source_bytes,
                include_private=self._include_private,
            )
            if result is not None:
                functions, classes, lang_name = result
                return self._build_tree_sitter_file_entry(
                    relative_path=relative_path,
                    source_bytes=source_bytes,
                    last_modified=stat.st_mtime,
                    content_hash=content_hash,
                    functions=functions,
                    classes=classes,
                    language=lang_name,
                )

        # Fall back to generalized text indexing.
        return self._build_generalized_file_entry(
            relative_path=relative_path,
            source_bytes=source_bytes,
            last_modified=stat.st_mtime,
            content_hash=content_hash,
            detail=f"Indexed via generalized protocol for extension {_ext(relative_path)}",
        )

    def _build_tree_sitter_file_entry(
        self,
        *,
        relative_path: str,
        source_bytes: bytes,
        last_modified: float,
        content_hash: str,
        functions: dict[str, FunctionEntry],
        classes: dict[str, ClassEntry],
        language: str,
    ) -> FileEntry:
        """Wrap tree-sitter extraction results into a FileEntry."""
        text = source_bytes.decode("utf-8", errors="replace")
        line_count = text.count("\n") + (1 if text else 0)

        preview_lines: list[str] = []
        preview_lines.append(
            f"# {relative_path}  ({len(functions)} functions, {len(classes)} classes)"
        )
        if functions:
            preview_lines.append("## Functions")
            for name, fn in list(functions.items())[:40]:
                preview_lines.append(f"- {name}  L{fn.start_line}-{fn.end_line}")
        if classes:
            preview_lines.append("## Classes")
            for name, cls in list(classes.items())[:40]:
                preview_lines.append(f"- {name}  L{cls.start_line}-{cls.end_line}")
        tier1_text = "\n".join(preview_lines)
        if len(tier1_text) > 4000:
            tier1_text = tier1_text[:4000] + "\n... (truncated)"

        # Only build semantic regions for modest-sized files — large ones can
        # swamp the embedder.
        if len(source_bytes) <= _LARGE_FILE_BYTES and line_count <= _LARGE_FILE_LINES:
            semantic_regions = self._build_semantic_regions(relative_path, text)
        else:
            semantic_regions = []

        return FileEntry(
            relative_path=relative_path,
            language=language,
            extraction_mode="tree-sitter",
            line_count=line_count,
            last_modified=last_modified,
            content_hash=content_hash,
            classes=classes,
            functions=functions,
            tier1_text=tier1_text,
            semantic_regions=semantic_regions,
            parse_error=False,
            parse_error_detail=None,
        )

    def _build_generalized_file_entry(
        self,
        relative_path: str,
        source_bytes: bytes,
        last_modified: float,
        content_hash: str,
        detail: str,
    ) -> FileEntry:
        """Create a normalized file entry using the generalized extraction path."""

        text = source_bytes.decode("utf-8", errors="replace")
        line_count = text.count("\n") + (1 if text else 0)
        preview_lines = [line.rstrip() for line in text.splitlines()[:80]]
        preview = "\n".join(line for line in preview_lines if line.strip())
        if len(preview) > 4000:
            preview = preview[:4000] + "\n... (truncated)"

        functions, classes = self._extract_fallback_symbols(relative_path, text)
        # Suppress semantic region chunking for large files — the regions
        # themselves are capped by ``_build_semantic_regions`` but an oversized
        # file can still yield hundreds of chunks each of which is individually
        # embedded.
        if len(source_bytes) <= _LARGE_FILE_BYTES and line_count <= _LARGE_FILE_LINES:
            semantic_regions = self._build_semantic_regions(relative_path, text)
        else:
            semantic_regions = []

        return FileEntry(
            relative_path=relative_path,
            language=self._guess_language(relative_path),
            extraction_mode="generalized",
            line_count=line_count,
            last_modified=last_modified,
            content_hash=content_hash,
            classes=classes,
            functions=functions,
            tier1_text=preview,
            semantic_regions=semantic_regions,
            parse_error=False,
            parse_error_detail=detail,
        )

    def _rebuild_lookups(self) -> None:
        """Rebuild function_lookup, type_lookup, and language list from current files.

        Also runs the call-graph resolution pass and updates tier text.
        """
        from abstract_engine.call_graph import (  # noqa: PLC0415
            build_function_lookup,
            resolve_call_graph,
        )

        self.function_lookup = build_function_lookup(self.files)
        resolve_call_graph(self.files, self.function_lookup)

        # Rebuild type_lookup from FileEntry.types (populated by TreeSitterParser)
        self.type_lookup = {}
        for file_entry in self.files.values():
            for type_name, type_entry in file_entry.types.items():
                self.type_lookup[type_name] = type_entry

        # Determine languages present
        lang_set: set[str] = set()
        for entry in self.files.values():
            if entry.language:
                lang_set.add(entry.language)
        self.languages = sorted(lang_set)

    @staticmethod
    def _extensions_for_languages(languages: list[str] | None) -> frozenset[str]:
        if not languages:
            return all_extensions() | _GENERIC_CODE_EXTENSIONS

        enabled: set[str] = set()
        for language in languages:
            key = language.strip().lower()
            if not key:
                continue
            config = config_for_language(key)
            if config is not None:
                enabled.update(config.extensions)
            else:
                enabled.update(_GENERIC_CODE_EXTENSIONS)
        return frozenset(enabled)

    @staticmethod
    def _guess_language(path: str) -> str:
        ext = _ext(path)
        language_map = {
            ".py": "python",
            ".pyi": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".mts": "typescript",
            ".cts": "typescript",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".scala": "scala",
            ".swift": "swift",
            ".c": "c",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".h": "c-family",
            ".hpp": "cpp",
            ".cs": "csharp",
            ".php": "php",
            ".rb": "ruby",
            ".lua": "lua",
            ".sh": "shell",
            ".bash": "shell",
            ".zsh": "shell",
            ".md": "markdown",
            ".mdx": "markdown",
            ".rst": "restructuredtext",
            ".txt": "text",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".ini": "ini",
            ".cfg": "config",
            ".conf": "config",
            ".env": "config",
            ".xml": "xml",
            ".html": "html",
            ".css": "css",
            ".sql": "sql",
            ".graphql": "graphql",
            ".proto": "protobuf",
        }
        return language_map.get(ext, ext.lstrip(".") or "unknown")

    @staticmethod
    def _extract_fallback_symbols(
        relative_path: str,
        text: str,
    ) -> tuple[dict[str, FunctionEntry], dict[str, ClassEntry]]:
        """Heuristically extract common symbol names across arbitrary languages."""

        function_patterns = (
            r"^(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?\(",
            r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?[_A-Za-z][A-Za-z0-9_<>,\s:*&-]*=>",
            r"^(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]+\"\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)",
        )
        class_patterns = (
            r"^(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:pub\s+)?enum\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:pub\s+)?trait\s+([A-Za-z_][A-Za-z0-9_]*)",
            r"^(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)",
        )

        functions: dict[str, FunctionEntry] = {}
        classes: dict[str, ClassEntry] = {}

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith(("//", "#", "/*", "*")):
                continue

            for pattern in class_patterns:
                match = re.match(pattern, line)
                if match:
                    name = match.group(1)
                    classes.setdefault(
                        name,
                        ClassEntry(
                            name=name,
                            file_path=relative_path,
                            start_line=lineno,
                            end_line=lineno,
                        ),
                    )
                    break

            for pattern in function_patterns:
                match = re.match(pattern, line)
                if match:
                    name = match.group(1)
                    functions.setdefault(
                        name,
                        FunctionEntry(
                            name=name,
                            qualified_name=name,
                            file_path=relative_path,
                            start_line=lineno,
                            end_line=lineno,
                        ),
                    )
                    break

        return functions, classes

    @staticmethod
    def _build_semantic_regions(
        relative_path: str,
        text: str,
        *,
        max_lines: int = 40,
        max_chars: int = 1800,
        max_regions: int = 200,
    ) -> list[SemanticRegionEntry]:
        """Create normalized semantic chunks for arbitrary code or text files.

        At most ``max_regions`` chunks are produced per file.  This is a safety
        valve: a pathologically large or repetitive file (e.g. a generated
        blob) could otherwise spawn thousands of regions and swamp the
        embedding queue.
        """

        lines = text.splitlines()
        if not lines:
            return []

        regions: list[SemanticRegionEntry] = []
        current: list[tuple[int, str]] = []
        file_is_text = _ext(relative_path) in {
            ".md", ".mdx", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml",
            ".ini", ".cfg", ".conf", ".env", ".xml", ".html", ".css",
        }

        def flush() -> None:
            if not current:
                return
            chunk_lines = [line.rstrip() for _, line in current]
            chunk_text = "\n".join(line for line in chunk_lines if line.strip()).strip()
            if not chunk_text:
                current.clear()
                return
            start_line = current[0][0]
            end_line = current[-1][0]
            title = AbstractIndex._region_title(relative_path, chunk_lines)
            kind = "doc" if file_is_text or AbstractIndex._looks_like_text_chunk(chunk_lines) else "code"
            regions.append(
                SemanticRegionEntry(
                    kind=kind,
                    title=title,
                    text=chunk_text[:max_chars],
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            current.clear()

        for lineno, raw_line in enumerate(lines, start=1):
            if len(regions) >= max_regions:
                break
            line = raw_line.rstrip()
            line_chars = sum(len(existing) for _, existing in current) + len(line)
            boundary = (
                bool(current)
                and (
                    len(current) >= max_lines
                    or line_chars >= max_chars
                    or AbstractIndex._is_region_boundary(line)
                )
            )
            if boundary:
                flush()
            if line.strip() or current:
                current.append((lineno, line))
        if len(regions) < max_regions:
            flush()
        return regions

    @staticmethod
    def _is_region_boundary(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return True
        boundary_patterns = (
            r"^(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+\w+",
            r"^(?:export\s+)?(?:async\s+)?function\s+\w+",
            r"^(?:export\s+)?class\s+\w+",
            r"^(?:pub\s+)?(?:struct|enum|trait)\s+\w+",
            r"^(?:def|class)\s+\w+",
            r"^(?:interface|type)\s+\w+",
            r"^#{1,6}\s+\S+",
            r"^\[.+\]$",
            r"^\{?$",
        )
        return any(re.match(pattern, stripped) for pattern in boundary_patterns)

    @staticmethod
    def _looks_like_text_chunk(lines: list[str]) -> bool:
        non_empty = [line.strip() for line in lines if line.strip()]
        if not non_empty:
            return False
        prose_lines = sum(
            1
            for line in non_empty
            if " " in line and not re.search(r"[{}();=<>\[\]]", line)
        )
        return prose_lines >= max(2, len(non_empty) // 2)

    @staticmethod
    def _region_title(relative_path: str, chunk_lines: list[str]) -> str:
        for line in chunk_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()[:120]
            if len(stripped) <= 120:
                return stripped
            return stripped[:117] + "..."
        return relative_path
