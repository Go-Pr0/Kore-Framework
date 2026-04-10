"""Configuration for the semantic-only MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass

from abstract_fs_server.repo_paths import repo_cache_dir, resolve_repo_root


@dataclass
class ServerConfig:
    repo_root: str
    cache_root: str
    repo_cache_dir: str
    abstract_index_path: str
    lancedb_path: str
    watch_files: bool
    log_level: str
    log_file: str | None
    include_private_functions: bool
    languages: list[str]
    extra_extensions: list[str]
    exclude_patterns: list[str]
    semantic_search_enabled: bool
    embedding_model: str
    embedding_device: str
    expand_dependency_docstrings: bool

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Build a ServerConfig from environment variables."""

        repo_root = resolve_repo_root()
        cache_root = os.environ.get(
            "SEMANTIC_MCP_CACHE_ROOT",
            os.path.join(os.path.expanduser("~"), ".cache", "claude-semantic-mcp"),
        )
        repo_specific_cache = repo_cache_dir(repo_root, cache_root)
        return cls(
            repo_root=repo_root,
            cache_root=os.path.abspath(os.path.expanduser(cache_root)),
            repo_cache_dir=repo_specific_cache,
            abstract_index_path=os.environ.get(
                "ABSTRACT_INDEX_PATH",
                os.path.join(repo_specific_cache, "abstract-index.json"),
            ),
            lancedb_path=os.environ.get(
                "LANCEDB_PATH",
                os.path.join(repo_specific_cache, "semantic-index"),
            ),
            watch_files=os.environ.get("WATCH_FILES", "true").lower() == "true",
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            log_file=os.environ.get("LOG_FILE"),
            include_private_functions=os.environ.get(
                "INCLUDE_PRIVATE_FUNCTIONS", "false"
            ).lower()
            == "true",
            languages=[
                item.strip()
                for item in os.environ.get("LANGUAGES", "").split(",")
                if item.strip()
            ],
            extra_extensions=[
                item.strip().lower()
                for item in os.environ.get(
                    "GENERIC_CODE_EXTENSIONS",
                    ".py,.pyi,.js,.jsx,.mjs,.cjs,.ts,.tsx,.mts,.cts,.rs,.go,.java,.kt,.kts,.scala,.swift,.c,.cc,.cpp,.cxx,.h,.hpp,.cs,.php,.rb,.lua,.sh,.bash,.zsh,.md,.mdx,.rst,.txt,.json,.yaml,.yml,.toml,.ini,.cfg,.conf,.env,.xml,.html,.css,.sql,.graphql,.proto",
                ).split(",")
                if item.strip()
            ],
            exclude_patterns=(
                os.environ.get("EXCLUDE_PATTERNS", "").split(",")
                if os.environ.get("EXCLUDE_PATTERNS")
                else []
            ),
            semantic_search_enabled=os.environ.get("SEMANTIC_SEARCH_ENABLED", "true").lower() == "true",
            embedding_model=os.environ.get("EMBEDDING_MODEL", "jinaai/jina-code-embeddings-1.5b"),
            embedding_device=os.environ.get("SEMANTIC_DEVICE", "auto").strip().lower() or "auto",
            expand_dependency_docstrings=os.environ.get("EXPAND_DEPENDENCY_DOCSTRINGS", "true").lower() == "true",
        )
