#!/usr/bin/env python3
"""Download the MCP server models into HF_HUB_CACHE.

Run once after cloning (or after changing models in semantic-mcp.json):

    python scripts/init_models.py

Reads HF_TOKEN and HF_HUB_CACHE from .env in the repo root.
huggingface_hub picks both up automatically via environment variables — no
extra configuration needed.

After a successful run, uncomment TRANSFORMERS_OFFLINE / HF_HUB_OFFLINE in
.env so the MCP server never hits the network at startup.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = REPO / ".env"
RUNTIME = REPO / "source" / "runtime" / "semantic-mcp.json"

# Hardcoded in semantic_index.py — not configurable via env.
_RERANKER_MODEL = "jinaai/jina-reranker-v3"

_MAX_ATTEMPTS = 10
_RETRY_DELAY = 15  # seconds between outer retries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file; skip comments and blanks."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def apply_env(env: dict[str, str]) -> None:
    """Write parsed env vars into os.environ, expanding ~ in values."""
    for key, value in env.items():
        os.environ[key] = os.path.expanduser(value)


def load_embedding_model_name() -> str:
    if not RUNTIME.exists():
        return "jinaai/jina-code-embeddings-1.5b"
    data = json.loads(RUNTIME.read_text(encoding="utf-8"))
    return data.get("semantic_mcp", {}).get(
        "embedding_model", "jinaai/jina-code-embeddings-1.5b"
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_model(model_id: str) -> None:
    """Download a full model repo snapshot.

    HF_TOKEN   — picked up automatically from env (set by apply_env).
    HF_HUB_CACHE — picked up automatically from env (set by apply_env).

    snapshot_download is the canonical huggingface_hub API for this:
    https://huggingface.co/docs/huggingface_hub/en/guides/download
    """
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

    print(f"  Downloading {model_id} ...", flush=True)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            # token and cache_dir come from HF_TOKEN / HF_HUB_CACHE env vars.
            # etag_timeout: seconds to wait for HEAD (ETag) responses.
            path = snapshot_download(
                repo_id=model_id,
                etag_timeout=30,
            )
            print(f"  OK  {model_id}", flush=True)
            print(f"      {path}", flush=True)
            return

        except (RepositoryNotFoundError, EntryNotFoundError) as exc:
            # Unrecoverable — wrong model ID or missing file.
            sys.exit(f"\nERROR: {exc}")

        except Exception as exc:
            short = str(exc).split("\n")[0][:160]
            if attempt < _MAX_ATTEMPTS:
                print(
                    f"  attempt {attempt}/{_MAX_ATTEMPTS} failed: {short}",
                    flush=True,
                )
                print(f"  retrying in {_RETRY_DELAY}s ...\n", flush=True)
                time.sleep(_RETRY_DELAY)
            else:
                sys.exit(
                    f"\nERROR: {model_id} failed after {_MAX_ATTEMPTS} attempts.\n{exc}"
                )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Load .env FIRST — before any huggingface_hub import so the library
    #    reads HF_TOKEN / HF_HUB_CACHE at import time.
    apply_env(load_env_file(ENV_FILE))

    cache_dir = os.environ.get("HF_HUB_CACHE", "")
    if cache_dir:
        cache_dir = os.path.abspath(os.path.expanduser(cache_dir))
        os.makedirs(cache_dir, exist_ok=True)
        print(f"Model cache : {cache_dir}")
    else:
        print("Model cache : HF default (~/.cache/huggingface/hub)")

    # 2. Re-exec under the server venv so huggingface_hub + hf_xet are available.
    runtime_data = (
        json.loads(RUNTIME.read_text(encoding="utf-8")) if RUNTIME.exists() else {}
    )
    venv_python = Path(
        runtime_data.get("semantic_mcp", {}).get("python_path", "")
    ).expanduser()
    if venv_python and venv_python.exists() and str(venv_python) != sys.executable:
        print(f"NOTE: re-running with venv python: {venv_python}", flush=True)
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)

    # 3. Sanity-check the import.
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        sys.exit(
            "ERROR: huggingface_hub not found.\n"
            f"  {sys.executable} -m pip install huggingface_hub"
        )

    token = os.environ.get("HF_TOKEN") or None
    print(
        f"HF_TOKEN   : {'set' if token else 'not set  (add to .env for gated models)'}"
    )

    # 4. Resolve model list.
    embed_model = load_embedding_model_name()
    models = [
        (embed_model, "embedder"),
        (_RERANKER_MODEL, "reranker"),
    ]

    print("\nModels to download:")
    for model_id, role in models:
        print(f"  {model_id}  ({role})")
    print()

    # 5. Download each model.
    for model_id, _ in models:
        download_model(model_id)

    # 6. Remind user to lock to offline mode.
    print("\nAll models downloaded successfully.")
    print(
        "\nNext: open .env and uncomment the two OFFLINE lines so the MCP server "
        "never hits the network at startup:"
    )
    print("  TRANSFORMERS_OFFLINE=1")
    print("  HF_HUB_OFFLINE=1")
    print("\nThen re-run:  python scripts/sync.py")


if __name__ == "__main__":
    main()
