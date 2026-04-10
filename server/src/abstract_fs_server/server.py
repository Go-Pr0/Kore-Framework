"""Semantic MCP server entry point.

Run as a module::

    python -m abstract_fs_server.server

Or via the mcp.json command spec::

    {"command": "python", "args": ["-m", "abstract_fs_server.server"]}

Transport modes:
- stdio (default): auto-registers one repo at startup from resolve_repo_root().
- streamable-http: registry starts empty; repo_path is required on each tool call.

Startup sequence (via FastMCP lifespan):
1. Load ServerConfig from environment variables.
2. Load shared Jina embedder/reranker (once — the critical VRAM optimisation).
3. Instantiate RepoRegistry with the shared models.
4. If MCP_TRANSPORT is stdio (or unset), auto-register the resolved repo.
5. Yield — the MCP message loop runs here.
6. On shutdown: registry.shutdown() stops watchers and persists all indices.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from abstract_fs_server.config import ServerConfig
from abstract_fs_server.registry import RepoRegistry
from abstract_fs_server.semantic_index import load_shared_models
from abstract_fs_server.tools.search_tools import register_search_tools

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
# Configure early so any startup errors are captured.  The log level and
# destination are re-applied once ServerConfig is loaded.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level server state (initialised in lifespan)
# ---------------------------------------------------------------------------

_registry: RepoRegistry | None = None
_base_config: ServerConfig | None = None


def _get_registry() -> RepoRegistry | None:
    return _registry


def _get_base_config() -> ServerConfig | None:
    return _base_config


# ---------------------------------------------------------------------------
# Lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """Startup and graceful shutdown for the abstract-fs server."""
    global _registry, _base_config  # noqa: PLW0603

    # ------------------------------------------------------------------ #
    # Startup
    # ------------------------------------------------------------------ #

    base_config = ServerConfig.from_env()
    _base_config = base_config

    # Configure logging from config now that we have the level/file settings.
    _configure_logging(base_config)

    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()

    logger.info(
        "abstract-fs server starting — transport=%s, embedding_model=%s, embedding_device=%s",
        transport,
        base_config.embedding_model,
        base_config.embedding_device,
    )

    # Load shared models ONCE.  This is the critical VRAM optimisation — all
    # repos share the same embedder/reranker handles regardless of how many
    # repos are active.
    embedder, reranker = (None, None)
    if base_config.semantic_search_enabled:
        logger.info("Loading shared embedding models …")
        embedder, reranker = await asyncio.to_thread(
            load_shared_models,
            base_config.embedding_model,
            base_config.embedding_device,
        )
        if embedder is None:
            logger.warning(
                "Shared model load failed — semantic search will be unavailable."
            )
    else:
        logger.info("Semantic search disabled (SEMANTIC_SEARCH_ENABLED=false).")

    # Create the registry.
    _registry = RepoRegistry(base_config, embedder, reranker)

    # Stdio mode: auto-register the repo resolved at startup so existing
    # per-session stdio usage works unchanged (no repo_path required).
    if transport == "stdio" or transport == "":
        repo_root = base_config.repo_root
        logger.info("stdio mode: auto-registering repo at %s", repo_root)
        try:
            await _registry.get(repo_root)
        except Exception as exc:  # noqa: BLE001
            logger.critical(
                "stdio mode: failed to build index for %s: %s", repo_root, exc, exc_info=True
            )
            raise

    # ------------------------------------------------------------------ #
    # Server runs here
    # ------------------------------------------------------------------ #
    yield

    # ------------------------------------------------------------------ #
    # Shutdown
    # ------------------------------------------------------------------ #
    logger.info("abstract-fs server shutting down.")
    if _registry is not None:
        await _registry.shutdown()


# ---------------------------------------------------------------------------
# FastMCP application
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="semantic-code-search",
    instructions=(
        "Semantic code search server for Claude Code. "
        "Provides semantic, keyword, and raw search over the active repository. "
        "This server is search-only and does not expose write or shell tools."
    ),
    lifespan=server_lifespan,
)

register_search_tools(mcp, _get_registry, _get_base_config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_logging_configured = False


def _configure_logging(config: ServerConfig) -> None:
    """Apply log level and optional log file from config.

    Guarded with a module-level sentinel so repeated lifespan invocations
    (one per streamable-http session) can't stack duplicate handlers — the
    original bug produced 7x-duplicated log lines after a few sessions.
    """
    global _logging_configured  # noqa: PLW0603

    level = getattr(logging, config.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if _logging_configured:
        return

    # Clear any pre-existing handlers before attaching ours.  The module-level
    # ``logging.basicConfig`` call adds a default stream handler that we need
    # to drop when logging to a file to avoid double-writes.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if config.log_file:
        try:
            fh = logging.FileHandler(config.log_file, encoding="utf-8")
            fh.setFormatter(formatter)
            root_logger.addHandler(fh)
            logger.info("Logging to file: %s", config.log_file)
        except OSError as exc:
            logger.warning("Could not open log file %s: %s", config.log_file, exc)
            # Fall back to stderr.
            sh = logging.StreamHandler()
            sh.setFormatter(formatter)
            root_logger.addHandler(sh)
    else:
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        root_logger.addHandler(sh)

    _logging_configured = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _eager_startup() -> None:
    """Initialise config, logging, shared models, and the registry.

    FastMCP's streamable-http mode hard-codes Starlette's lifespan to the MCP
    session manager, which means our ``server_lifespan`` is never invoked in
    daemon mode. We therefore perform the same startup work synchronously in
    ``main()`` before calling ``mcp.run()``. For stdio mode this is harmless —
    ``server_lifespan`` detects that ``_registry`` is already populated and
    skips re-initialising it.
    """
    global _registry, _base_config  # noqa: PLW0603

    if _registry is not None:
        return

    base_config = ServerConfig.from_env()
    _base_config = base_config
    _configure_logging(base_config)

    logger.info(
        "abstract-fs eager startup — embedding_model=%s, embedding_device=%s",
        base_config.embedding_model,
        base_config.embedding_device,
    )

    embedder, reranker = (None, None)
    if base_config.semantic_search_enabled:
        logger.info("Loading shared embedding models …")
        embedder, reranker = load_shared_models(
            base_config.embedding_model,
            base_config.embedding_device,
        )
        if embedder is None:
            logger.warning(
                "Shared model load failed — semantic search will be unavailable."
            )
    else:
        logger.info("Semantic search disabled (SEMANTIC_SEARCH_ENABLED=false).")

    _registry = RepoRegistry(base_config, embedder, reranker)


def main() -> None:
    """Console-script entrypoint."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "streamable-http":
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_PORT", "8800"))
        _eager_startup()
        logger.info(
            "Starting abstract-fs in streamable-http mode on %s:%d", host, port
        )
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    elif transport == "stdio" or transport == "":
        mcp.run(transport="stdio")
    else:
        raise SystemExit(
            f"Unsupported MCP_TRANSPORT: {transport!r} (expected 'stdio' or 'streamable-http')"
        )


if __name__ == "__main__":
    main()
