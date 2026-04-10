#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO / "source"
SOURCE = SOURCE_ROOT / "claude"
RUNTIME = SOURCE_ROOT / "runtime"
CLAUDE_HOME = HOME / ".claude"
CLAUDE_CONFIG = HOME / ".claude.json"
CLAUDE_SETTINGS = CLAUDE_HOME / "settings.json"
CLAUDE_HOOKS_DIR = CLAUDE_HOME / "hooks"
GEMINI_SETTINGS = HOME / ".gemini" / "settings.json"
CODEX_CONFIG = HOME / ".codex" / "config.toml"
MANAGED_BLOCK_START = "# BEGIN CLAUDE-ORACLE MANAGED MCP"
MANAGED_BLOCK_END = "# END CLAUDE-ORACLE MANAGED MCP"
RTK_HOOK_MARKER = "rtk-rewrite"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_content_deployed(src: Path, dst: Path) -> bool:
    """Check that dst contains the source content (handles managed-block wrapping)."""
    if not dst.exists():
        return False
    return src.read_text(encoding="utf-8").strip() in dst.read_text(encoding="utf-8")


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


def load_server_config() -> dict:
    """Return a mode-aware dict describing the expected MCP server deployment."""
    manifest = load_json(RUNTIME / "semantic-mcp.json")
    server = manifest["semantic_mcp"]
    mode = server.get("mode", "stdio")
    cfg: dict = {
        "name": server["name"],
        "mode": mode,
        "codex_timeout": server.get("codex_startup_timeout_sec"),
    }
    if mode == "daemon":
        host = server.get("host", "127.0.0.1")
        port = server.get("port", 8800)
        mount = server.get("mount_path", "/mcp")
        cfg["url"] = f"http://{host}:{port}{mount}"
    else:
        cfg["command"] = str(Path(server["python_path"]).expanduser())
        cfg["env"] = {
            "PYTHONPATH": str(Path(server["repo_path"]).expanduser() / server["pythonpath"]),
            "EMBEDDING_MODEL": server.get("embedding_model", "jinaai/jina-code-embeddings-1.5b"),
            "SEMANTIC_DEVICE": server.get("semantic_device", "auto"),
            "LOG_FILE": str(Path(server["log_file"]).expanduser()),
        }
    return cfg


def main() -> None:
    checks: list[tuple[str, bool]] = []
    cfg = load_server_config()
    server_name: str = cfg["name"]
    mode: str = cfg["mode"]
    codex_timeout = cfg["codex_timeout"]

    # CLAUDE.md: source content must be present in deployed file (wrapped in managed block)
    checks.append((
        "claude-md",
        source_content_deployed(SOURCE / "CLAUDE.md", CLAUDE_HOME / "CLAUDE.md"),
    ))

    for src in sorted((SOURCE / "agents").glob("*.md")):
        dst = HOME / ".claude" / "agents" / src.name
        checks.append((f"agent:{src.name}", dst.exists() and sha256(src) == sha256(dst)))

    for src in sorted((SOURCE / "rules").glob("*.md")):
        dst = HOME / ".claude" / "rules" / src.name
        checks.append((f"rule:{src.name}", dst.exists() and sha256(src) == sha256(dst)))

    checks.extend(collect_skill_checks())
    checks.extend(collect_team_checks())

    # --- Claude MCP ---
    claude_config = load_json(CLAUDE_CONFIG)
    deployed_claude = claude_config.get("mcpServers", {}).get(server_name, {})
    checks.append(("claude-mcp", server_name in claude_config.get("mcpServers", {})))
    if mode == "daemon":
        checks.append(("claude-mcp-url", deployed_claude.get("url") == cfg["url"]))
    else:
        checks.append(("claude-mcp-command", deployed_claude.get("command") == cfg["command"]))
        checks.append(("claude-mcp-env", deployed_claude.get("env") == cfg["env"]))

    # --- Gemini ---
    checks.append(("gemini-md", (HOME / ".gemini" / "GEMINI.md").exists()))
    if GEMINI_SETTINGS.exists():
        gemini_settings = load_json(GEMINI_SETTINGS)
        deployed_gemini = gemini_settings.get("mcpServers", {}).get(server_name, {})
        checks.append(("gemini-mcp", server_name in gemini_settings.get("mcpServers", {})))
        if mode == "daemon":
            checks.append(("gemini-mcp-url", deployed_gemini.get("httpUrl") == cfg["url"]))
        else:
            checks.append(("gemini-mcp-command", deployed_gemini.get("command") == cfg["command"]))
            checks.append(("gemini-mcp-env", deployed_gemini.get("env") == cfg["env"]))
    else:
        checks.append(("gemini-mcp", False))
        checks.append(("gemini-mcp-url" if mode == "daemon" else "gemini-mcp-command", False))

    # --- Codex ---
    checks.append(("codex-agents", (HOME / ".codex" / "AGENTS.md").exists()))
    if CODEX_CONFIG.exists():
        codex_text = CODEX_CONFIG.read_text(encoding="utf-8")
        checks.append(("codex-mcp", MANAGED_BLOCK_START in codex_text and f"[mcp_servers.{server_name}]" in codex_text))
        if mode == "daemon":
            checks.append(("codex-mcp-url", f'url = "{cfg["url"]}"' in codex_text))
        else:
            checks.append(("codex-mcp-command", f'command = "{cfg["command"]}"' in codex_text))
            env_frags = [
                f'PYTHONPATH = "{cfg["env"]["PYTHONPATH"]}"',
                f'EMBEDDING_MODEL = "{cfg["env"]["EMBEDDING_MODEL"]}"',
                f'SEMANTIC_DEVICE = "{cfg["env"]["SEMANTIC_DEVICE"]}"',
                f'LOG_FILE = "{cfg["env"]["LOG_FILE"]}"',
            ]
            checks.append(("codex-mcp-env", all(f in codex_text for f in env_frags)))
        if codex_timeout is not None:
            checks.append(("codex-mcp-startup-timeout", f"startup_timeout_sec = {codex_timeout}" in codex_text))
    else:
        checks.append(("codex-mcp", False))
        checks.append(("codex-mcp-url" if mode == "daemon" else "codex-mcp-command", False))
        if codex_timeout is not None:
            checks.append(("codex-mcp-startup-timeout", False))

    # --- RTK ---
    checks.append(("rtk-binary", shutil.which("rtk") is not None))
    checks.append(("rtk-hook-script", (CLAUDE_HOOKS_DIR / "rtk-rewrite.sh").exists()))
    rtk_hook_wired = False
    if CLAUDE_SETTINGS.exists():
        try:
            settings = load_json(CLAUDE_SETTINGS)
            for entry in settings.get("hooks", {}).get("PreToolUse", []):
                for h in entry.get("hooks", []):
                    if RTK_HOOK_MARKER in h.get("command", ""):
                        rtk_hook_wired = True
        except (json.JSONDecodeError, KeyError):
            pass
    checks.append(("rtk-hook-wired", rtk_hook_wired))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{name}: {'OK' if ok else 'FAIL'}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
