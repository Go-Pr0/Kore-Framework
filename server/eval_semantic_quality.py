"""Semantic search quality evaluator — jina-v4 + jina-reranker-v3 stack.

1. Concept queries (68 hand-crafted, spans all subsystems):
   Natural-language queries → expected function. Tests semantic reasoning.

2. Docstring retrieval (CodeSearchNet-style, all ~630 pairs):
   Docstring as query → find the function it describes. Tests paraphrase recall.

Metrics: Recall@1, Recall@3, Recall@5, MRR.

Usage:
    python eval_semantic_quality.py
    python eval_semantic_quality.py --concept-only
    python eval_semantic_quality.py --docstring-only
    python eval_semantic_quality.py --verbose
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "src"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ------------------------------------------------------------------
# Hand-crafted concept queries (68, spanning all 4 subsystems)
# Each entry: (query, list of acceptable symbol names or path fragments)
# Hit if any acceptable string appears as substring of 'path::symbol' (case-insensitive).
# ------------------------------------------------------------------
CONCEPT_QUERIES: list[tuple[str, list[str]]] = [

    # ── abstract_engine/index ────────────────────────────────────────
    ("load or build an index from disk",
        ["load_or_build"]),
    ("detect which source files have changed since last index build",
        ["_detect_changes", "detect_changes"]),
    ("compute a hash of a file's contents",
        ["_file_hash", "file_hash"]),
    ("save the current index state to disk",
        ["save_to_disk"]),
    ("rebuild symbol lookup tables after files are added or removed",
        ["_rebuild_lookups", "rebuild_lookups"]),
    ("determine which parser to use for a given file extension",
        ["_get_parser", "get_parser"]),
    ("list all file extensions handled by a set of languages",
        ["_extensions_for_languages", "extensions_for_languages"]),
    ("reparse a single file and update its entry in the index",
        ["_reparse_file", "reparse_file"]),

    # ── abstract_engine/parsers ──────────────────────────────────────
    ("parse Python source into function entries",
        ["python_parser", "parse_python", "PythonParser", "PythonFileParser"]),
    ("parse a TypeScript source file",
        ["typescript_parser", "parse_typescript", "TypeScriptParser", "TypeScriptFileParser"]),
    ("extract a docstring from a Python function AST node",
        ["_extract_docstring", "extract_docstring"]),
    ("extract the parameters of a function from its AST node",
        ["_extract_parameters", "extract_parameters"]),
    ("extract all function calls made inside a method body",
        ["_extract_calls", "extract_calls"]),
    ("find what exceptions a function can raise",
        ["_extract_raises", "extract_raises"]),
    ("collect all instance attributes set in __init__",
        ["_extract_instance_attrs", "extract_instance_attrs"]),
    ("strip decorator wrappers to reach the underlying function node",
        ["_unwrap_to_function", "unwrap_to_function"]),
    ("determine whether a function definition is async",
        ["_is_async_function", "is_async_function"]),
    ("find the class node that encloses a given function",
        ["_find_enclosing_class", "find_enclosing_class"]),
    ("check whether a source file has syntax errors",
        ["_check_parse_errors", "check_parse_errors"]),

    # ── abstract_engine/renderer ─────────────────────────────────────
    ("render a compact one-line function signature",
        ["render_tier1", "render_tier1_function"]),

    # ── abstract_engine/call_graph ───────────────────────────────────
    ("resolve the call graph relationships between functions",
        ["resolve_call_graph"]),
    ("build a map from imported names to their source files",
        ["_build_import_file_map", "_build_imported_name_alias_map"]),
    ("find a function entry by name across all indexed files",
        ["_find_func_entry_in_files", "find_func_entry"]),
    ("follow an attribute access chain to determine the type",
        ["_resolve_typed_attribute", "resolve_typed_attribute"]),

    # ── abstract_fs_server/semantic_index ────────────────────────────
    ("embed text into a dense vector",
        ["_embed_passages", "_embed_query", "embed_texts"]),
    ("merge two ranked result lists using reciprocal rank fusion",
        ["_rrf_merge", "rrf_merge"]),
    ("cross-encoder reranking of search candidates",
        ["_rerank", "rerank"]),
    ("build or rebuild the full vector index from the AbstractIndex",
        ["build_from_index"]),
    ("add new vector rows for a single changed source file",
        ["update_file", "SemanticIndex.update_file"]),
    ("remove all index rows belonging to a deleted file",
        ["remove_file", "SemanticIndex.remove_file"]),
    ("open or create the LanceDB table with the correct schema",
        ["_open_or_create_table", "open_or_create_table"]),
    ("create a full-text search index on the indexed text",
        ["create_fts_index", "build_from_index"]),
    ("hybrid semantic search over functions given a query string",
        ["search", "async_search", "SemanticIndex.search"]),

    # ── abstract_fs_server/file_watcher ──────────────────────────────
    ("watch a directory for file creation, modification, and deletion",
        ["FileWatcher", "file_watcher", "watch"]),

    # ── abstract_fs_server/lock_manager ──────────────────────────────
    ("acquire an exclusive write lock before modifying a file",
        ["LockManager", "lock_manager", "acquire"]),

    # ── abstract_fs_server/adapter ───────────────────────────────────
    ("generate a tier-1 summary view of all functions in a directory",
        ["view_generator", "get_all_tier1", "get_tier1"]),
    ("run a semantic search query through the adapter layer",
        ["search_engine", "are_adapter", "SemanticIndex"]),
    ("trace the full call chain for a named function",
        ["tracer", "function_trace", "resolve_call_graph"]),

    # ── write_pipeline/extraction ────────────────────────────────────
    ("discover all imports and dependencies a file relies on",
        ["dependency_discovery", "DependencyDiscovery"]),
    ("find the AST node corresponding to a named function",
        ["node_finder", "NodeFinder"]),
    ("build the code context needed before generating a write",
        ["context_builder", "ContextBuilder"]),

    # ── write_pipeline/application ───────────────────────────────────
    ("apply a batch of code edits to files on disk",
        ["batch_applier", "BatchApplier"]),
    ("manage import statements when inserting new symbols",
        ["import_manager", "ImportManager"]),
    ("rewrite code to match Claude's output using AST manipulation",
        ["code_utils_claude", "code_utils"]),

    # ── write_pipeline/execution ─────────────────────────────────────
    ("execute a code generation request via the Gemini API",
        ["gemini_executor", "GeminiExecutor"]),
    ("validate that generated code parses without syntax errors",
        ["validator", "Validator"]),
    ("count tokens and track cost from a Gemini API response",
        ["gemini_stats", "GeminiStats"]),

    # ── ai_control_panel/session manager ─────────────────────────────
    ("cancel a running agent session",
        ["cancel_session", "CancelSession"]),
    ("queue a new prompt for an agent to execute",
        ["queue_prompt", "QueuePrompt", "add_to_queue"]),
    ("retrieve the full transcript of a completed session",
        ["get_session_transcript", "session_transcript"]),
    ("check the current status of active sessions",
        ["check_sessions", "session_status"]),

    # ── ai_control_panel/workflow ─────────────────────────────────────
    ("find the next pending step to execute in a workflow run",
        ["_find_next_pending_step", "find_next_pending_step"]),
    ("determine whether the supervisor agent is idle",
        ["_is_supervisor_idle", "is_supervisor_idle"]),
    ("save a workflow definition to a JSON file",
        ["save_to_file", "WorkflowExecutor.save"]),
    ("load a workflow definition from a JSON file",
        ["load_from_file", "WorkflowExecutor.load"]),
    ("execute a single step within a workflow run",
        ["_execute_step", "execute_step"]),

    # ── ai_control_panel/widgets ──────────────────────────────────────
    ("format cost and session statistics for the status bar",
        ["_format_stats", "StatusBar"]),
    ("cycle between available agent target sessions in the UI",
        ["cycle_target", "PromptInput"]),
    ("refresh the queue panel to show latest pending prompts",
        ["refresh_queue", "QueuePanel"]),
    ("append a new message to the session panel display",
        ["append_message", "SessionPanel"]),

    # ── chat_tester ───────────────────────────────────────────────────
    ("calculate total API cost from input and output token counts",
        ["calc_cost"]),
    ("stream token output from a Claude CLI subprocess",
        ["stream_turn", "build_turn_cmd"]),
    ("configure MCP server settings for a project directory",
        ["configure_mcp_for_project"]),
    ("assemble the full system prompt from a profile configuration",
        ["assemble_prompt", "load_profile_config"]),
    ("print formatted tool usage and turn statistics",
        ["print_turn_stats"]),
    ("check whether required CLI tools and dependencies are installed",
        ["check_prerequisites", "show_prerequisites"]),
    ("save and restore application state across sessions",
        ["AppState.save", "AppState.load"]),
    ("start an interactive MCP console for manual tool testing",
        ["run_console", "_console_loop"]),
]


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------

class QueryResult(NamedTuple):
    query: str
    expected_id: str
    rank: int | None
    results_count: int


def reciprocal_rank(rank: int | None) -> float:
    if rank is None:
        return 0.0
    return 1.0 / rank


def recall_at_k(results: list[QueryResult], k: int) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.rank is not None and r.rank <= k) / len(results)


def mrr(results: list[QueryResult]) -> float:
    if not results:
        return 0.0
    return sum(reciprocal_rank(r.rank) for r in results) / len(results)


# ------------------------------------------------------------------
# Search output parser
# ------------------------------------------------------------------

def parse_search_output(raw: str) -> list[str]:
    """Parse compact semantic/keyword search output into ``path::symbol`` ids.

    New format: ``{path}[:{start}[-{end}]] {record_type} {signature}``
    where ``signature`` is e.g. ``backup_dir_md_files()->None`` or
    ``MyClass.do_thing(x: int)->bool``.
    """
    ids: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("..."):
            # Skip error tags like "[Semantic search: no results]" and the
            # "...and N more" truncation notice.
            continue
        try:
            # First whitespace-delimited token is "path" or "path:range".
            location, _, remainder = line.partition(" ")
            path = location.split(":", 1)[0]
            # remainder is "{record_type} {signature}". Drop record_type.
            _, _, signature = remainder.partition(" ")
            # Signature may be "Cls.name(...)..." or "name(...)...".
            head = signature.split("(", 1)[0]
            # Strip async prefix if present.
            if head.startswith("async "):
                head = head[6:]
            symbol = head.strip().split(" ")[-1]
            ids.append(f"{path}::{symbol}")
        except (IndexError, ValueError):
            ids.append(line)
    return ids


def find_rank(expected: str, ids: list[str]) -> int | None:
    for i, hit_id in enumerate(ids, start=1):
        if expected.lower() in hit_id.lower():
            return i
    return None


# ------------------------------------------------------------------
# Docstring evaluation
# ------------------------------------------------------------------

def collect_docstring_pairs(abstract_index, min_len: int = 20) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for rel_path, file_entry in abstract_index.files.items():
        for func_name, func in file_entry.functions.items():
            ds = func.docstring_full or func.docstring_first_line or ""
            ds = ds.strip().strip("\"'").strip()
            if len(ds) >= min_len:
                pairs.append((ds, f"{rel_path}::{func_name}"))
        for cls in file_entry.classes.values():
            for method_name, method in cls.methods.items():
                ds = method.docstring_full or method.docstring_first_line or ""
                ds = ds.strip().strip("\"'").strip()
                if len(ds) >= min_len:
                    pairs.append((ds, f"{rel_path}::{cls.name}.{method_name}"))
    return pairs


def run_docstring_eval(semantic_index, pairs: list[tuple[str, str]], k: int,
                       verbose: bool = False) -> list[QueryResult]:
    results: list[QueryResult] = []
    total = len(pairs)
    for i, (docstring, expected_id) in enumerate(pairs, start=1):
        if i % 50 == 0:
            print(f"  [{i}/{total}] ...", flush=True)
        query = docstring[:500]
        raw = semantic_index.search(query, k=k)
        hit_ids = parse_search_output(raw)
        rank = find_rank(expected_id.split("::")[-1], hit_ids)
        r = QueryResult(query=query[:80], expected_id=expected_id, rank=rank, results_count=len(hit_ids))
        results.append(r)
        if verbose and rank is None:
            print(f"  MISS  {expected_id}")
        elif verbose and rank == 1:
            print(f"  HIT@1 {expected_id}")
    return results


# ------------------------------------------------------------------
# Concept query evaluation
# ------------------------------------------------------------------

def run_concept_eval(semantic_index, queries: list[tuple[str, list[str]]],
                     k: int) -> list[QueryResult]:
    results: list[QueryResult] = []
    for query, acceptable in queries:
        raw = semantic_index.search(query, k=k)
        hit_ids = parse_search_output(raw)
        rank: int | None = None
        for i, hit_id in enumerate(hit_ids, start=1):
            for acc in acceptable:
                if acc.lower() in hit_id.lower():
                    rank = i
                    break
            if rank is not None:
                break
        results.append(QueryResult(
            query=query,
            expected_id=" | ".join(acceptable),
            rank=rank,
            results_count=len(hit_ids),
        ))
    return results


# ------------------------------------------------------------------
# Reporting
# ------------------------------------------------------------------

def print_metrics(label: str, results: list[QueryResult], k: int) -> None:
    r1 = recall_at_k(results, 1)
    r3 = recall_at_k(results, 3)
    rk = recall_at_k(results, k)
    m = mrr(results)
    misses = [r for r in results if r.rank is None]
    hits = [r for r in results if r.rank is not None]

    print(f"\n{'='*60}")
    print(f"  {label}  (n={len(results)})")
    print(f"{'='*60}")
    print(f"  Recall@1 : {r1:.3f}  ({sum(1 for r in results if r.rank == 1)}/{len(results)})")
    print(f"  Recall@3 : {r3:.3f}  ({sum(1 for r in results if r.rank is not None and r.rank <= 3)}/{len(results)})")
    print(f"  Recall@{k} : {rk:.3f}  ({len(hits)}/{len(results)})")
    print(f"  MRR      : {m:.3f}")

    if misses:
        print(f"\n  Misses ({len(misses)}):")
        for r in misses[:15]:
            q = r.query[:55] + "..." if len(r.query) > 55 else r.query
            print(f"    - {r.expected_id:<40}  query: {q}")
        if len(misses) > 15:
            print(f"    ... and {len(misses) - 15} more")


def print_concept_detail(results: list[QueryResult]) -> None:
    print(f"\n  Per-query results:")
    for r in results:
        status = f"@{r.rank}" if r.rank else "MISS"
        q = r.query[:52] + "..." if len(r.query) > 52 else r.query
        print(f"  [{status:6s}] {q:<55}  → {r.expected_id}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate jina-v4 + jina-reranker-v3 retrieval quality")
    parser.add_argument("--target", default=str(REPO_ROOT / "src"))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-docstring-len", type=int, default=20)
    parser.add_argument("--docstring-only", action="store_true")
    parser.add_argument("--concept-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--cache-dir", default=None,
                        help="Persist LanceDB index to skip rebuild on re-runs")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    print(f"Target : {target}")
    print(f"k={args.k}  Stack: jinaai/jina-embeddings-v4 + jinaai/jina-reranker-v3")

    print("\n[1/3] Building AbstractIndex...")
    t0 = time.perf_counter()
    from abstract_engine.index import AbstractIndex
    abstract_index = AbstractIndex.load_or_build(target)
    print(f"      {len(abstract_index.files)} files in {time.perf_counter() - t0:.1f}s")
    if not abstract_index.files:
        print("ERROR: No files found."); sys.exit(1)

    print("\n[2/3] Building SemanticIndex (jina-v4)...")
    t0 = time.perf_counter()
    from abstract_fs_server.semantic_index import SemanticIndex

    import contextlib

    @contextlib.contextmanager
    def _index_dir():
        if args.cache_dir:
            p = Path(args.cache_dir)
            p.mkdir(parents=True, exist_ok=True)
            yield str(p)
        else:
            with tempfile.TemporaryDirectory(prefix="sem_eval_") as d:
                yield d

    with _index_dir() as lancedb_path:
        sem = SemanticIndex(lancedb_path)

        already_cached = False
        if args.cache_dir and not args.rebuild:
            sem._ensure_ready()
            if sem._is_available:
                try:
                    n = sem._table.count_rows()
                    if n > 0:
                        already_cached = True
                        print(f"      {n} symbols loaded from cache ({lancedb_path})")
                except Exception:
                    pass

        if not already_cached:
            sem.build_from_index(abstract_index)
            try:
                n = sem._table.count_rows()
            except Exception:
                n = "?"
            print(f"      {n} symbols embedded in {time.perf_counter() - t0:.1f}s")

        if not sem._is_available:
            print("ERROR: SemanticIndex unavailable — check deps (lancedb, transformers).")
            sys.exit(1)

        print("\n[3/3] Running evaluations...")

        if not args.docstring_only:
            print("\n  Running concept queries...")
            concept_results = run_concept_eval(sem, CONCEPT_QUERIES, args.k)
            print_metrics("Concept Queries", concept_results, args.k)
            print_concept_detail(concept_results)

        if not args.concept_only:
            pairs = collect_docstring_pairs(abstract_index, min_len=args.min_docstring_len)
            print(f"\n  Docstring pairs: {len(pairs)} (using all)")
            print(f"  Running docstring retrieval eval...")
            t0 = time.perf_counter()
            doc_results = run_docstring_eval(sem, pairs, args.k, verbose=args.verbose)
            elapsed = time.perf_counter() - t0
            print(f"  Done in {elapsed:.1f}s ({elapsed/len(pairs)*1000:.0f}ms/query)")
            print_metrics("Docstring Retrieval (CodeSearchNet-style)", doc_results, args.k)


if __name__ == "__main__":
    main()
