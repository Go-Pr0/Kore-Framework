"""Search-side MCP tools for the semantic-only server.

These are thin wrappers that delegate to are_adapter methods.  Register them
via register_search_tools(mcp, get_registry, get_base_config).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from abstract_fs_server.adapter import are_adapter

if TYPE_CHECKING:
    from abstract_fs_server.registry import RepoBundle, RepoRegistry
    from abstract_fs_server.config import ServerConfig

logger = logging.getLogger(__name__)


async def _flush_bundle_watcher(bundle: RepoBundle) -> None:
    """Await any pending file-watcher index updates (best-effort, 1s cap)."""
    if bundle.watcher is not None:
        await bundle.watcher.flush(timeout=1.0)


async def _resolve_bundle(
    get_registry: Callable[[], RepoRegistry | None],
    repo_path: str | None,
) -> tuple[RepoBundle | None, str | None]:
    """Resolve the RepoBundle for *repo_path*, applying the stdio fallback.

    Returns ``(bundle, None)`` on success or ``(None, error_message)`` on
    failure so callers can do a single check.
    """
    registry = get_registry()
    if registry is None:
        return None, "Error: Registry not initialised."

    if repo_path is None:
        default = registry.default_repo()
        if default is None:
            return None, (
                "Error: repo_path is required (no default repo registered). "
                "Pass repo_path as the absolute path to the target repository."
            )
        repo_path = default

    try:
        bundle = await registry.get(repo_path)
    except ValueError as exc:
        return None, f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"Error building index for {repo_path!r}: {exc}"

    return bundle, None


def register_search_tools(
    mcp,  # FastMCP instance
    get_registry: Callable[[], RepoRegistry | None],
    get_base_config: Callable[[], ServerConfig | None],
) -> None:
    """Register search-side tools with the FastMCP instance.

    Args:
        mcp: The FastMCP application instance.
        get_registry: Callable returning the current RepoRegistry (or None).
        get_base_config: Callable returning the base ServerConfig (or None).
    """

    @mcp.tool()
    async def file_find(
        pattern: str,
        include_tier1: bool = False,
        repo_path: str | None = None,
    ) -> str:
        """Find files matching a glob pattern and return abstract metadata for each match.

        Use this instead of GlobTool — it returns the file path, function count,
        line count, and one-line module description for each match, giving you
        instant structural insight without reading individual files.

        Pass ``repo_path`` as the absolute path to the target repo when the
        server is shared across multiple repos.  In stdio mode with a single
        default repo configured, ``repo_path`` may be omitted.
        """
        try:
            bundle, err = await _resolve_bundle(get_registry, repo_path)
            if err:
                return err
            await _flush_bundle_watcher(bundle)
            logger.info(
                "file_find: pattern=%s include_tier1=%s repo_path=%s",
                pattern,
                include_tier1,
                bundle.repo_root,
            )
            return are_adapter.glob_files(bundle.index, pattern, include_tier1, bundle.config)
        except Exception as exc:  # noqa: BLE001
            logger.error("file_find failed: %s", exc, exc_info=True)
            return (
                f"Error in file_find: {type(exc).__name__}: {exc}. "
                "Check server logs for details."
            )

    @mcp.tool()
    async def find_code(
        pattern: str,
        case_sensitive: bool = False,
        search_in: str = "all",
        repo_path: str | None = None,
    ) -> str:
        """Search for a pattern in function names, signatures, and one-liner descriptions.

        Use this instead of GrepTool when you want to find functions by what they
        do or how they are typed, not by grep-ping source code.  Searches the
        abstract index — much cheaper than reading files.

        search_in options: 'all', 'names', 'signatures', 'descriptions', 'types'.

        Pass ``repo_path`` as the absolute path to the target repo when the
        server is shared across multiple repos.  In stdio mode with a single
        default repo configured, ``repo_path`` may be omitted.
        """
        try:
            bundle, err = await _resolve_bundle(get_registry, repo_path)
            if err:
                return err
            await _flush_bundle_watcher(bundle)
            logger.info(
                "find_code: pattern=%s case_sensitive=%s search_in=%s repo_path=%s",
                pattern,
                case_sensitive,
                search_in,
                bundle.repo_root,
            )
            return are_adapter.grep_index(bundle.index, pattern, case_sensitive, search_in)
        except Exception as exc:  # noqa: BLE001
            logger.error("find_code failed: %s", exc, exc_info=True)
            return (
                f"Error in find_code: {type(exc).__name__}: {exc}. "
                "Check server logs for details."
            )

    @mcp.tool()
    async def type_shape(
        type_name: str,
        include_methods: bool = True,
        repo_path: str | None = None,
    ) -> str:
        """Look up a type, class, interface, or enum definition and return its shape.

        Returns fields, methods, and structure.  Use this when you need to
        understand the shape of a data type before writing a spec that uses it.
        Faster and cheaper than reading the full file.

        Pass ``repo_path`` as the absolute path to the target repo when the
        server is shared across multiple repos.  In stdio mode with a single
        default repo configured, ``repo_path`` may be omitted.
        """
        try:
            bundle, err = await _resolve_bundle(get_registry, repo_path)
            if err:
                return err
            await _flush_bundle_watcher(bundle)
            logger.info(
                "type_shape: type_name=%s include_methods=%s repo_path=%s",
                type_name,
                include_methods,
                bundle.repo_root,
            )
            return are_adapter.get_type_shape_text(bundle.index, type_name, include_methods)
        except Exception as exc:  # noqa: BLE001
            logger.error("type_shape failed: %s", exc, exc_info=True)
            return (
                f"Error in type_shape: {type(exc).__name__}: {exc}. "
                "Check server logs for details."
            )

    @mcp.tool()
    async def search_codebase(
        query: str,
        mode: str,
        target_dir: str = ".",
        max_results: int = 15,
        repo_path: str | None = None,
    ) -> str:
        """Search the codebase.  Always specify mode explicitly — there is no default.

        mode="keyword"
            Regex/substring search over the abstract index: function names,
            signatures, and one-liner descriptions.  Fast, structured.
            Use when you know the name or rough signature of what you want.
            Examples: "parse_candle", "async.*order", "price.*float"

        mode="semantic"
            Searches by meaning across all indexed function signatures and docstrings.
            Use for natural-language queries when you don't know the exact name.
            Examples: "functions that validate user credentials",
                      "code that handles order cancellation"

        mode="raw"
            ripgrep over raw file contents.  Finds literal strings, constants,
            log messages, config keys — anything not in the abstract index.
            Use when looking for a specific string in source code, not a function.
            Examples: "CLOB_API_URL", "order already filled", "TODO"

        Args:
            query: Search string, regex, or natural-language description.
            mode: Required.  One of "keyword", "semantic", "raw".
            target_dir: Scope for mode="raw" only (relative to repo root).
                        Ignored by "keyword" and "semantic" which always cover
                        the full index.
            max_results: Maximum result lines to return (default 15). Keep low for
                         focused queries; only raise it when you genuinely need a
                         wide sweep.
            repo_path: Absolute path to the repository root.  Required unless
                       the server is running in stdio mode with a default repo
                       configured.  Pass ``repo_path`` as the absolute path to
                       the target repo when the server is shared across multiple
                       repos.
        """
        import re  # noqa: PLC0415

        try:
            bundle, err = await _resolve_bundle(get_registry, repo_path)
            if err:
                return err
            await _flush_bundle_watcher(bundle)

            if mode not in ("keyword", "semantic", "raw"):
                return (
                    f"Invalid mode: {mode!r}. "
                    "Must be one of: \"keyword\", \"semantic\", \"raw\"."
                )

            logger.info(
                "search_codebase: mode=%s query=%s target_dir=%s max_results=%s repo_path=%s",
                mode,
                query,
                target_dir,
                max_results,
                bundle.repo_root,
            )

            # ------------------------------------------------------------------
            # keyword — abstract index grep
            # ------------------------------------------------------------------
            if mode == "keyword":
                raw = are_adapter.grep_index(
                    bundle.index, query, case_sensitive=False, search_in="all"
                )
                if raw.startswith("No matches for pattern"):
                    return f"No keyword matches for: {query}"
                return "\n".join(raw.splitlines()[:max_results])

            # ------------------------------------------------------------------
            # semantic — vector search
            # ------------------------------------------------------------------
            if mode == "semantic":
                sem_index = bundle.semantic
                if sem_index is None:
                    return "Semantic search is not enabled (SEMANTIC_SEARCH_ENABLED=false or not initialized)."
                result = await sem_index.async_search(query, k=max_results)
                if not result or result.startswith("[Semantic search"):
                    return f"No semantic results for: {query}"
                lines = result.splitlines()
                return "\n".join(lines[:max_results])

            # ------------------------------------------------------------------
            # raw — ripgrep on file contents
            # ------------------------------------------------------------------
            if mode == "raw":
                repo_root = Path(bundle.config.repo_root).resolve()

                candidate = (repo_root / target_dir).resolve()
                try:
                    candidate.relative_to(repo_root)
                except ValueError:
                    return (
                        f"Error: target_dir '{target_dir}' resolves outside "
                        f"repo root '{repo_root}'."
                    )
                resolved_target = str(candidate)

                raw_lines: list[str] = []

                try:
                    result = subprocess.run(
                        [
                            "rg",
                            "--no-heading",
                            "-n",
                            "-i",
                            "--max-count=1",
                            query,
                            resolved_target,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if result.stdout:
                        for line in result.stdout.splitlines():
                            try:
                                parts = line.split(":", 2)
                                if len(parts) >= 3:
                                    rel = str(Path(parts[0]).relative_to(repo_root))
                                    line = f"{rel}:{parts[1]}:{parts[2]}"
                            except ValueError:
                                pass
                            raw_lines.append(line)
                except FileNotFoundError:
                    logger.info("search_codebase: rg not found, using Python re fallback")
                    try:
                        compiled = re.compile(query, re.IGNORECASE)
                    except re.error as exc:
                        return f"Invalid regex pattern '{query}': {exc}"

                    target_path = Path(resolved_target)
                    file_iter = (
                        target_path.rglob("*")
                        if target_path.is_dir()
                        else [target_path]
                    )
                    for file_path in sorted(file_iter):
                        if not file_path.is_file():
                            continue
                        try:
                            text = file_path.read_text(errors="replace")
                        except OSError:
                            continue
                        for lineno, file_line in enumerate(text.splitlines(), start=1):
                            if compiled.search(file_line):
                                try:
                                    rel = str(file_path.relative_to(repo_root))
                                except ValueError:
                                    rel = str(file_path)
                                raw_lines.append(f"{rel}:{lineno}:{file_line}")
                                if len(raw_lines) >= max_results:
                                    break
                        if len(raw_lines) >= max_results:
                            break

                if not raw_lines:
                    return f"No raw text matches for: {query}"
                tagged = [f"[Raw] {line}" for line in raw_lines[:max_results]]
                return "\n".join(tagged)

            return f"No results found for: {query}"  # unreachable

        except Exception as exc:  # noqa: BLE001
            logger.error("search_codebase failed: %s", exc, exc_info=True)
            return (
                f"Error in search_codebase: {type(exc).__name__}: {exc}. "
                "Check server logs for details."
            )

    @mcp.tool()
    async def semantic_status(repo_path: str | None = None) -> str:
        """Return server and index status for the target repository.

        Pass ``repo_path`` as the absolute path to the target repo when the
        server is shared across multiple repos.  In stdio mode with a single
        default repo configured, ``repo_path`` may be omitted.
        """
        bundle, err = await _resolve_bundle(get_registry, repo_path)
        if err:
            return err
        await _flush_bundle_watcher(bundle)

        index = bundle.index
        config = bundle.config
        sem_index = bundle.semantic

        file_count = len(index.files)
        function_count = sum(len(file_entry.functions) for file_entry in index.files.values())
        class_count = sum(len(file_entry.classes) for file_entry in index.files.values())
        parse_errors = sum(1 for file_entry in index.files.values() if file_entry.parse_error)
        generalized_files = sum(
            1 for file_entry in index.files.values() if file_entry.extraction_mode == "generalized"
        )
        legacy_parser_files = sum(
            1 for file_entry in index.files.values() if file_entry.extraction_mode == "parser"
        )
        legacy_fallback_files = sum(
            1 for file_entry in index.files.values() if file_entry.extraction_mode == "fallback"
        )
        region_count = sum(len(file_entry.semantic_regions) for file_entry in index.files.values())
        semantic_ready = "enabled" if sem_index is not None else "disabled"
        semantic_state = "disabled"
        semantic_error: str | None = None
        if sem_index is not None:
            semantic_state, semantic_error = sem_index.status_summary()

        lines = [
            f"Repo root: {config.repo_root}",
            f"Cache dir: {config.repo_cache_dir}",
            f"Languages: {', '.join(index.languages) if index.languages else '(not detected yet)'}",
            f"Files indexed: {file_count}",
            f"Generalized files: {generalized_files}",
            f"Legacy parser files: {legacy_parser_files}",
            f"Legacy fallback files: {legacy_fallback_files}",
            f"Functions indexed: {function_count}",
            f"Classes indexed: {class_count}",
            f"Semantic regions: {region_count}",
            f"Files with parse errors: {parse_errors}",
            f"Semantic index: {semantic_ready}",
            f"Semantic state: {semantic_state}",
            f"Semantic model: {config.embedding_model}",
            f"Semantic device: {config.embedding_device}",
            f"Watcher enabled: {config.watch_files}",
        ]
        if semantic_error:
            first_line = semantic_error.strip().splitlines()[-1]
            lines.append(f"Semantic error: {first_line}")
        return "\n".join(lines)
