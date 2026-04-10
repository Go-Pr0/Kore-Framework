"""Tests for call graph resolution and called_by index building."""

from __future__ import annotations

from abstract_engine.call_graph import build_function_lookup, resolve_call_graph
from abstract_engine.models import CallEntry, ClassEntry, FileEntry, FunctionEntry, ImportEntry


def _make_file(
    path: str,
    functions: dict[str, FunctionEntry] | None = None,
    classes: dict[str, ClassEntry] | None = None,
) -> FileEntry:
    return FileEntry(
        relative_path=path,
        language="python",
        line_count=10,
        functions=functions or {},
        classes=classes or {},
    )


def _make_func(
    name: str,
    qualified_name: str = "",
    calls: list[CallEntry] | None = None,
    file_path: str = "",
) -> FunctionEntry:
    return FunctionEntry(
        name=name,
        qualified_name=qualified_name or name,
        file_path=file_path,
        calls=calls or [],
    )


class TestBuildFunctionLookup:
    def test_top_level_functions_indexed(self) -> None:
        func = _make_func("process", file_path="a.py")
        files = {"a.py": _make_file("a.py", functions={"process": func})}
        lookup = build_function_lookup(files)
        assert "process" in lookup
        assert lookup["process"][0].file_path == "a.py"

    def test_class_methods_indexed(self) -> None:
        method = _make_func("compute", qualified_name="Calc.compute", file_path="calc.py")
        cls = ClassEntry(name="Calc", methods={"compute": method})
        files = {"calc.py": _make_file("calc.py", classes={"Calc": cls})}
        lookup = build_function_lookup(files)
        assert "compute" in lookup
        loc = lookup["compute"][0]
        assert loc.class_name == "Calc"
        assert loc.file_path == "calc.py"

    def test_same_name_multiple_files(self) -> None:
        f1 = _make_func("helper", file_path="a.py")
        f2 = _make_func("helper", file_path="b.py")
        files = {
            "a.py": _make_file("a.py", functions={"helper": f1}),
            "b.py": _make_file("b.py", functions={"helper": f2}),
        }
        lookup = build_function_lookup(files)
        assert len(lookup["helper"]) == 2
        paths = {loc.file_path for loc in lookup["helper"]}
        assert "a.py" in paths
        assert "b.py" in paths

    def test_empty_files(self) -> None:
        lookup = build_function_lookup({})
        assert lookup == {}

    def test_qualified_name_stored(self) -> None:
        func = _make_func("run", qualified_name="Runner.run", file_path="r.py")
        cls = ClassEntry(name="Runner", methods={"run": func})
        files = {"r.py": _make_file("r.py", classes={"Runner": cls})}
        lookup = build_function_lookup(files)
        assert lookup["run"][0].qualified_name == "Runner.run"


class TestResolveCallGraph:
    def test_same_file_call_resolved(self) -> None:
        callee = _make_func("helper", file_path="app.py")
        caller = _make_func(
            "main",
            file_path="app.py",
            calls=[CallEntry(callee_name="helper")],
        )
        files = {"app.py": _make_file("app.py", functions={"helper": callee, "main": caller})}
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        resolved_call = files["app.py"].functions["main"].calls[0]
        assert resolved_call.resolved_file == "app.py"
        assert resolved_call.is_external is False

    def test_cross_file_call_resolved(self) -> None:
        callee = _make_func("util", file_path="utils.py")
        caller = _make_func(
            "main",
            file_path="main.py",
            calls=[CallEntry(callee_name="util")],
        )
        files = {
            "utils.py": _make_file("utils.py", functions={"util": callee}),
            "main.py": _make_file("main.py", functions={"main": caller}),
        }
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        resolved_call = files["main.py"].functions["main"].calls[0]
        assert resolved_call.resolved_file == "utils.py"

    def test_unresolved_call_marked_external(self) -> None:
        caller = _make_func(
            "main",
            file_path="app.py",
            calls=[CallEntry(callee_name="requests.get")],
        )
        files = {"app.py": _make_file("app.py", functions={"main": caller})}
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        call = files["app.py"].functions["main"].calls[0]
        assert call.is_external is True

    def test_called_by_populated(self) -> None:
        callee = _make_func("helper", file_path="app.py")
        caller = _make_func(
            "main",
            file_path="app.py",
            calls=[CallEntry(callee_name="helper")],
        )
        files = {"app.py": _make_file("app.py", functions={"helper": callee, "main": caller})}
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        helper = files["app.py"].functions["helper"]
        assert len(helper.called_by) == 1
        assert helper.called_by[0].caller_name == "main"

    def test_called_by_cross_file(self) -> None:
        callee = _make_func("shared", file_path="shared.py")
        caller = _make_func(
            "consumer",
            file_path="consumer.py",
            calls=[CallEntry(callee_name="shared")],
        )
        files = {
            "shared.py": _make_file("shared.py", functions={"shared": callee}),
            "consumer.py": _make_file("consumer.py", functions={"consumer": caller}),
        }
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        shared = files["shared.py"].functions["shared"]
        assert any(c.caller_name == "consumer" for c in shared.called_by)

    def test_same_file_preferred_over_cross_file(self) -> None:
        """When the same name exists in two files, same-file should be preferred."""
        local_helper = _make_func("helper", file_path="a.py")
        remote_helper = _make_func("helper", file_path="b.py")
        caller = _make_func(
            "main",
            file_path="a.py",
            calls=[CallEntry(callee_name="helper")],
        )
        files = {
            "a.py": _make_file("a.py", functions={"helper": local_helper, "main": caller}),
            "b.py": _make_file("b.py", functions={"helper": remote_helper}),
        }
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        resolved = files["a.py"].functions["main"].calls[0]
        assert resolved.resolved_file == "a.py"

    def test_no_duplicate_called_by_entries(self) -> None:
        """Calling resolve_call_graph twice should not duplicate called_by entries."""
        callee = _make_func("helper", file_path="app.py")
        caller = _make_func(
            "main",
            file_path="app.py",
            calls=[CallEntry(callee_name="helper")],
        )
        files = {"app.py": _make_file("app.py", functions={"helper": callee, "main": caller})}
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)
        resolve_call_graph(files, lookup)

        helper = files["app.py"].functions["helper"]
        # Should still be exactly one entry, not duplicated
        assert len(helper.called_by) == 1

    def test_method_call_resolved(self) -> None:
        """Calls to class methods should be resolved via method name."""
        method = _make_func("process", qualified_name="Worker.process", file_path="worker.py")
        cls = ClassEntry(name="Worker", methods={"process": method})
        caller = _make_func(
            "orchestrate",
            file_path="main.py",
            calls=[CallEntry(callee_name="process")],
        )
        files = {
            "worker.py": _make_file("worker.py", classes={"Worker": cls}),
            "main.py": _make_file("main.py", functions={"orchestrate": caller}),
        }
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        call = files["main.py"].functions["orchestrate"].calls[0]
        assert call.resolved_file == "worker.py"
        assert call.resolved_qualified_name == "Worker.process"

    def test_from_import_alias_resolves_to_original_function(self) -> None:
        callee = _make_func("target", file_path="helper.py")
        caller = _make_func(
            "run",
            file_path="main.py",
            calls=[CallEntry(callee_name="alias")],
        )
        main_file = _make_file("main.py", functions={"run": caller})
        main_file.imports = [
            ImportEntry(
                module="helper",
                names=["target"],
                is_from_import=True,
                alias="alias",
            )
        ]
        files = {
            "helper.py": _make_file("helper.py", functions={"target": callee}),
            "main.py": main_file,
        }
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        call = files["main.py"].functions["run"].calls[0]
        assert call.resolved_file == "helper.py"
        assert call.resolved_qualified_name == "target"

    def test_tier2_text_updated_after_resolution(self) -> None:
        """After call graph resolution, tier2_text should be re-rendered."""
        callee = _make_func("helper", file_path="app.py")
        caller = _make_func(
            "main",
            file_path="app.py",
            calls=[CallEntry(callee_name="helper")],
        )
        files = {"app.py": _make_file("app.py", functions={"helper": callee, "main": caller})}
        lookup = build_function_lookup(files)
        resolve_call_graph(files, lookup)

        # tier2_text should reference the resolved callee after resolution
        main_func = files["app.py"].functions["main"]
        assert "depends_on:" in main_func.tier2_text
        assert "helper" in main_func.tier2_text
