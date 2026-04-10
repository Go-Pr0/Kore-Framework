from pathlib import Path

from abstract_fs_server.repo_paths import _resolve_repo_root_from_probes


def test_prefers_marked_project_root_over_home_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    project = home / "Documents" / "app"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'app'\n", encoding="utf-8")

    resolved = _resolve_repo_root_from_probes([home, project], home=home)

    assert resolved == str(project)


def test_skips_global_tool_dirs_when_project_probe_exists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    global_codex = home / ".codex"
    global_codex.mkdir()
    project = home / "Documents" / "service"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "package.json").write_text("{\"name\":\"service\"}\n", encoding="utf-8")

    resolved = _resolve_repo_root_from_probes([global_codex, src], home=home)

    assert resolved == str(project)


def test_keeps_non_repo_workspace_when_no_markers_exist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workspace = home / ".claude-oracle"
    workspace.mkdir()

    resolved = _resolve_repo_root_from_probes([workspace, home], home=home)

    assert resolved == str(workspace)


def test_ignores_home_level_markers_for_workspace_resolution(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "package.json").write_text("{\"name\":\"home-dotfiles\"}\n", encoding="utf-8")
    workspace = home / ".claude-oracle"
    workspace.mkdir()

    resolved = _resolve_repo_root_from_probes([workspace], home=home)

    assert resolved == str(workspace)
