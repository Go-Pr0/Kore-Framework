"""SemanticIndex — LanceDB-backed vector search using the Jina stack.

Default embedding:  jinaai/jina-code-embeddings-1.5b  (truncate_dim=1024)
Default reranking:  jinaai/jina-reranker-v3    (native rerank() API)
Pipeline:           embed -> hybrid (vector + FTS) -> RRF merge -> Jina rerank
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

_SIGNATURE_RANGE_TAIL = re.compile(r"\s+L\d+-\d+\s*$")
_STRUCTURED_RECORD_TYPES = frozenset({"function", "method", "class", "type", "file"})


def _compute_index_fingerprint(abstract_index: AbstractIndex) -> str:
    """Stable fingerprint of the abstract index used to detect rebuild need.

    Encodes file count, total function count, and total semantic-region count.
    Any of those changing means the LanceDB is stale and must be rebuilt.
    """
    file_count = len(abstract_index.files)
    func_count = sum(len(fe.functions) for fe in abstract_index.files.values())
    region_count = sum(len(fe.semantic_regions) for fe in abstract_index.files.values())
    return f"{file_count}:{func_count}:{region_count}"

if TYPE_CHECKING:
    from abstract_engine.index import AbstractIndex
    from abstract_engine.models import FileEntry, FunctionEntry

log = logging.getLogger(__name__)

_EMBEDDING_DIM = 1024
_TABLE_NAME = "jina_semantic_retrieval_1024"
_RERANKER_MODEL = "jinaai/jina-reranker-v3"
_DEFAULT_EMBED_MODEL = "jinaai/jina-code-embeddings-1.5b"

# Cap the character length of any single text before it reaches the embedder.
# The embedder tokenizer will truncate anyway, but trimming up front bounds
# the amount of memory the tokenizer buffers.
_MAX_EMBED_CHARS = 4000

# Passage embedding batch size.  Small enough to fit comfortably in 20 GB VRAM
# on a 7900 XT while keeping throughput acceptable.
_EMBED_BATCH_SIZE = 4


# ---------------------------------------------------------------------------
# Module-level shared model loader
# ---------------------------------------------------------------------------


def load_shared_models(model_name: str, device: str) -> tuple[Any, Any]:
    """Load the embedding and reranker models once for shared use across repos.

    This is intended to be called exactly once at daemon startup. Both returned
    objects are then passed to every SemanticIndex so that model weights are
    held in VRAM only once, regardless of how many repos are active.

    VRAM strategy:

    * The embedder loads in half precision (bfloat16 for cuda/rocm, float16 for
      mps) onto the GPU — the hot path that every search and every index build
      hits, so we want it fast.
    * The reranker loads onto CPU in float32.  It is called at most once per
      query with a handful of documents, so CPU latency is acceptable and the
      VRAM saving (~3 GB on a 7900 XT class card) is significant.
    * ``torch.inference_mode()`` wraps embedding calls (done inside
      ``_embed_passages`` / ``_embed_query``) to skip autograd bookkeeping.

    Args:
        model_name: HuggingFace model ID for the embedder
                    (e.g. "jinaai/jina-code-embeddings-1.5b").
        device: Target device string — "auto", "cuda", "mps", or "cpu".

    Returns:
        A ``(embedder, reranker)`` tuple. Both objects may be ``None`` if the
        required libraries are unavailable or loading fails.
    """
    try:
        import torch
        from sentence_transformers import SentenceTransformer
        from transformers import AutoModel
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()

        resolved_device = _resolve_device_static(device, torch)

        if resolved_device in ("cuda", "mps"):
            embed_dtype = torch.bfloat16 if resolved_device == "cuda" else torch.float16
        else:
            embed_dtype = torch.float32

        log.info(
            "load_shared_models: loading embedder %s on %s (dtype=%s) ...",
            model_name,
            resolved_device,
            embed_dtype,
        )
        # The Jina code-embeddings model has no ``auto_map`` in its config, so
        # loading it via ``AutoModel + trust_remote_code`` drops us at a bare
        # Qwen2Model without ``encode_text``.  Instead, load it through
        # SentenceTransformer — the cache already contains modules.json,
        # 1_Pooling, and 2_Normalize — and call ``encode`` with prompt_name.
        embedder = SentenceTransformer(
            model_name,
            device=resolved_device,
            model_kwargs={"torch_dtype": embed_dtype},
            trust_remote_code=True,
        )
        embedder.eval()
        log.info("load_shared_models: embedder loaded on %s", resolved_device)

        log.info(
            "load_shared_models: loading reranker %s on %s (dtype=%s) ...",
            _RERANKER_MODEL,
            resolved_device,
            embed_dtype,
        )
        reranker = AutoModel.from_pretrained(
            _RERANKER_MODEL,
            trust_remote_code=True,
            torch_dtype=embed_dtype,
        )
        reranker = reranker.to(resolved_device)
        reranker.eval()
        log.info("load_shared_models: reranker loaded on %s", resolved_device)

        if resolved_device == "cuda":
            try:
                torch.cuda.empty_cache()
                free, total = torch.cuda.mem_get_info()
                log.info(
                    "load_shared_models: cuda VRAM free=%.2f GB / total=%.2f GB",
                    free / 1024**3,
                    total / 1024**3,
                )
            except Exception:  # noqa: BLE001
                pass

        return embedder, reranker

    except ImportError as exc:
        log.warning("load_shared_models: missing dependency (%s) — semantic search disabled.", exc)
        return None, None
    except Exception:
        log.exception("load_shared_models: failed to load models")
        return None, None


def _resolve_device_static(device: str, torch) -> str:
    """Resolve a device string using torch availability checks."""
    device = (device or "auto").strip().lower()
    if device == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if (
            getattr(torch.backends, "mps", None) is not None
            and torch.backends.mps.is_available()
        ):
            return "mps"
        return "cpu"
    if device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("SEMANTIC_DEVICE=cuda but CUDA is not available on this machine.")
        return device
    if device == "mps":
        if not (
            getattr(torch.backends, "mps", None) is not None
            and torch.backends.mps.is_available()
        ):
            raise RuntimeError(
                "SEMANTIC_DEVICE=mps but Apple Metal Performance Shaders are not available."
            )
        return device
    if device == "cpu":
        return device
    raise RuntimeError(
        f"Unsupported SEMANTIC_DEVICE={device!r}. Use auto, cuda, mps, or cpu."
    )


# ---------------------------------------------------------------------------
# SemanticIndex
# ---------------------------------------------------------------------------


class SemanticIndex:
    """Vector database of function/method semantic signatures.

    Wraps LanceDB + Jina embeddings + jina-reranker-v3 behind a lazy-init
    facade so that import cost is zero and missing deps degrade gracefully.

    Two construction modes:

    1. **Pre-loaded models** (daemon / multi-repo mode)::

           si = SemanticIndex(
               lancedb_path,
               embedder=shared_embedder,
               reranker=shared_reranker,
           )

       When ``embedder`` and ``reranker`` are provided the internal model-load
       path is skipped entirely.  ``_is_available`` is set to ``True``
       immediately (assuming the DB is accessible) so no redundant load occurs.

    2. **Lazy load** (stdio / test mode)::

           si = SemanticIndex(lancedb_path, model="jinaai/...", device="auto")

       Models are loaded on first use inside ``_ensure_ready()``, preserving
       the existing behaviour.
    """

    def __init__(
        self,
        lancedb_path: str,
        model: str = _DEFAULT_EMBED_MODEL,
        device: str = "auto",
        *,
        embedder: Any = None,
        reranker: Any = None,
    ) -> None:
        self._lancedb_path = lancedb_path
        self._embed_model = model
        self._device = device
        self._db = None
        self._table = None
        self._ready_event = asyncio.Event()
        self._last_error: str | None = None

        # Map of rel_path -> content_hash for the most recently indexed
        # version of each file.  Used to short-circuit re-embedding when a
        # watcher event fires but the file contents are byte-identical to
        # what we already have (typical for ``cp -a``, ``git checkout``,
        # backup snapshots, etc.).  Populated during ``build_from_index``
        # and kept current by ``update_file`` / ``update_files``.
        self._file_hashes: dict[str, str] = {}

        # --- Pre-loaded model path -------------------------------------------
        if embedder is not None:
            # Injected models: skip internal loading, mark available immediately.
            self._embedder = embedder
            self._reranker = reranker
            self._is_available: bool | None = None  # resolved on first DB open
            self._build_state = "pending"
            self._preloaded = True
        else:
            # Lazy-load path (backwards compat).
            self._embedder = None
            self._reranker = None
            self._is_available = None
            self._build_state = "pending"
            self._preloaded = False

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _has_mps(torch) -> bool:
        return bool(
            getattr(torch.backends, "mps", None) is not None
            and torch.backends.mps.is_available()
        )

    def _resolve_device(self, torch) -> str:
        device = (self._device or "auto").strip().lower()
        if device == "auto":
            if torch.cuda.is_available():
                return "cuda"
            if self._has_mps(torch):
                return "mps"
            return "cpu"
        if device == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "SEMANTIC_DEVICE=cuda but CUDA is not available on this machine."
                )
            return device
        if device == "mps":
            if not self._has_mps(torch):
                raise RuntimeError(
                    "SEMANTIC_DEVICE=mps but Apple Metal Performance Shaders are not available."
                )
            return device
        if device == "cpu":
            return device
        raise RuntimeError(
            f"Unsupported SEMANTIC_DEVICE={self._device!r}. Use auto, cuda, mps, or cpu."
        )

    def _ensure_ready(self) -> bool:
        if self._is_available is not None:
            return self._is_available

        if self._preloaded:
            # Models already injected — just open/create the LanceDB table.
            try:
                import lancedb  # noqa: F401
                import pyarrow  # noqa: F401
            except ImportError as exc:
                log.warning("Semantic search unavailable — missing dependency: %s", exc)
                self._is_available = False
                self._build_state = "unavailable"
                self._last_error = str(exc)
                return False

            try:
                import lancedb

                if self._db is None:
                    self._db = lancedb.connect(self._lancedb_path)
                self._open_or_create_table()
                self._is_available = True
                return True
            except Exception:
                log.exception("Failed to open LanceDB for pre-loaded SemanticIndex")
                self._is_available = False
                self._build_state = "failed"
                self._last_error = logging.Formatter().formatException(
                    __import__("sys").exc_info()
                )
                return False

        # --- Original lazy-load path -----------------------------------------
        try:
            import lancedb  # noqa: F401
            import pyarrow  # noqa: F401
            from transformers import AutoModel  # noqa: F401
        except ImportError as exc:
            log.warning("Semantic search unavailable — missing dependency: %s", exc)
            self._is_available = False
            self._build_state = "unavailable"
            self._last_error = str(exc)
            return False

        try:
            import torch
            from transformers import AutoModel
            from transformers.utils import logging as hf_logging

            hf_logging.disable_progress_bar()
            self._build_state = "loading_models"
            self._last_error = None

            device = self._resolve_device(torch)

            if self._embedder is None:
                log.info("Loading %s on %s ...", self._embed_model, device)
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(
                    self._embed_model,
                    device=device,
                    trust_remote_code=True,
                )
                self._embedder.eval()
                log.info("Embedder loaded.")

            if self._reranker is None:
                log.info("Loading %s on %s ...", _RERANKER_MODEL, device)
                self._reranker = AutoModel.from_pretrained(
                    _RERANKER_MODEL, trust_remote_code=True
                )
                try:
                    self._reranker = self._reranker.to(device)
                except Exception:
                    pass
                log.info("Reranker loaded.")

            import lancedb

            if self._db is None:
                self._db = lancedb.connect(self._lancedb_path)

            self._open_or_create_table()
            self._is_available = True
            return True

        except Exception:
            log.exception("Failed to initialise SemanticIndex")
            self._is_available = False
            self._build_state = "failed"
            self._last_error = logging.Formatter().formatException(__import__("sys").exc_info())
            return False

    def status_summary(self) -> tuple[str, str | None]:
        return self._build_state, self._last_error

    def _open_or_create_table(self) -> None:
        import pyarrow as pa

        schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("path", pa.string()),
            pa.field("record_type", pa.string()),
            pa.field("language", pa.string()),
            pa.field("extraction_mode", pa.string()),
            pa.field("symbol", pa.string()),
            pa.field("title", pa.string()),
            pa.field("start_line", pa.int32()),
            pa.field("end_line", pa.int32()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), _EMBEDDING_DIM)),
        ])
        try:
            tables = self._db.list_tables()
        except AttributeError:
            tables = self._db.table_names()
        if _TABLE_NAME in tables:
            self._table = self._db.open_table(_TABLE_NAME)
        else:
            try:
                self._table = self._db.create_table(_TABLE_NAME, schema=schema)
            except (ValueError, Exception) as e:
                if "already exists" in str(e).lower():
                    self._table = self._db.open_table(_TABLE_NAME)
                else:
                    raise

    # ------------------------------------------------------------------
    # Record extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_records(rel_path: str, file_entry: FileEntry) -> list[dict]:
        from abstract_engine.renderer import render_tier1_function

        records: list[dict] = []
        extraction_mode = file_entry.extraction_mode or "generalized"
        language = file_entry.language or "unknown"

        module_summary: str | None = None
        if file_entry.module_docstring:
            module_summary = file_entry.module_docstring.strip().strip("\"'").strip()
            if len(module_summary) > 120:
                module_summary = module_summary[:117] + "..."
        elif file_entry.tier1_text:
            first_line = file_entry.tier1_text.splitlines()[0] if file_entry.tier1_text else ""
            if "]: " in first_line:
                module_summary = first_line.split("]: ", 1)[1].strip()

        def build_text(symbol_name: str, func: FunctionEntry,
                       cls_name: str | None = None, cls_entry=None) -> str:
            tier1 = render_tier1_function(func, is_method=cls_name is not None)
            parts = [f"File: {rel_path}"]
            parts.append(f"Language: {language}")
            parts.append(f"Extraction: {extraction_mode}")
            if module_summary:
                parts.append(f"Module: {module_summary}")
            parts.append(f"Symbol: {symbol_name}")
            if cls_name:
                if cls_entry and cls_entry.docstring_first_line:
                    parts.append(f"Class: {cls_name} [{cls_entry.docstring_first_line}]")
                else:
                    parts.append(f"Class: {cls_name}")
            parts.append(f"Signature: {tier1}")
            if func.docstring_full:
                ds = func.docstring_full.strip().strip("\"'").strip()
                if ds:
                    if len(ds) > 1000:
                        ds = ds[:997] + "..."
                    parts.append(f"Docstring: {ds}")
            if func.calls:
                call_names = sorted({c.callee_name for c in func.calls})
                if call_names:
                    parts.append(f"Calls: {', '.join(call_names)}")
            if func.called_by:
                caller_names = [c.caller_name for c in func.called_by[:5]]
                if caller_names:
                    parts.append(f"Used by: {', '.join(caller_names)}")
            return "\n".join(parts)

        def build_class_text(cls_name: str, cls_entry) -> str:
            parts = [
                f"File: {rel_path}",
                f"Language: {language}",
                f"Extraction: {extraction_mode}",
                f"Type: {cls_name}",
            ]
            if module_summary:
                parts.append(f"Module: {module_summary}")
            if cls_entry.base_classes:
                parts.append(f"Bases: {', '.join(cls_entry.base_classes)}")
            if cls_entry.docstring_first_line:
                parts.append(f"Summary: {cls_entry.docstring_first_line}")
            if cls_entry.class_attributes or cls_entry.instance_attributes:
                attrs = cls_entry.class_attributes + cls_entry.instance_attributes
                attr_names = ", ".join(attr.name for attr in attrs[:12])
                if attr_names:
                    parts.append(f"Attributes: {attr_names}")
            if cls_entry.methods:
                method_names = ", ".join(list(cls_entry.methods.keys())[:12])
                if method_names:
                    parts.append(f"Methods: {method_names}")
            return "\n".join(parts)

        def build_type_text(type_name: str, type_entry) -> str:
            parts = [
                f"File: {rel_path}",
                f"Language: {language}",
                f"Extraction: {extraction_mode}",
                f"Type: {type_name}",
            ]
            if module_summary:
                parts.append(f"Module: {module_summary}")
            if type_entry.kind:
                parts.append(f"Kind: {type_entry.kind}")
            if type_entry.source_text:
                source_text = type_entry.source_text
                if len(source_text) > 1000:
                    source_text = source_text[:997] + "..."
                parts.append(f"Shape: {source_text}")
            return "\n".join(parts)

        file_parts = [
            f"File: {rel_path}",
            f"Language: {language}",
            f"Extraction: {extraction_mode}",
        ]
        if module_summary:
            file_parts.append(f"Summary: {module_summary}")
        if file_entry.tier1_text:
            tier1 = file_entry.tier1_text.strip()
            if len(tier1) > 4000:
                tier1 = tier1[:4000] + "\n... (truncated)"
            file_parts.append(f"Outline:\n{tier1}")
        if file_entry.parse_error_detail:
            file_parts.append(f"Index detail: {file_entry.parse_error_detail}")
        records.append({
            "id": f"{rel_path}::__file__",
            "path": rel_path,
            "record_type": "file",
            "language": language,
            "extraction_mode": extraction_mode,
            "symbol": "__file__",
            "title": rel_path,
            "start_line": 1,
            "end_line": max(file_entry.line_count, 1),
            "text": "\n".join(file_parts),
        })

        for func in file_entry.functions.values():
            records.append({
                "id": f"{rel_path}::{func.name}",
                "path": rel_path,
                "record_type": "function",
                "language": language,
                "extraction_mode": extraction_mode,
                "symbol": func.name,
                "title": func.qualified_name or func.name,
                "start_line": func.start_line,
                "end_line": func.end_line,
                "text": build_text(func.name, func),
            })

        for cls in file_entry.classes.values():
            records.append({
                "id": f"{rel_path}::class::{cls.name}",
                "path": rel_path,
                "record_type": "class",
                "language": language,
                "extraction_mode": extraction_mode,
                "symbol": cls.name,
                "title": cls.name,
                "start_line": cls.start_line,
                "end_line": cls.end_line,
                "text": build_class_text(cls.name, cls),
            })
            for method in cls.methods.values():
                qualified = f"{cls.name}.{method.name}"
                records.append({
                    "id": f"{rel_path}::{qualified}",
                    "path": rel_path,
                    "record_type": "method",
                    "language": language,
                    "extraction_mode": extraction_mode,
                    "symbol": qualified,
                    "title": qualified,
                    "start_line": method.start_line,
                    "end_line": method.end_line,
                    "text": build_text(qualified, method, cls_name=cls.name, cls_entry=cls),
                })

        for type_name, type_entry in file_entry.types.items():
            records.append({
                "id": f"{rel_path}::type::{type_name}",
                "path": rel_path,
                "record_type": "type",
                "language": language,
                "extraction_mode": extraction_mode,
                "symbol": type_name,
                "title": type_name,
                "start_line": type_entry.start_line,
                "end_line": type_entry.start_line,
                "text": build_type_text(type_name, type_entry),
            })

        for idx, region in enumerate(file_entry.semantic_regions):
            records.append({
                "id": f"{rel_path}::region::{idx}",
                "path": rel_path,
                "record_type": region.kind,
                "language": language,
                "extraction_mode": extraction_mode,
                "symbol": "__region__",
                "title": region.title,
                "start_line": region.start_line,
                "end_line": region.end_line,
                "text": "\n".join([
                    f"File: {rel_path}",
                    f"Language: {language}",
                    f"Extraction: {extraction_mode}",
                    f"Region: {region.title}",
                    f"Lines: {region.start_line}-{region.end_line}",
                    region.text,
                ]),
            })

        return records

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _encode(self, texts: list[str], prompt_name: str) -> "np.ndarray":
        """Run the SentenceTransformer embedder and return an (N, 1024) array.

        The Jina code-embeddings model emits 1536-dim vectors natively but
        supports Matryoshka truncation. Our LanceDB schema is fixed at 1024
        dims, so we take the first 1024 components and re-normalise.
        """
        import numpy as np
        import torch

        clipped = [t[:_MAX_EMBED_CHARS] if len(t) > _MAX_EMBED_CHARS else t for t in texts]
        if not clipped:
            return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)

        all_vecs: list[np.ndarray] = []
        with torch.inference_mode():
            for start in range(0, len(clipped), _EMBED_BATCH_SIZE):
                chunk = clipped[start : start + _EMBED_BATCH_SIZE]
                vecs = self._embedder.encode(
                    chunk,
                    prompt_name=prompt_name,
                    batch_size=_EMBED_BATCH_SIZE,
                    convert_to_numpy=False,
                    convert_to_tensor=True,
                    normalize_embeddings=False,
                    show_progress_bar=False,
                )
                arr = vecs.float().cpu().numpy().astype(np.float32)
                all_vecs.append(arr)
                # Intentionally do NOT call torch.cuda.empty_cache() per
                # batch: on ROCm it forces a device sync and a release of
                # the caching allocator, which adds tens of ms of latency
                # to every single query for no practical memory win.

        full = np.concatenate(all_vecs, axis=0)
        # Matryoshka-style truncate to _EMBEDDING_DIM and re-normalise.
        truncated = full[:, :_EMBEDDING_DIM]
        norms = np.linalg.norm(truncated, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return (truncated / norms).astype(np.float32)

    def _embed_passages(self, texts: list[str]) -> "np.ndarray":
        return self._encode(texts, prompt_name="nl2code_document")

    def _embed_query(self, query: str) -> list[float]:
        arr = self._encode([query], prompt_name="nl2code_query")
        return arr[0].tolist()

    # ------------------------------------------------------------------
    # Merge and reranking
    # ------------------------------------------------------------------

    @staticmethod
    def _rrf_merge(vec_results: list[dict], fts_results: list[dict], k: int = 60) -> list[dict]:
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

    def _rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if self._reranker is None or not candidates:
            return candidates[:top_k]
        import torch

        docs = [
            (row["text"][:_MAX_EMBED_CHARS] if len(row["text"]) > _MAX_EMBED_CHARS else row["text"])
            for row in candidates
        ]
        clipped_query = query[:_MAX_EMBED_CHARS]
        try:
            with torch.inference_mode():
                ranked = self._reranker.rerank(clipped_query, docs, top_n=top_k)
        except Exception:  # noqa: BLE001
            log.exception("Reranker call failed — returning unranked candidates")
            return candidates[:top_k]
        ranked = sorted(ranked, key=lambda x: x["relevance_score"], reverse=True)
        return [candidates[r["index"]] for r in ranked[:top_k]]

    # ------------------------------------------------------------------
    # Public sync API
    # ------------------------------------------------------------------

    def try_load_from_disk(self, abstract_index: AbstractIndex) -> bool:
        """Restore from an existing LanceDB if its fingerprint matches the current index.

        Returns True and transitions to ``ready`` if a valid, non-empty table
        exists whose fingerprint matches ``abstract_index``.  Returns False if
        the index is missing, stale, or empty — caller should schedule a full
        rebuild in that case.
        """
        meta_path = self._lancedb_path + ".meta.json"
        if not os.path.exists(meta_path):
            return False

        try:
            with open(meta_path) as f:
                meta = json.load(f)
        except Exception:
            return False

        current_fp = _compute_index_fingerprint(abstract_index)
        stored_fp = meta.get("fingerprint")
        if stored_fp != current_fp:
            log.info(
                "SemanticIndex: fingerprint changed (stored=%s current=%s) — will rebuild",
                stored_fp,
                current_fp,
            )
            return False

        if not self._ensure_ready():
            return False

        try:
            count = self._table.count_rows()
        except Exception:
            return False

        if count == 0:
            return False

        self._build_state = "ready"
        self._ready_event.set()
        log.info(
            "SemanticIndex: restored from disk — %d records, fingerprint=%s",
            count,
            current_fp,
        )
        return True

    def _save_meta(self, abstract_index: AbstractIndex) -> None:
        """Persist build fingerprint so the next startup can skip a full rebuild."""
        meta_path = self._lancedb_path + ".meta.json"
        try:
            with open(meta_path, "w") as f:
                json.dump({"fingerprint": _compute_index_fingerprint(abstract_index)}, f)
        except Exception:
            log.warning("SemanticIndex: failed to persist build meta to %s", meta_path)

    def build_from_index(self, abstract_index: AbstractIndex) -> None:
        self._build_state = "building"
        self._last_error = None
        if not self._ensure_ready():
            self._ready_event.set()
            return

        import pyarrow as pa

        try:
            try:
                tables = self._db.list_tables()
            except AttributeError:
                tables = self._db.table_names()
            if _TABLE_NAME in tables:
                self._db.drop_table(_TABLE_NAME)
            self._open_or_create_table()

            all_records: list[dict] = []
            fresh_hashes: dict[str, str] = {}
            for rel_path, file_entry in abstract_index.files.items():
                all_records.extend(self._extract_records(rel_path, file_entry))
                h = getattr(file_entry, "content_hash", None)
                if h:
                    fresh_hashes[rel_path] = h
            # Replace hash map atomically at the end of a successful build.
            self._file_hashes = fresh_hashes

            if not all_records:
                self._build_state = "ready"
                log.info("SemanticIndex build complete — 0 records.")
                return

            texts = [r["text"] for r in all_records]
            vectors = self._embed_passages(texts)

            self._table.add(pa.table({
                "id":     [r["id"] for r in all_records],
                "path":   [r["path"] for r in all_records],
                "record_type": [r["record_type"] for r in all_records],
                "language": [r["language"] for r in all_records],
                "extraction_mode": [r["extraction_mode"] for r in all_records],
                "symbol": [r["symbol"] for r in all_records],
                "title": [r["title"] for r in all_records],
                "start_line": [r["start_line"] for r in all_records],
                "end_line": [r["end_line"] for r in all_records],
                "text":   texts,
                "vector": [v.tolist() for v in vectors],
            }))

            try:
                self._table.create_fts_index("text", replace=True)
            except Exception:
                log.warning("FTS index creation failed")

            self._build_state = "ready"
            self._save_meta(abstract_index)
            log.info("SemanticIndex build complete — %d records.", len(all_records))
        finally:
            self._ready_event.set()

    def update_file(self, rel_path: str, file_entry: FileEntry) -> None:
        """Re-embed a single file if its content has changed.

        Delegates to ``update_files`` so the hash-skip and batching paths
        stay in one place.
        """
        self.update_files([(rel_path, file_entry)])

    def update_files(
        self,
        entries: list[tuple[str, FileEntry]],
    ) -> None:
        """Batch update: re-embed multiple files with one GPU call.

        For each ``(rel_path, file_entry)`` pair:

        * If ``file_entry.content_hash`` matches the previously stored hash
          for that path, the file is skipped entirely — no delete, no
          re-extract, no embed.
        * Otherwise the old rows are deleted and the new records are
          extracted.  All records from all changed files are then embedded
          in one call so the GPU gets a proper batch instead of many
          1-item forward passes.
        """
        if not entries:
            return
        if not self._ensure_ready():
            return

        import pyarrow as pa

        # Phase 1: decide which files actually need work.
        changed: list[tuple[str, FileEntry, str | None]] = []
        skipped = 0
        for rel_path, file_entry in entries:
            new_hash = getattr(file_entry, "content_hash", None)
            old_hash = self._file_hashes.get(rel_path)
            if new_hash and old_hash == new_hash:
                skipped += 1
                continue
            changed.append((rel_path, file_entry, new_hash))

        if skipped:
            log.debug(
                "SemanticIndex.update_files: skipped %d unchanged files", skipped
            )
        if not changed:
            return

        # Phase 2: delete old rows for every changed file.  We batch the
        # deletes into a single SQL IN clause to avoid one round-trip per
        # path.
        quoted = ", ".join("'{}'".format(r.replace("'", "''")) for r, _, _ in changed)
        try:
            self._table.delete(f"path IN ({quoted})")
        except Exception:  # noqa: BLE001
            log.exception("SemanticIndex.update_files: batched delete failed; "
                          "falling back to per-path deletes")
            for rel_path, _, _ in changed:
                try:
                    self._table.delete(f"path = '{rel_path}'")
                except Exception:  # noqa: BLE001
                    log.warning("Delete failed for %s", rel_path)

        # Phase 3: extract records for every changed file and embed as one
        # batch.
        all_records: list[dict] = []
        for rel_path, file_entry, _ in changed:
            all_records.extend(self._extract_records(rel_path, file_entry))
        if not all_records:
            # Update hashes anyway — an empty file entry is still "known".
            for rel_path, _, new_hash in changed:
                if new_hash:
                    self._file_hashes[rel_path] = new_hash
            return

        texts = [r["text"] for r in all_records]
        vectors = self._embed_passages(texts)

        self._table.add(pa.table({
            "id":     [r["id"] for r in all_records],
            "path":   [r["path"] for r in all_records],
            "record_type": [r["record_type"] for r in all_records],
            "language": [r["language"] for r in all_records],
            "extraction_mode": [r["extraction_mode"] for r in all_records],
            "symbol": [r["symbol"] for r in all_records],
            "title": [r["title"] for r in all_records],
            "start_line": [r["start_line"] for r in all_records],
            "end_line": [r["end_line"] for r in all_records],
            "text":   texts,
            "vector": [v.tolist() for v in vectors],
        }))

        # Phase 4: update the hash map.
        for rel_path, _, new_hash in changed:
            if new_hash:
                self._file_hashes[rel_path] = new_hash

        log.info(
            "SemanticIndex.update_files: %d files re-embedded, %d skipped",
            len(changed), skipped,
        )

    def remove_file(self, rel_path: str) -> None:
        if not self._ensure_ready():
            return
        self._table.delete(f"path = '{rel_path}'")
        self._file_hashes.pop(rel_path, None)

    def search(self, query: str, k: int = 5) -> str:
        if not self._ensure_ready():
            return "[Semantic search unavailable — required libraries not installed]"

        log.debug("Semantic search: query=%r, k=%d", query, k)

        query_vec = self._embed_query(query)

        try:
            vec_results = self._table.search(query_vec).limit(k * 4).to_list()

            try:
                fts_results = self._table.search(query).limit(k * 2).to_list()
            except Exception:
                fts_results = []

            merged = self._rrf_merge(vec_results, fts_results)[:30]
            # Oversample slightly so dedup of overlapping region/function
            # records doesn't leave us short of k final hits.
            rerank_k = min(len(merged), k + 5)
            results = self._rerank(query, merged, rerank_k)

        except Exception:
            log.exception("Semantic search query failed")
            return "[Semantic search error — query failed]"

        if not results:
            return "[Semantic search: no results]"

        # First pass: positions claimed by structured records. Region/code
        # chunks that overlap a structured record are redundant — drop them.
        structured_positions: set[tuple[str, int]] = {
            (row["path"], row.get("start_line") or 0)
            for row in results
            if row.get("record_type") in _STRUCTURED_RECORD_TYPES
        }

        lines: list[str] = []
        for row in results:
            if len(lines) >= k:
                break
            record_type = row.get("record_type") or "record"
            start_line = row.get("start_line") or 0
            end_line = row.get("end_line") or 0

            if record_type not in _STRUCTURED_RECORD_TYPES and (
                row["path"],
                start_line,
            ) in structured_positions:
                continue

            signature = row.get("title") or row["symbol"]
            for text_line in row["text"].splitlines():
                if text_line.startswith("Signature: "):
                    signature = text_line[11:]
                    break
            signature = _SIGNATURE_RANGE_TAIL.sub("", signature)

            if start_line > 0 and end_line > start_line:
                location = f":{start_line}-{end_line}"
            elif start_line > 0:
                location = f":{start_line}"
            else:
                location = ""

            lines.append(f"{row['path']}{location} {record_type} {signature}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Async wrappers
    # ------------------------------------------------------------------

    async def async_search(self, query: str, k: int = 5) -> str:
        if not self._ready_event.is_set():
            log.info("Semantic search: waiting for index build...")
            await self._ready_event.wait()
        return await asyncio.to_thread(self.search, query, k)

    async def async_build_from_index(self, abstract_index: AbstractIndex) -> None:
        await asyncio.to_thread(self.build_from_index, abstract_index)

    async def async_update_file(self, rel_path: str, file_entry: FileEntry) -> None:
        if not self._ready_event.is_set():
            await self._ready_event.wait()
        await asyncio.to_thread(self.update_file, rel_path, file_entry)

    async def async_update_files(
        self,
        entries: list[tuple[str, FileEntry]],
    ) -> None:
        """Async batch variant used by the FileWatcher flush loop."""
        if not entries:
            return
        if not self._ready_event.is_set():
            await self._ready_event.wait()
        await asyncio.to_thread(self.update_files, entries)
