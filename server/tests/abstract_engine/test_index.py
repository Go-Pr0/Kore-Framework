"""Tests for AbstractIndex — load_or_build, tiers, persistence, and incremental updates."""

from __future__ import annotations

import json
import os
import time

import pytest

from abstract_engine.index import AbstractIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_PY = """\
\"\"\"A simple module.\"\"\"


def hello(name: str) -> str:
    \"\"\"Say hello.\"\"\"
    return f"Hello, {name}"


def _hidden() -> None:
    pass
"""

OTHER_PY = """\
\"\"\"Other module.\"\"\"

MAX_SIZE = 100


def compute(x: int) -> int:
    \"\"\"Compute something.\"\"\"
    return x * 2
"""


@pytest.fixture
def project_dir(tmp_path):
    """A temporary project with a Python file."""
    (tmp_path / "mod.py").write_text(SIMPLE_PY, encoding="utf-8")
    return tmp_path


@pytest.fixture
def index(project_dir) -> AbstractIndex:
    """Cold-start index over the sample project."""
    return AbstractIndex.load_or_build(str(project_dir))


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------

class TestColdStart:
    def test_creates_index_from_scratch(self, project_dir) -> None:
        idx = AbstractIndex.load_or_build(str(project_dir))
        assert len(idx.files) == 1

    def test_python_file_present(self, index) -> None:
        paths = set(index.files.keys())
        assert any("mod.py" in p for p in paths)

    def test_project_root_set(self, project_dir, index) -> None:
        assert index.project_root == str(project_dir)

    def test_languages_detected(self, index) -> None:
        assert "python" in index.languages

    def test_function_lookup_populated(self, index) -> None:
        assert "hello" in index.function_lookup

    def test_content_hash_set(self, index) -> None:
        for entry in index.files.values():
            assert entry.content_hash != ""


# ---------------------------------------------------------------------------
# Tier 1
# ---------------------------------------------------------------------------

class TestGetTier1:
    def test_returns_cached_text(self, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t1 = index.get_tier1(py_path)
        assert isinstance(t1, str)
        assert len(t1) > 0

    def test_includes_function_name(self, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t1 = index.get_tier1(py_path)
        assert "hello" in t1

    def test_missing_path_returns_error_message(self, index) -> None:
        t1 = index.get_tier1("nonexistent/file.py")
        assert "[ERROR]" in t1

    def test_get_all_tier1_includes_all_files(self, index) -> None:
        all_t1 = index.get_all_tier1()
        assert "mod.py" in all_t1


# ---------------------------------------------------------------------------
# Tier 2
# ---------------------------------------------------------------------------

class TestGetTier2:
    def test_returns_function_detail(self, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t2 = index.get_tier2(py_path, "hello")
        assert isinstance(t2, str)
        assert "hello" in t2

    def test_includes_context_header(self, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t2 = index.get_tier2(py_path, "hello")
        assert "context for" in t2

    def test_missing_function_returns_error(self, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t2 = index.get_tier2(py_path, "nonexistent_fn")
        assert "[ERROR]" in t2

    def test_missing_file_returns_error(self, index) -> None:
        t2 = index.get_tier2("ghost.py", "foo")
        assert "[ERROR]" in t2

    def test_method_lookup_with_class_name(self, tmp_path) -> None:
        src = """\
class Calc:
    def add(self, x: int, y: int) -> int:
        \"\"\"Add two numbers.\"\"\"
        return x + y
"""
        (tmp_path / "calc.py").write_text(src, encoding="utf-8")
        idx = AbstractIndex.load_or_build(str(tmp_path))
        t2 = idx.get_tier2("calc.py", "add", class_name="Calc")
        assert "add" in t2
        assert "[ERROR]" not in t2


# ---------------------------------------------------------------------------
# Tier 3
# ---------------------------------------------------------------------------

class TestGetTier3:
    def test_returns_fresh_source(self, project_dir, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t3 = index.get_tier3(py_path, "hello")
        assert "def hello" in t3
        assert "return" in t3

    def test_reflects_current_disk_state(self, project_dir, index) -> None:
        """Tier 3 should read fresh from disk even if index is stale."""
        py_path = next(p for p in index.files if p.endswith(".py"))
        abs_path = os.path.join(str(project_dir), py_path)

        new_src = SIMPLE_PY.replace("Hello, {name}", "Hi, {name}")
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(new_src)

        t3 = index.get_tier3(py_path, "hello")
        assert "Hi, {name}" in t3

    def test_missing_function_returns_error(self, project_dir, index) -> None:
        py_path = next(p for p in index.files if p.endswith(".py"))
        t3 = index.get_tier3(py_path, "totally_missing")
        assert "[ERROR]" in t3

    def test_missing_file_returns_error(self, index) -> None:
        t3 = index.get_tier3("does_not_exist.py", "foo")
        assert "[ERROR]" in t3


# ---------------------------------------------------------------------------
# Persistence — save/load round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_to_disk(self, project_dir, index) -> None:
        index.save_to_disk()
        index_file = os.path.join(str(project_dir), ".abstract-index.json")
        assert os.path.isfile(index_file)

    def test_load_from_disk(self, project_dir, index) -> None:
        index.save_to_disk()
        index_file = os.path.join(str(project_dir), ".abstract-index.json")
        loaded = AbstractIndex.load_from_disk(index_file)
        assert loaded.project_root == index.project_root

    def test_round_trip_files_match(self, project_dir, index) -> None:
        index.save_to_disk()
        index_file = os.path.join(str(project_dir), ".abstract-index.json")
        loaded = AbstractIndex.load_from_disk(index_file)

        # All file paths should be present after loading
        assert set(loaded.files.keys()) == set(index.files.keys())

    def test_round_trip_tier1_matches(self, project_dir, index) -> None:
        index.save_to_disk()
        index_file = os.path.join(str(project_dir), ".abstract-index.json")
        loaded = AbstractIndex.load_from_disk(index_file)

        for path in index.files:
            assert index.get_tier1(path) == loaded.get_tier1(path)

    def test_warm_start_loads_existing(self, project_dir, index) -> None:
        """load_or_build should use the saved index on second call."""
        index.save_to_disk()
        idx2 = AbstractIndex.load_or_build(str(project_dir))
        assert set(idx2.files.keys()) == set(index.files.keys())

    def test_load_from_disk_rebuilds_stale_call_metadata(self, tmp_path) -> None:
        (tmp_path / "helper.py").write_text(
            "def target():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        (tmp_path / "caller.py").write_text(
            "def run():\n"
            "    return target()\n",
            encoding="utf-8",
        )
        idx = AbstractIndex.load_or_build(str(tmp_path), config={"languages": ["python"]})
        idx.save_to_disk()

        index_file = tmp_path / ".abstract-index.json"
        data = json.loads(index_file.read_text(encoding="utf-8"))
        run_call = data["files"]["caller.py"]["functions"]["run"]["calls"][0]
        run_call["resolved_file"] = "deleted.py"
        run_call["resolved_qualified_name"] = "stale.target"
        data["files"]["helper.py"]["functions"]["target"]["called_by"] = [
            {"caller_name": "stale", "caller_file": "deleted.py"}
        ]
        index_file.write_text(json.dumps(data), encoding="utf-8")

        loaded = AbstractIndex.load_from_disk(str(index_file))
        call = loaded.files["caller.py"].functions["run"].calls[0]
        called_by = loaded.files["helper.py"].functions["target"].called_by

        assert call.resolved_file == "helper.py"
        assert call.resolved_qualified_name == "target"
        assert len(called_by) == 1
        assert called_by[0].caller_name == "run"
        assert called_by[0].caller_file == "caller.py"

    def test_schema_version_persisted(self, project_dir, index) -> None:
        from abstract_engine.models import SCHEMA_VERSION

        index.save_to_disk()
        index_file = os.path.join(str(project_dir), ".abstract-index.json")
        loaded = AbstractIndex.load_from_disk(index_file)
        assert loaded.schema_version == SCHEMA_VERSION

    def test_custom_index_path_used_for_save(self, project_dir) -> None:
        custom_path = os.path.join(str(project_dir), "cache", "custom-index.json")

        idx = AbstractIndex.load_or_build(
            str(project_dir),
            config={"index_path": custom_path},
        )
        idx.save_to_disk()

        assert os.path.isfile(custom_path)
        assert not os.path.isfile(os.path.join(str(project_dir), ".abstract-index.json"))


# ---------------------------------------------------------------------------
# Incremental updates
# ---------------------------------------------------------------------------

class TestIncrementalUpdate:
    def test_modified_file_reparsed(self, project_dir) -> None:
        """Modifying a file causes it to be re-parsed on warm start."""
        idx = AbstractIndex.load_or_build(str(project_dir))
        idx.save_to_disk()

        py_path = next(p for p in idx.files if p.endswith(".py"))
        abs_path = os.path.join(str(project_dir), py_path)

        # Write new content (different hash)
        new_content = SIMPLE_PY + "\ndef new_function() -> None:\n    pass\n"
        # Ensure mtime changes by sleeping briefly or forcing a different mtime
        time.sleep(0.01)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(new_content)

        idx2 = AbstractIndex.load_or_build(str(project_dir))
        entry = idx2.files.get(py_path)
        assert entry is not None
        assert "new_function" in entry.functions

    def test_deleted_file_removed(self, project_dir) -> None:
        """Deleting a file removes it from the index on warm start."""
        # Add a second Python file so we can delete one
        extra_file = project_dir / "extra.py"
        extra_file.write_text(OTHER_PY, encoding="utf-8")

        idx = AbstractIndex.load_or_build(str(project_dir))
        idx.save_to_disk()

        abs_path = str(extra_file)
        os.remove(abs_path)

        idx2 = AbstractIndex.load_or_build(str(project_dir))
        assert "extra.py" not in idx2.files

    def test_added_file_indexed(self, project_dir) -> None:
        """Adding a new file picks it up on warm start."""
        idx = AbstractIndex.load_or_build(str(project_dir))
        idx.save_to_disk()

        new_file = project_dir / "other.py"
        new_file.write_text(OTHER_PY, encoding="utf-8")

        idx2 = AbstractIndex.load_or_build(str(project_dir))
        assert "other.py" in idx2.files
        assert "compute" in idx2.files["other.py"].functions

    def test_remove_file_rebuilds_lookup_and_call_graph(self, tmp_path) -> None:
        (tmp_path / "caller.py").write_text(
            "from helper import target\n\n"
            "def run():\n"
            "    return target()\n",
            encoding="utf-8",
        )
        (tmp_path / "helper.py").write_text(
            "def target():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        idx = AbstractIndex.load_or_build(str(tmp_path))
        assert "target" in idx.function_lookup
        assert idx.files["helper.py"].functions["target"].called_by

        idx.remove_file("helper.py")

        assert "helper.py" not in idx.files
        assert "target" not in idx.function_lookup
        call = idx.files["caller.py"].functions["run"].calls[0]
        assert call.is_external is True
        assert call.resolved_file is None


# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

class TestLanguageConfiguration:
    def test_python_only_language_config(self, project_dir) -> None:
        idx = AbstractIndex.load_or_build(
            str(project_dir),
            config={"languages": ["python"]},
        )

        assert "mod.py" in idx.files
        assert idx.languages == ["python"]


# ---------------------------------------------------------------------------
# Excluded directories
# ---------------------------------------------------------------------------

class TestExcludedDirectories:
    def _assert_excluded(self, project_dir, dirname: str) -> None:
        excl_dir = project_dir / dirname
        excl_dir.mkdir()
        (excl_dir / "hidden.py").write_text("def secret(): pass\n", encoding="utf-8")
        idx = AbstractIndex.load_or_build(str(project_dir))
        excluded_paths = [p for p in idx.files if dirname in p]
        assert excluded_paths == [], f"Expected {dirname} to be excluded but found: {excluded_paths}"

    def test_node_modules_excluded(self, project_dir) -> None:
        self._assert_excluded(project_dir, "node_modules")

    def test_pycache_excluded(self, project_dir) -> None:
        self._assert_excluded(project_dir, "__pycache__")

    def test_venv_excluded(self, project_dir) -> None:
        self._assert_excluded(project_dir, ".venv")

    def test_git_excluded(self, project_dir) -> None:
        self._assert_excluded(project_dir, ".git")

    def test_build_excluded(self, project_dir) -> None:
        self._assert_excluded(project_dir, "build")
