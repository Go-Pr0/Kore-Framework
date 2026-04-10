#!/usr/bin/env python3
from __future__ import annotations

import json
import platform
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO / "source"
SOURCE = SOURCE_ROOT / "claude"
RUNTIME = SOURCE_ROOT / "runtime"
BACKUPS = REPO / "backups"

CLAUDE_HOME = HOME / ".claude"
GEMINI_HOME = HOME / ".gemini"
CODEX_HOME = HOME / ".codex"
CLAUDE_CONFIG = HOME / ".claude.json"
CLAUDE_SETTINGS = CLAUDE_HOME / "settings.json"
CLAUDE_HOOKS_DIR = CLAUDE_HOME / "hooks"
GEMINI_SETTINGS = GEMINI_HOME / "settings.json"
CODEX_CONFIG = CODEX_HOME / "config.toml"
SYSTEMD_UNIT_DIR = HOME / ".config" / "systemd" / "user"
SYSTEMD_UNIT = SYSTEMD_UNIT_DIR / "abstract-fs.service"

# RTK hook command — calls the vendored script deployed to ~/.claude/hooks/
RTK_HOOK_COMMAND = "bash ~/.claude/hooks/rtk-rewrite.sh"
RTK_HOOK_MARKER = "rtk-rewrite"

MANAGED_BLOCK_START = "# BEGIN CLAUDE-ORACLE MANAGED MCP"
MANAGED_BLOCK_END = "# END CLAUDE-ORACLE MANAGED MCP"

CLAUDE_MD_BLOCK_START = "<!-- BEGIN CLAUDE-ORACLE MANAGED -->"
CLAUDE_MD_BLOCK_END = "<!-- END CLAUDE-ORACLE MANAGED -->"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content, encoding="utf-8")


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, content: dict) -> None:
    write_text(path, json.dumps(content, indent=2, ensure_ascii=True) + "\n")


def backup_file(path: Path, root: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    dest = backup_root / path.relative_to(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def backup_dir_md_files(path: Path, root: Path, backup_root: Path) -> None:
    if not path.is_dir():
        return
    for file_path in sorted(path.glob("*.md")):
        backup_file(file_path, root, backup_root)


def sync_md_dir(src_dir: Path, dst_dir: Path, root: Path, backup_root: Path) -> None:
    """Additive sync: copies oracle files into dst_dir, never deletes user files."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    backup_dir_md_files(dst_dir, root, backup_root)
    for src_file in sorted(src_dir.glob("*.md")):
        shutil.copy2(src_file, dst_dir / src_file.name)


def backup_tree(path: Path, root: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            backup_file(file_path, root, backup_root)


def sync_skill_dir(src_dir: Path, dst_dir: Path, root: Path, backup_root: Path) -> None:
    """Additive sync: copies oracle skill dirs into dst_dir, never deletes user skill dirs."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    backup_tree(dst_dir, root, backup_root)

    if not src_dir.is_dir():
        return

    for skill_src in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        skill_dst = dst_dir / skill_src.name
        if skill_dst.exists():
            shutil.rmtree(skill_dst)
        shutil.copytree(skill_src, skill_dst)


def apply_removals(removed_dir: Path, dst_root: Path) -> None:
    """Delete skills/agents listed under removed/ from the destination.

    removed/skills/<name>/  → deletes ~/.claude/skills/<name>/
    removed/agents/<name>.md → deletes ~/.claude/agents/<name>.md
    """
    if not removed_dir.is_dir():
        return

    removed_skills = removed_dir / "skills"
    if removed_skills.is_dir():
        dst_skills = dst_root / "skills"
        for marker in sorted(p for p in removed_skills.iterdir() if p.is_dir()):
            target = dst_skills / marker.name
            if target.exists():
                shutil.rmtree(target)

    removed_agents = removed_dir / "agents"
    if removed_agents.is_dir():
        dst_agents = dst_root / "agents"
        for marker in sorted(removed_agents.glob("*.md")):
            target = dst_agents / marker.name
            if target.exists():
                target.unlink()


def expand_path(raw: str) -> Path:
    return Path(raw).expanduser()


def load_runtime_manifest() -> dict:
    return load_json(RUNTIME / "semantic-mcp.json")


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file; ignore comments and blanks."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def resolve_semantic_device(raw: str | None) -> str:
    value = (raw or "auto").strip().lower()
    if value != "auto":
        return value
    system = platform.system().lower()
    if system == "darwin":
        return "mps" if platform.machine().lower() in ("arm64", "aarch64") else "cpu"
    # Linux: leave as cuda by default — the abstract-fs server falls back to
    # CPU internally if torch.cuda.is_available() is False.
    return "cuda"


# Keys from .env that should be forwarded to the MCP server process.
_HF_ENV_KEYS = ("HF_HUB_CACHE", "HF_TOKEN", "TRANSFORMERS_OFFLINE", "HF_HUB_OFFLINE")

# HF env keys that become extra Environment= lines in the systemd unit (excluding
# HF_HUB_CACHE which always has its own dedicated line in the template).
_HF_EXTRA_SERVICE_KEYS = ("HF_TOKEN", "TRANSFORMERS_OFFLINE", "HF_HUB_OFFLINE")


@dataclass
class McpEmission:
    server_name: str
    mode: str                    # "daemon" or "stdio"
    claude_config: dict          # goes straight under mcpServers[name]
    gemini_config: dict
    codex_toml_lines: list[str]  # TOML lines inside the managed block
    codex_options: dict          # startup_timeout_sec etc.
    # stdio-only: the raw env dict built from repo .env (used by install_systemd_unit)
    _server_env: dict = field(default_factory=dict, repr=False)
    # full manifest server section, kept for systemd rendering
    _manifest_server: dict = field(default_factory=dict, repr=False)


def build_semantic_mcp_config() -> McpEmission:
    manifest = load_runtime_manifest()
    server = manifest["semantic_mcp"]
    server_name = server["name"]
    mode = server.get("mode", "stdio")

    codex_options: dict[str, int] = {}
    if "codex_startup_timeout_sec" in server:
        codex_options["startup_timeout_sec"] = int(server["codex_startup_timeout_sec"])

    if mode == "daemon":
        host = server.get("host", "127.0.0.1")
        port = server.get("port", 8800)
        mount_path = server.get("mount_path", "/mcp")
        url = f"http://{host}:{port}{mount_path}"

        claude_config: dict = {"type": "http", "url": url}
        gemini_config: dict = {"httpUrl": url}
        codex_toml_lines: list[str] = [
            f"[mcp_servers.{server_name}]",
            f"url = {render_toml_string(url)}",
        ]

        return McpEmission(
            server_name=server_name,
            mode=mode,
            claude_config=claude_config,
            gemini_config=gemini_config,
            codex_toml_lines=codex_toml_lines,
            codex_options=codex_options,
            _manifest_server=server,
        )

    # stdio mode — keep original behaviour exactly
    repo_path = expand_path(server["repo_path"])
    python_path = expand_path(server["python_path"])
    log_file = expand_path(server["log_file"])
    embedding_model = server.get("embedding_model", "jinaai/jina-code-embeddings-1.5b")
    env: dict[str, str] = {
        "PYTHONPATH": str(repo_path / server["pythonpath"]),
        "EMBEDDING_MODEL": embedding_model,
        "SEMANTIC_DEVICE": resolve_semantic_device(server.get("semantic_device")),
        "LOG_FILE": str(log_file),
    }

    dot_env = load_env_file(REPO / ".env")
    for key in _HF_ENV_KEYS:
        if key in dot_env:
            value = dot_env[key]
            env[key] = str(Path(value).expanduser()) if value.startswith("~") else value

    stdio_config: dict = {
        "command": str(python_path),
        "args": ["-m", server["module"]],
        "env": env,
    }
    codex_toml_lines = [
        f"[mcp_servers.{server_name}]",
        f"command = {render_toml_string(stdio_config['command'])}",
        f"args = {render_toml_array(stdio_config['args'])}",
        f"env = {render_toml_inline_table(stdio_config['env'])}",
    ]

    return McpEmission(
        server_name=server_name,
        mode=mode,
        claude_config=stdio_config,
        gemini_config=stdio_config,
        codex_toml_lines=codex_toml_lines,
        codex_options=codex_options,
        _server_env=env,
        _manifest_server=server,
    )


def update_claude_config(server_name: str, server_config: dict) -> None:
    data = load_json(CLAUDE_CONFIG)
    data.setdefault("mcpServers", {})
    data["mcpServers"][server_name] = server_config

    projects = data.setdefault("projects", {})
    home_key = str(HOME)
    home_project = projects.setdefault(home_key, {})
    home_project.setdefault("mcpServers", {})
    home_project["mcpServers"][server_name] = server_config

    write_json(CLAUDE_CONFIG, data)


def render_toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_toml_array(values: list[str]) -> str:
    return "[" + ", ".join(render_toml_string(value) for value in values) + "]"


def render_toml_inline_table(values: dict[str, str]) -> str:
    items = ", ".join(f"{key} = {render_toml_string(value)}" for key, value in values.items())
    return "{ " + items + " }"


def replace_managed_block(existing: str, block: str) -> str:
    if MANAGED_BLOCK_START in existing and MANAGED_BLOCK_END in existing:
        start = existing.index(MANAGED_BLOCK_START)
        end = existing.index(MANAGED_BLOCK_END) + len(MANAGED_BLOCK_END)
        prefix = existing[:start].rstrip()
        suffix = existing[end:].lstrip()
        parts = [part for part in [prefix, block, suffix] if part]
        return "\n\n".join(parts) + "\n"
    stripped = existing.rstrip()
    if stripped:
        return stripped + "\n\n" + block + "\n"
    return block + "\n"


def update_codex_config(
    server_name: str,
    codex_toml_lines: list[str],
    codex_options: dict,
) -> None:
    existing = read_text(CODEX_CONFIG) if CODEX_CONFIG.exists() else ""
    lines = [MANAGED_BLOCK_START] + codex_toml_lines
    if "startup_timeout_sec" in codex_options:
        lines.append(f"startup_timeout_sec = {codex_options['startup_timeout_sec']}")
    lines.append(MANAGED_BLOCK_END)
    block = "\n".join(lines)
    write_text(CODEX_CONFIG, replace_managed_block(existing, block))


def update_gemini_settings(server_name: str, server_config: dict) -> None:
    data = load_json(GEMINI_SETTINGS)
    data.setdefault("mcpServers", {})
    data["mcpServers"][server_name] = server_config
    write_json(GEMINI_SETTINGS, data)


def install_systemd_unit(emission: McpEmission, backup_root: Path) -> None:
    """Render abstract-fs.service from template and install to systemd user dir."""
    template_path = RUNTIME / "abstract-fs.service.template"
    if not template_path.exists():
        raise SystemExit(f"Missing service template: {template_path}")

    server = emission._manifest_server
    dot_env = load_env_file(REPO / ".env")

    # Resolve substitution values
    repo_path = expand_path(server["repo_path"])
    python_path = str(expand_path(server["python_path"]))
    pythonpath = str(repo_path / server["pythonpath"])
    log_file = str(expand_path(server["log_file"]))
    host = server.get("host", "127.0.0.1")
    port = str(server.get("port", 8800))
    embedding_model = server.get("embedding_model", "jinaai/jina-code-embeddings-1.5b")
    semantic_device = resolve_semantic_device(server.get("semantic_device"))

    # HF_HUB_CACHE: prefer repo .env, fall back to systemd %h specifier
    raw_hf_cache = dot_env.get("HF_HUB_CACHE", "")
    if raw_hf_cache:
        hf_hub_cache = str(Path(raw_hf_cache).expanduser()) if raw_hf_cache.startswith("~") else raw_hf_cache
    else:
        hf_hub_cache = "%h/.cache/huggingface/hub"

    # Extra HF env lines (HF_TOKEN, TRANSFORMERS_OFFLINE, HF_HUB_OFFLINE when set)
    hf_extra_lines: list[str] = []
    for key in _HF_EXTRA_SERVICE_KEYS:
        if key in dot_env and dot_env[key]:
            value = dot_env[key]
            value = str(Path(value).expanduser()) if value.startswith("~") else value
            hf_extra_lines.append(f"Environment={key}={value}")
    hf_extras = ("\n".join(hf_extra_lines) + "\n") if hf_extra_lines else ""

    template = read_text(template_path)
    rendered = template.format(
        HOST=host,
        PORT=port,
        SEMANTIC_DEVICE=semantic_device,
        EMBEDDING_MODEL=embedding_model,
        HF_HUB_CACHE=hf_hub_cache,
        PYTHONPATH=pythonpath,
        LOG_FILE=log_file,
        PYTHON_PATH=python_path,
        HF_EXTRAS=hf_extras,
    )

    # Back up existing unit if present
    backup_file(SYSTEMD_UNIT, SYSTEMD_UNIT_DIR, backup_root / "systemd")

    write_text(SYSTEMD_UNIT, rendered)


def inject_claude_md_block(existing: str, oracle_content: str) -> str:
    """Inject oracle content into a managed block, preserving surrounding user content."""
    block = f"{CLAUDE_MD_BLOCK_START}\n{oracle_content.strip()}\n{CLAUDE_MD_BLOCK_END}"
    if CLAUDE_MD_BLOCK_START in existing and CLAUDE_MD_BLOCK_END in existing:
        start = existing.index(CLAUDE_MD_BLOCK_START)
        end = existing.index(CLAUDE_MD_BLOCK_END) + len(CLAUDE_MD_BLOCK_END)
        prefix = existing[:start].rstrip()
        suffix = existing[end:].lstrip()
        parts = [part for part in [prefix, block, suffix] if part]
        return "\n\n".join(parts) + "\n"
    stripped = existing.rstrip()
    if stripped:
        return stripped + "\n\n" + block + "\n"
    return block + "\n"


def generated_header(target_name: str) -> str:
    return "\n".join([
        f"<!-- GENERATED FILE: {target_name} -->",
        "<!-- Source of truth: ~/.claude-oracle/source/claude -->",
        "<!-- Claude is the oracle. Do not edit this file directly. -->",
        "",
    ])


def adapt_for_target(claude_md: str, target: str) -> str:
    text = claude_md
    if target == "gemini":
        text = text.replace("Claude Code sessions", "Gemini CLI sessions")
        text = text.replace("the global `~/.claude/CLAUDE.md` file", "the generated global `~/.gemini/GEMINI.md` file")
        text = text.replace(
            "- Global behavior comes from this file plus the oracle-managed agent files in `~/.claude/agents/`, the oracle-managed rule files in `~/.claude/rules/`, and the oracle-managed skills in `~/.claude/skills/`.",
            "- Global behavior comes from this generated file. Claude remains the oracle, and this file is a synced output for Gemini.",
        )
        text = text.replace(
            "- The canonical source for this global setup is `~/.claude-oracle/source/`.",
            "- The canonical source remains `~/.claude-oracle/source/`.",
        )
        text = text.replace(
            "- `~/.claude/teams/` is Claude-only team state and team documentation. Runtime entries may be created there during native team runs.",
            "- Claude-specific team runtime stays local to Claude and is not synced into Gemini.",
        )
        text = text.replace(
            "- `~/.claude/skills/` contains explicitly callable global skills such as `/team-lead`.",
            "- Gemini-specific runtime state stays local to Gemini.",
        )
    elif target == "codex":
        text = text.replace("Claude Code sessions", "Codex sessions")
        text = text.replace("the global `~/.claude/CLAUDE.md` file", "the generated global `~/.codex/AGENTS.md` file")
        text = text.replace(
            "- Global behavior comes from this file plus the oracle-managed agent files in `~/.claude/agents/`, the oracle-managed rule files in `~/.claude/rules/`, and the oracle-managed skills in `~/.claude/skills/`.",
            "- Global behavior comes from this generated file. Claude remains the oracle, and this file is a synced output for Codex.",
        )
        text = text.replace(
            "- The canonical source for this global setup is `~/.claude-oracle/source/`.",
            "- The canonical source remains `~/.claude-oracle/source/`.",
        )
        text = text.replace(
            "- `~/.claude/teams/` is Claude-only team state and team documentation. Runtime entries may be created there during native team runs.",
            "- Claude-specific team runtime stays local to Claude and is not synced into Codex.",
        )
        text = text.replace(
            "- `~/.claude/skills/` contains explicitly callable global skills such as `/team-lead`.",
            "- Codex-specific runtime state stays local to Codex.",
        )
    return text



def deploy_rtk_hook_script() -> None:
    """Copy the vendored rtk-rewrite.sh into ~/.claude/hooks/."""
    src = RUNTIME / "rtk-rewrite.sh"
    if not src.exists():
        raise SystemExit(f"Missing RTK hook script: {src}")
    CLAUDE_HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    dst = CLAUDE_HOOKS_DIR / "rtk-rewrite.sh"
    shutil.copy2(src, dst)
    dst.chmod(0o755)


def inject_rtk_hook(settings_path: Path) -> None:
    """Merge the RTK PreToolUse hook into ~/.claude/settings.json non-destructively."""
    data = load_json(settings_path) if settings_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    pre_hooks = hooks.setdefault("PreToolUse", [])

    # Idempotent: skip if already wired
    for entry in pre_hooks:
        for h in entry.get("hooks", []):
            if RTK_HOOK_MARKER in h.get("command", ""):
                return

    rtk_entry = {
        "matcher": "Bash",
        "hooks": [
            {
                "type": "command",
                "command": RTK_HOOK_COMMAND,
                "timeout": 10,
                "statusMessage": "RTK: compressing bash output...",
            }
        ],
    }
    pre_hooks.insert(0, rtk_entry)
    write_json(settings_path, data)


def main(dry_run: bool = False) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = BACKUPS / timestamp
    backup_root.mkdir(parents=True, exist_ok=True)

    source_claude_md = SOURCE / "CLAUDE.md"
    source_agents = SOURCE / "agents"
    source_rules = SOURCE / "rules"
    source_skills = SOURCE / "skills"
    source_teams = SOURCE / "teams"

    if not source_claude_md.is_file():
        raise SystemExit(f"Missing source file: {source_claude_md}")

    claude_md = read_text(source_claude_md)
    emission = build_semantic_mcp_config()

    if dry_run:
        print("=== DRY RUN — no files written ===")
        print(f"mode: {emission.mode}")
        print(f"server_name: {emission.server_name}")
        print(f"claude_config:  {json.dumps(emission.claude_config, indent=2)}")
        print(f"gemini_config:  {json.dumps(emission.gemini_config, indent=2)}")
        print("codex_toml_lines:")
        for line in [MANAGED_BLOCK_START] + emission.codex_toml_lines + [MANAGED_BLOCK_END]:
            print(f"  {line}")
        if emission.mode == "daemon":
            template_path = RUNTIME / "abstract-fs.service.template"
            if template_path.exists():
                server = emission._manifest_server
                dot_env = load_env_file(REPO / ".env")
                repo_path = expand_path(server["repo_path"])
                raw_hf_cache = dot_env.get("HF_HUB_CACHE", "")
                hf_hub_cache = (
                    str(Path(raw_hf_cache).expanduser()) if raw_hf_cache.startswith("~") else raw_hf_cache
                ) if raw_hf_cache else "%h/.cache/huggingface/hub"
                hf_extra_lines = []
                for key in _HF_EXTRA_SERVICE_KEYS:
                    if key in dot_env and dot_env[key]:
                        value = dot_env[key]
                        hf_extra_lines.append(f"Environment={key}={value}")
                hf_extras = ("\n".join(hf_extra_lines) + "\n") if hf_extra_lines else ""
                template = read_text(template_path)
                rendered = template.format(
                    HOST=server.get("host", "127.0.0.1"),
                    PORT=str(server.get("port", 8800)),
                    SEMANTIC_DEVICE=resolve_semantic_device(server.get("semantic_device")),
                    EMBEDDING_MODEL=server.get("embedding_model", "jinaai/jina-code-embeddings-1.5b"),
                    HF_HUB_CACHE=hf_hub_cache,
                    PYTHONPATH=str(repo_path / server["pythonpath"]),
                    LOG_FILE=str(expand_path(server["log_file"])),
                    PYTHON_PATH=str(expand_path(server["python_path"])),
                    HF_EXTRAS=hf_extras,
                )
                print("\n=== Rendered abstract-fs.service ===")
                print(rendered)
        return

    backup_file(CLAUDE_HOME / "CLAUDE.md", CLAUDE_HOME, backup_root / "claude-home")
    backup_dir_md_files(CLAUDE_HOME / "agents", CLAUDE_HOME, backup_root / "claude-home")
    backup_dir_md_files(CLAUDE_HOME / "rules", CLAUDE_HOME, backup_root / "claude-home")
    backup_tree(CLAUDE_HOME / "skills", CLAUDE_HOME, backup_root / "claude-home")
    backup_dir_md_files(CLAUDE_HOME / "teams", CLAUDE_HOME, backup_root / "claude-home")
    backup_file(CLAUDE_SETTINGS, CLAUDE_HOME, backup_root / "claude-home")
    backup_file(GEMINI_HOME / "GEMINI.md", GEMINI_HOME, backup_root / "gemini-home")
    backup_file(GEMINI_SETTINGS, GEMINI_HOME, backup_root / "gemini-home")
    backup_file(CODEX_HOME / "AGENTS.md", CODEX_HOME, backup_root / "codex-home")
    backup_file(CODEX_CONFIG, CODEX_HOME, backup_root / "codex-home")
    backup_file(CLAUDE_CONFIG, HOME, backup_root / "home")

    claude_md_path = CLAUDE_HOME / "CLAUDE.md"
    existing_claude_md = read_text(claude_md_path) if claude_md_path.exists() else ""
    write_text(claude_md_path, inject_claude_md_block(existing_claude_md, claude_md))

    sync_md_dir(source_agents, CLAUDE_HOME / "agents", CLAUDE_HOME, backup_root / "claude-home")
    sync_md_dir(source_rules, CLAUDE_HOME / "rules", CLAUDE_HOME, backup_root / "claude-home")
    sync_skill_dir(source_skills, CLAUDE_HOME / "skills", CLAUDE_HOME, backup_root / "claude-home")
    apply_removals(SOURCE / "removed", CLAUDE_HOME)
    sync_md_dir(source_teams, CLAUDE_HOME / "teams", CLAUDE_HOME, backup_root / "claude-home")
    update_claude_config(emission.server_name, emission.claude_config)

    gemini_md_path = GEMINI_HOME / "GEMINI.md"
    existing_gemini_md = read_text(gemini_md_path) if gemini_md_path.exists() else ""
    write_text(gemini_md_path, inject_claude_md_block(existing_gemini_md, adapt_for_target(claude_md, "gemini")))
    update_gemini_settings(emission.server_name, emission.gemini_config)

    codex_agents_path = CODEX_HOME / "AGENTS.md"
    existing_codex_md = read_text(codex_agents_path) if codex_agents_path.exists() else ""
    write_text(codex_agents_path, inject_claude_md_block(existing_codex_md, adapt_for_target(claude_md, "codex")))
    update_codex_config(emission.server_name, emission.codex_toml_lines, emission.codex_options)

    deploy_rtk_hook_script()
    inject_rtk_hook(CLAUDE_SETTINGS)

    if emission.mode == "daemon":
        install_systemd_unit(emission, backup_root)

    print(f"Synced Claude oracle from {REPO}")
    print(f"Backup: {backup_root}")
    print("Updated targets:")
    print(f"- {CLAUDE_HOME / 'CLAUDE.md'}")
    print(f"- {CLAUDE_HOME / 'agents'}")
    print(f"- {CLAUDE_HOME / 'rules'}")
    print(f"- {CLAUDE_HOME / 'skills'}")
    print(f"- {CLAUDE_HOME / 'teams'}")
    print(f"- {CLAUDE_CONFIG}")
    print(f"- {GEMINI_HOME / 'GEMINI.md'}")
    print(f"- {GEMINI_SETTINGS}")
    print(f"- {CODEX_HOME / 'AGENTS.md'}")
    print(f"- {CODEX_CONFIG}")
    print(f"- {CLAUDE_SETTINGS} (RTK PreToolUse hook)")
    print(f"- {CLAUDE_HOOKS_DIR / 'rtk-rewrite.sh'}")

    if emission.mode == "daemon":
        print(f"- {SYSTEMD_UNIT}")
        print()
        print("Daemon mode enabled. To start the server:")
        print("    systemctl --user daemon-reload")
        print("    systemctl --user enable --now abstract-fs.service")
        print("    systemctl --user status abstract-fs.service")
        print("Logs: journalctl --user -u abstract-fs.service -f")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
