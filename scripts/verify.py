#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path


HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO / "source"
SOURCE = SOURCE_ROOT / "claude"
RUNTIME = SOURCE_ROOT / "runtime"
CLAUDE_CONFIG = HOME / ".claude.json"
GEMINI_SETTINGS = HOME / ".gemini" / "settings.json"
CODEX_CONFIG = HOME / ".codex" / "config.toml"
MANAGED_BLOCK_START = "# BEGIN CLAUDE-ORACLE MANAGED MCP"
MANAGED_BLOCK_END = "# END CLAUDE-ORACLE MANAGED MCP"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_skill_checks() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    skill_root = SOURCE / "skills"
    if not skill_root.exists():
        return checks
    for src in sorted(p for p in skill_root.rglob("*") if p.is_file()):
        dst = HOME / ".claude" / "skills" / src.relative_to(skill_root)
        checks.append((f"skill:{src.relative_to(skill_root).as_posix()}", dst.exists() and sha256(src) == sha256(dst)))
    return checks


def collect_team_checks() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    team_root = SOURCE / "teams"
    if not team_root.exists():
        return checks
    for src in sorted(team_root.glob("*.md")):
        dst = HOME / ".claude" / "teams" / src.name
        checks.append((f"team:{src.name}", dst.exists() and sha256(src) == sha256(dst)))
    return checks


def load_server_name() -> str:
    manifest = load_json(RUNTIME / "semantic-mcp.json")
    return manifest["semantic_mcp"]["name"]


def load_expected_server() -> tuple[str, str, int | None, dict[str, str]]:
    manifest = load_json(RUNTIME / "semantic-mcp.json")
    server = manifest["semantic_mcp"]
    expected_env = {
        "PYTHONPATH": str(Path(server["repo_path"]).expanduser() / server["pythonpath"]),
        "EMBEDDING_MODEL": server.get("embedding_model", "jinaai/jina-code-embeddings-1.5b"),
        "SEMANTIC_DEVICE": server.get("semantic_device", "auto"),
        "LOG_FILE": str(Path(server["log_file"]).expanduser()),
    }
    return (
        server["name"],
        str(Path(server["python_path"]).expanduser()),
        server.get("codex_startup_timeout_sec"),
        expected_env,
    )


def main() -> None:
    checks: list[tuple[str, bool]] = []
    server_name, expected_command, expected_codex_timeout, expected_env = load_expected_server()

    checks.append((
        "claude-md",
        sha256(SOURCE / "CLAUDE.md") == sha256(HOME / ".claude" / "CLAUDE.md"),
    ))

    for src in sorted((SOURCE / "agents").glob("*.md")):
        dst = HOME / ".claude" / "agents" / src.name
        checks.append((f"agent:{src.name}", dst.exists() and sha256(src) == sha256(dst)))

    for src in sorted((SOURCE / "rules").glob("*.md")):
        dst = HOME / ".claude" / "rules" / src.name
        checks.append((f"rule:{src.name}", dst.exists() and sha256(src) == sha256(dst)))

    checks.extend(collect_skill_checks())
    checks.extend(collect_team_checks())

    claude_config = load_json(CLAUDE_CONFIG)
    claude_server_ok = server_name in claude_config.get("mcpServers", {})
    checks.append(("claude-mcp", claude_server_ok))
    checks.append((
        "claude-mcp-command",
        claude_config.get("mcpServers", {}).get(server_name, {}).get("command") == expected_command,
    ))
    checks.append((
        "claude-mcp-env",
        claude_config.get("mcpServers", {}).get(server_name, {}).get("env") == expected_env,
    ))

    gemini_exists = (HOME / ".gemini" / "GEMINI.md").exists()
    codex_exists = (HOME / ".codex" / "AGENTS.md").exists()
    checks.append(("gemini-md", gemini_exists))
    checks.append(("codex-agents", codex_exists))
    if GEMINI_SETTINGS.exists():
        gemini_settings = load_json(GEMINI_SETTINGS)
        checks.append(("gemini-mcp", server_name in gemini_settings.get("mcpServers", {})))
        checks.append((
            "gemini-mcp-command",
            gemini_settings.get("mcpServers", {}).get(server_name, {}).get("command") == expected_command,
        ))
        checks.append((
            "gemini-mcp-env",
            gemini_settings.get("mcpServers", {}).get(server_name, {}).get("env") == expected_env,
        ))
    else:
        checks.append(("gemini-mcp", False))
        checks.append(("gemini-mcp-command", False))
        checks.append(("gemini-mcp-env", False))
    if CODEX_CONFIG.exists():
        codex_config = CODEX_CONFIG.read_text(encoding="utf-8")
        checks.append(("codex-mcp", MANAGED_BLOCK_START in codex_config and f"[mcp_servers.{server_name}]" in codex_config))
        checks.append(("codex-mcp-command", f'command = "{expected_command}"' in codex_config))
        expected_env_fragments = [
            f'PYTHONPATH = "{expected_env["PYTHONPATH"]}"',
            f'EMBEDDING_MODEL = "{expected_env["EMBEDDING_MODEL"]}"',
            f'SEMANTIC_DEVICE = "{expected_env["SEMANTIC_DEVICE"]}"',
            f'LOG_FILE = "{expected_env["LOG_FILE"]}"',
        ]
        checks.append((
            "codex-mcp-env",
            all(fragment in codex_config for fragment in expected_env_fragments),
        ))
        if expected_codex_timeout is not None:
            checks.append((
                "codex-mcp-startup-timeout",
                f"startup_timeout_sec = {expected_codex_timeout}" in codex_config,
            ))
    else:
        checks.append(("codex-mcp", False))
        checks.append(("codex-mcp-command", False))
        checks.append(("codex-mcp-env", False))
        if expected_codex_timeout is not None:
            checks.append(("codex-mcp-startup-timeout", False))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{name}: {'OK' if ok else 'FAIL'}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
