"""Eval the full jina stack: jina-embeddings-v4 + jina-reranker-v3.

Pins transformers==4.52.4 before loading (required for jina-v4 custom arch),
restores the original version afterward.

Runs the same concept-query + docstring-retrieval eval as eval_semantic_quality.py.
Prints a comparison table alongside the previously measured bge-m3 results.
"""

from __future__ import annotations

import subprocess
import sys

# ── Pin transformers to version compatible with jina-embeddings-v4 ──────────
print("[setup] Pinning transformers==4.52.4 for jina-v4 compatibility...")
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "transformers==4.52.4"],
    check=True,
)
print("[setup] Done.\n")

import argparse
import logging
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT / "src"))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ── Reuse concept queries and helpers from eval_semantic_quality ─────────────
from eval_semantic_quality import (  # noqa: E402
    CONCEPT_QUERIES,
    QueryResult,
    collect_docstring_pairs,
    find_rank,
    mrr,
    print_metrics,
    print_concept_detail,
    print_comparison_table,
    recall_at_k,
)

# ── Previously measured bge-m3 results (from eval run 2026-04-08) ────────────
# Format: (label, {concept_r1, concept_mrr, doc_r1, doc_r3, doc_r5, doc_mrr, n_concept, n_doc})
KNOWN_BGE_RESULTS: list[tuple[str, dict]] = [
    ("bge-m3 + no reranker", {
        "concept_r1": None, "concept_mrr": None,
        "doc_r1": None, "doc_r3": None, "doc_r5": None, "doc_mrr": None,
        "n_concept": 68, "n_doc": 627,
        "_note": "not yet measured",
    }),
    ("bge-m3 + bge-reranker-base", {
        "concept_r1": 0.647, "concept_mrr": 0.749,
        "doc_r1": 0.606, "doc_r3": 0.624, "doc_r5": 0.624, "doc_mrr": 0.615,
        "n_concept": 68, "n_doc": 627,
    }),
    ("bge-m3 + bge-reranker-v2-m3", {
        "concept_r1": None, "concept_mrr": None,
        "doc_r1": None, "doc_r3": None, "doc_r5": None, "doc_mrr": None,
        "n_concept": 68, "n_doc": 627,
        "_note": "not yet measured",
    }),
]


# ── Embedding dimension ───────────────────────────────────────────────────────
# jina-v4 matryoshka: [128, 256, 512, 1024, 2048]. Use 1024 to match bge-m3.
EMBED_DIM = 1024
JINA_EMBED_MODEL = "jinaai/jina-embeddings-v4"
JINA_RERANKER_MODEL = "jinaai/jina-reranker-v3"
LANCEDB_TABLE = "jina_v4_1024"


# ── Jina embedder wrapper ─────────────────────────────────────────────────────

class JinaEmbedder:
    def __init__(self, device: str = "cuda"):
        from transformers import AutoModel
        print(f"  Loading {JINA_EMBED_MODEL} on {device}...")
        t0 = time.perf_counter()
        self._model = AutoModel.from_pretrained(
            JINA_EMBED_MODEL,
            trust_remote_code=True,
        ).to(device)
        print(f"  Loaded in {time.perf_counter() - t0:.1f}s")

    def encode_passages(self, texts: list[str], batch_size: int = 8) -> "np.ndarray":
        import numpy as np
        import torch
        vecs = self._model.encode_text(
            texts,
            task="retrieval",
            prompt_name="passage",
            batch_size=batch_size,
            truncate_dim=EMBED_DIM,
            return_numpy=True,
        )
        if isinstance(vecs, list):
            vecs = np.array([v if isinstance(v, np.ndarray) else v.cpu().numpy() for v in vecs])
        elif not isinstance(vecs, np.ndarray):
            vecs = vecs.cpu().numpy() if hasattr(vecs, "cpu") else np.array(vecs)
        return vecs.astype(np.float32)

    def encode_query(self, query: str) -> "np.ndarray":
        import numpy as np
        vec = self._model.encode_text(
            [query],
            task="retrieval",
            prompt_name="query",
            batch_size=1,
            truncate_dim=EMBED_DIM,
            return_numpy=True,
        )
        if isinstance(vec, list):
            vec = vec[0]
        if not isinstance(vec, np.ndarray):
            vec = vec.cpu().numpy() if hasattr(vec, "cpu") else np.array(vec)
        return vec.astype(np.float32).flatten()


# ── Jina reranker wrapper ─────────────────────────────────────────────────────

class JinaReranker:
    def __init__(self, device: str = "cuda"):
        from transformers import AutoModel
        print(f"  Loading {JINA_RERANKER_MODEL} on {device}...")
        t0 = time.perf_counter()
        self._model = AutoModel.from_pretrained(
            JINA_RERANKER_MODEL,
            trust_remote_code=True,
        )
        # Move to device if possible
        try:
            self._model = self._model.to(device)
        except Exception:
            pass
        print(f"  Loaded in {time.perf_counter() - t0:.1f}s")

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """Rerank candidates using jina-reranker-v3's native API."""
        docs = [row["text"] for row in candidates]
        results = self._model.rerank(query, docs, top_n=top_k)
        # results is a list of dicts: {document, relevance_score, index}
        reranked = sorted(results, key=lambda x: x["relevance_score"], reverse=True)
        return [candidates[r["index"]] for r in reranked[:top_k]]


# ── RRF merge (same as SemanticIndex) ────────────────────────────────────────

def rrf_merge(vec_results: list[dict], fts_results: list[dict], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    id_to_row: dict[str, dict] = {}
    for rank, row in enumerate(vec_results):
        scores[row["id"]] = scores.get(row["id"], 0) + 1.0 / (rank + k)
        id_to_row[row["id"]] = row
    for rank, row in enumerate(fts_results):
        scores[row["id"]] = scores.get(row["id"], 0) + 1.0 / (rank + k)
        id_to_row[row["id"]] = row
    ranked = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)
    return [id_to_row[i] for i in ranked]


# ── Index builder ─────────────────────────────────────────────────────────────

def build_jina_index(abstract_index, lancedb_path: str, embedder: JinaEmbedder,
                     force_rebuild: bool = False) -> "lancedb.Table":
    import lancedb
    import pyarrow as pa

    db = lancedb.connect(lancedb_path)

    schema = pa.schema([
        pa.field("id", pa.string()),
        pa.field("path", pa.string()),
        pa.field("symbol", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), EMBED_DIM)),
    ])

    if LANCEDB_TABLE in db.table_names() and not force_rebuild:
        table = db.open_table(LANCEDB_TABLE)
        n = table.count_rows()
        if n > 0:
            print(f"  Loaded {n} symbols from cache")
            return table
        db.drop_table(LANCEDB_TABLE)

    if LANCEDB_TABLE in db.table_names():
        db.drop_table(LANCEDB_TABLE)

    table = db.create_table(LANCEDB_TABLE, schema=schema)

    # Extract records (reuse SemanticIndex's logic)
    from abstract_fs_server.semantic_index import SemanticIndex
    all_records: list[dict] = []
    for rel_path, file_entry in abstract_index.files.items():
        all_records.extend(SemanticIndex._extract_records(rel_path, file_entry))

    print(f"  Embedding {len(all_records)} symbols with jina-v4 (truncate_dim={EMBED_DIM})...")
    t0 = time.perf_counter()
    texts = [r["text"] for r in all_records]
    vectors = embedder.encode_passages(texts, batch_size=8)
    print(f"  Embedded in {time.perf_counter() - t0:.1f}s")

    table.add(pa.table({
        "id":     [r["id"] for r in all_records],
        "path":   [r["path"] for r in all_records],
        "symbol": [r["symbol"] for r in all_records],
        "text":   texts,
        "vector": [v.tolist() for v in vectors],
    }))

    try:
        table.create_fts_index("text", replace=True)
    except Exception:
        pass

    print(f"  Index built: {len(all_records)} symbols")
    return table


# ── Search function ───────────────────────────────────────────────────────────

def search(query: str, table, embedder: JinaEmbedder, reranker: JinaReranker | None,
           k: int = 5) -> list[str]:
    """Embed query, hybrid search, RRF merge, rerank. Returns list of 'path::symbol' ids."""
    query_vec = embedder.encode_query(query).tolist()

    vec_results = table.search(query_vec).limit(k * 4).to_list()
    try:
        fts_results = table.search(query).limit(k * 2).to_list()
    except Exception:
        fts_results = []

    merged = rrf_merge(vec_results, fts_results)[:30]

    if reranker is not None:
        final = reranker.rerank(query, merged, top_k=k)
    else:
        final = merged[:k]

    return [f"{row['path']}::{row['symbol']}" for row in final]


# ── Eval runners ─────────────────────────────────────────────────────────────

def run_concept_eval(table, embedder: JinaEmbedder, reranker: JinaReranker | None,
                     k: int) -> list[QueryResult]:
    results = []
    for query, acceptable in CONCEPT_QUERIES:
        hit_ids = search(query, table, embedder, reranker, k=k)
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


def run_docstring_eval(pairs: list[tuple[str, str]], table, embedder: JinaEmbedder,
                       reranker: JinaReranker | None, k: int,
                       verbose: bool = False) -> list[QueryResult]:
    results = []
    total = len(pairs)
    for i, (docstring, expected_id) in enumerate(pairs, start=1):
        if i % 50 == 0:
            print(f"  [{i}/{total}] ...", flush=True)
        query = docstring[:500]
        hit_ids = search(query, table, embedder, reranker, k=k)
        rank = find_rank(expected_id.split("::")[-1], hit_ids)
        results.append(QueryResult(
            query=query[:80],
            expected_id=expected_id,
            rank=rank,
            results_count=len(hit_ids),
        ))
    return results


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=str(REPO_ROOT / "src"))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--cache-dir", default="/tmp/sem_eval_jina_cache")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--concept-only", action="store_true")
    parser.add_argument("--docstring-only", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    target = str(Path(args.target).resolve())
    print(f"Target     : {target}")
    print(f"Embedder   : {JINA_EMBED_MODEL} (truncate_dim={EMBED_DIM})")
    print(f"Reranker   : {JINA_RERANKER_MODEL}")
    print(f"k          : {args.k}")

    # AbstractIndex
    print("\n[1/4] Building AbstractIndex...")
    t0 = time.perf_counter()
    from abstract_engine.index import AbstractIndex
    abstract_index = AbstractIndex.load_or_build(target)
    print(f"      {len(abstract_index.files)} files in {time.perf_counter() - t0:.1f}s")

    # Docstring pairs
    print("\n[2/4] Collecting docstring pairs...")
    pairs = collect_docstring_pairs(abstract_index, min_len=20)
    print(f"      {len(pairs)} pairs (using all)")

    # Load models
    print("\n[3/4] Loading jina models...")
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"      Device: {device}")
    embedder = JinaEmbedder(device=device)
    reranker = JinaReranker(device=device)

    # Build index
    print("\n[4/4] Building jina-v4 vector index...")
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    table = build_jina_index(abstract_index, args.cache_dir, embedder, force_rebuild=args.rebuild)

    comparison_rows: list[tuple[str, dict]] = []

    # ── Run 1: jina-v4 embeddings, NO reranker ───────────────────────────────
    print("\n\n── jina-v4 + no reranker ────────────────────────────────────────")
    metrics_no_rerank: dict = {}

    if not args.docstring_only:
        print("  Running concept queries...")
        concept_r = run_concept_eval(table, embedder, None, args.k)
        print_metrics("Concept Queries — jina-v4 + no reranker", concept_r, args.k)
        if args.verbose:
            print_concept_detail(concept_r)
        metrics_no_rerank.update({
            "concept_r1": recall_at_k(concept_r, 1),
            "concept_mrr": mrr(concept_r),
            "n_concept": len(concept_r),
        })

    if not args.concept_only:
        print("  Running docstring eval (all pairs)...")
        t0 = time.perf_counter()
        doc_r = run_docstring_eval(pairs, table, embedder, None, args.k, verbose=args.verbose)
        elapsed = time.perf_counter() - t0
        print(f"  Done in {elapsed:.1f}s ({elapsed/len(pairs)*1000:.0f}ms/query)")
        print_metrics("Docstring Retrieval — jina-v4 + no reranker", doc_r, args.k)
        metrics_no_rerank.update({
            "doc_r1": recall_at_k(doc_r, 1),
            "doc_r3": recall_at_k(doc_r, 3),
            "doc_r5": recall_at_k(doc_r, args.k),
            "doc_mrr": mrr(doc_r),
            "n_doc": len(doc_r),
        })

    comparison_rows.append(("jina-v4 + no reranker", metrics_no_rerank))

    # ── Run 2: jina-v4 embeddings + jina-reranker-v3 ────────────────────────
    print("\n\n── jina-v4 + jina-reranker-v3 ───────────────────────────────────")
    metrics_jina: dict = {}

    if not args.docstring_only:
        print("  Running concept queries...")
        concept_r = run_concept_eval(table, embedder, reranker, args.k)
        print_metrics("Concept Queries — jina-v4 + jina-reranker-v3", concept_r, args.k)
        if args.verbose:
            print_concept_detail(concept_r)
        metrics_jina.update({
            "concept_r1": recall_at_k(concept_r, 1),
            "concept_mrr": mrr(concept_r),
            "n_concept": len(concept_r),
        })

    if not args.concept_only:
        print("  Running docstring eval (all pairs)...")
        t0 = time.perf_counter()
        doc_r = run_docstring_eval(pairs, table, embedder, reranker, args.k, verbose=args.verbose)
        elapsed = time.perf_counter() - t0
        print(f"  Done in {elapsed:.1f}s ({elapsed/len(pairs)*1000:.0f}ms/query)")
        print_metrics("Docstring Retrieval — jina-v4 + jina-reranker-v3", doc_r, args.k)
        metrics_jina.update({
            "doc_r1": recall_at_k(doc_r, 1),
            "doc_r3": recall_at_k(doc_r, 3),
            "doc_r5": recall_at_k(doc_r, args.k),
            "doc_mrr": mrr(doc_r),
            "n_doc": len(doc_r),
        })

    comparison_rows.append(("jina-v4 + jina-v3", metrics_jina))

    # ── Comparison table ─────────────────────────────────────────────────────
    # Add known bge results (mark unmeasured as 0 for display)
    all_rows: list[tuple[str, dict]] = []
    for label, m in KNOWN_BGE_RESULTS:
        display = {k: (v if v is not None else float("nan")) for k, v in m.items()
                   if not k.startswith("_")}
        all_rows.append((label, display))
    all_rows.extend(comparison_rows)

    print_comparison_table(all_rows)
    print("  Note: bge rows with 'nan' were not measured in this run.")
    print(f"        Concept queries n={len(CONCEPT_QUERIES)}, Docstring pairs n={len(pairs)}\n")


# ── Restore transformers after eval ──────────────────────────────────────────
def _restore_transformers():
    print("\n[teardown] Restoring transformers==5.5.0...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "transformers==5.5.0"],
        check=True,
    )
    print("[teardown] Done.")


if __name__ == "__main__":
    try:
        main()
    finally:
        _restore_transformers()
