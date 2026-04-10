"""Tests for the pure rendering functions."""

from __future__ import annotations

from abstract_engine.models import (
    AttributeEntry,
    CallEntry,
    CallerEntry,
    ClassEntry,
    FileEntry,
    FunctionEntry,
    ParameterEntry,
)
from abstract_engine.renderer import (
    render_all_tier1,
    render_tier1_file,
    render_tier1_function,
    render_tier2_function,
)


def _make_func(
    name: str = "do_work",
    params: list[ParameterEntry] | None = None,
    return_type: str | None = "None",
    docstring: str | None = None,
    is_async: bool = False,
    is_method: bool = False,
    visibility: str = "public",
    decorators: list[str] | None = None,
    calls: list[CallEntry] | None = None,
    called_by: list[CallerEntry] | None = None,
    raises: list[str] | None = None,
    qualified_name: str = "",
) -> FunctionEntry:
    return FunctionEntry(
        name=name,
        qualified_name=qualified_name or name,
        visibility=visibility,
        is_async=is_async,
        parameters=params or [],
        return_type=return_type,
        docstring_first_line=docstring,
        decorators=decorators or [],
        calls=calls or [],
        called_by=called_by or [],
        raises=raises or [],
    )


class TestRenderTier1Function:
    def test_basic_format(self) -> None:
        func = _make_func("greet", return_type="str", docstring="Greet a user.")
        line = render_tier1_function(func)
        assert line.startswith("greet(")
        assert "->str:" in line
        assert "Greet a user." in line

    def test_async_prefix(self) -> None:
        func = _make_func("fetch", is_async=True, return_type="str")
        line = render_tier1_function(func)
        assert line.startswith("async fetch(")

    def test_non_async_no_prefix(self) -> None:
        func = _make_func("process")
        line = render_tier1_function(func)
        assert not line.startswith("async")

    def test_no_docstring_omits_description(self) -> None:
        func = _make_func("get_user_by_id", docstring=None, return_type="User")
        line = render_tier1_function(func)
        # No docstring — description is omitted, signature and line range only
        assert "get_user_by_id" in line
        assert "->User" in line
        assert "Get user by id" not in line

    def test_return_type_none_fallback(self) -> None:
        func = _make_func("side_effect", return_type=None)
        line = render_tier1_function(func)
        assert "->None" in line

    def test_self_removed_from_method(self) -> None:
        params = [
            ParameterEntry(name="self"),
            ParameterEntry(name="x", type_annotation="int"),
        ]
        func = _make_func("method", params=params, return_type="str")
        line = render_tier1_function(func, is_method=True)
        assert "self" not in line
        assert "x:int" in line

    def test_cls_removed_from_method(self) -> None:
        params = [
            ParameterEntry(name="cls"),
            ParameterEntry(name="value", type_annotation="str"),
        ]
        func = _make_func("create", params=params, return_type="object")
        line = render_tier1_function(func, is_method=True)
        assert "cls" not in line

    def test_typed_params_shown(self) -> None:
        params = [
            ParameterEntry(name="url", type_annotation="str"),
            ParameterEntry(name="timeout", type_annotation="float", has_default=True, default_value="30.0"),
        ]
        func = _make_func("fetch", params=params, return_type="bytes")
        line = render_tier1_function(func)
        assert "url:str" in line
        assert "timeout:float=30.0" in line

    def test_variadic_args_shown(self) -> None:
        params = [
            ParameterEntry(name="args", is_variadic=True, type_annotation="int"),
        ]
        func = _make_func("f", params=params, return_type="None")
        line = render_tier1_function(func)
        assert "*args" in line

    def test_docstring_truncated_at_100(self) -> None:
        long_doc = "A" * 120
        func = _make_func("fn", docstring=long_doc, return_type="None")
        line = render_tier1_function(func)
        # The one-liner portion should be truncated at 100 chars (97 + "...")
        one_liner_part = line.split(": ", 1)[1]
        assert len(one_liner_part) <= 103  # 100 + "..."
        assert one_liner_part.endswith("...")


class TestRenderTier1File:
    def _make_file(
        self,
        path: str = "mymodule.py",
        module_doc: str | None = None,
        functions: dict | None = None,
        classes: dict | None = None,
        line_count: int = 10,
        parse_error: bool = False,
        parse_error_detail: str | None = None,
    ) -> FileEntry:
        return FileEntry(
            relative_path=path,
            language="python",
            line_count=line_count,
            module_docstring=module_doc,
            functions=functions or {},
            classes=classes or {},
            parse_error=parse_error,
            parse_error_detail=parse_error_detail,
        )

    def test_header_format(self) -> None:
        func = _make_func("foo", return_type="None", docstring="Does foo.")
        entry = self._make_file(functions={"foo": func}, line_count=20, module_doc="My module.")
        rendered = render_tier1_file(entry)
        # Header line: path [Nfn, NL]: one-liner
        first_line = rendered.split("\n")[0]
        assert "mymodule.py" in first_line
        assert "[1fn, 20L]" in first_line
        assert "My module." in first_line

    def test_function_indented(self) -> None:
        func = _make_func("compute", return_type="int")
        entry = self._make_file(functions={"compute": func})
        rendered = render_tier1_file(entry)
        lines = rendered.split("\n")
        func_lines = [l for l in lines if "compute" in l]
        assert any(l.startswith("  ") for l in func_lines)

    def test_private_function_excluded(self) -> None:
        pub = _make_func("public_fn", visibility="public")
        priv = _make_func("__private_fn", visibility="private")
        entry = self._make_file(functions={"public_fn": pub, "__private_fn": priv})
        rendered = render_tier1_file(entry)
        assert "public_fn" in rendered
        assert "__private_fn" not in rendered

    def test_fn_count_only_public(self) -> None:
        pub = _make_func("pub", visibility="public")
        priv = _make_func("_priv", visibility="protected")
        entry = self._make_file(functions={"pub": pub, "_priv": priv}, line_count=5)
        first_line = render_tier1_file(entry).split("\n")[0]
        assert "[1fn," in first_line

    def test_parse_error_file(self) -> None:
        entry = self._make_file(
            parse_error=True,
            parse_error_detail="Unexpected indent at line 5",
        )
        rendered = render_tier1_file(entry)
        assert "[PARSE ERROR]" in rendered
        assert "Unexpected indent" in rendered

    def test_parse_error_without_detail(self) -> None:
        entry = self._make_file(parse_error=True)
        rendered = render_tier1_file(entry)
        assert "[PARSE ERROR]" in rendered

    def test_dataclass_rendered_as_type(self) -> None:
        cls = ClassEntry(
            name="Point",
            is_dataclass=True,
            instance_attributes=[
                AttributeEntry(name="x", type_annotation="float"),
                AttributeEntry(name="y", type_annotation="float"),
                AttributeEntry(name="label", type_annotation="str", has_default=True, default_value="'default'"),
            ],
        )
        entry = self._make_file(classes={"Point": cls})
        rendered = render_tier1_file(entry)
        assert "Point{" in rendered
        assert "x:float" in rendered
        assert "y:float" in rendered
        assert "label:str='default'" in rendered

    def test_protocol_class_suffix(self) -> None:
        cls = ClassEntry(
            name="Drawable",
            is_protocol=True,
            methods={
                "draw": _make_func("draw", visibility="public", return_type="None"),
            },
        )
        entry = self._make_file(classes={"Drawable": cls})
        rendered = render_tier1_file(entry)
        assert "Drawable [Protocol]" in rendered

    def test_file_one_liner_from_docstring(self) -> None:
        entry = self._make_file(module_doc="The main application entrypoint.")
        rendered = render_tier1_file(entry)
        assert "The main application entrypoint." in rendered

    def test_file_one_liner_derived_from_filename(self) -> None:
        entry = self._make_file(path="user_service.py", module_doc=None)
        rendered = render_tier1_file(entry)
        # Should expand snake_case of "user_service"
        assert "User service" in rendered


class TestRenderTier2Function:
    def test_signature_line(self) -> None:
        params = [
            ParameterEntry(name="self"),
            ParameterEntry(name="user_id", type_annotation="int"),
        ]
        func = _make_func(
            "get_user",
            params=params,
            return_type="User",
            qualified_name="UserService.get_user",
        )
        rendered = render_tier2_function(func)
        first_line = rendered.split("\n")[0]
        assert "UserService.get_user" in first_line
        assert "context for" in first_line

    def test_docstring_not_in_tier2(self) -> None:
        func = _make_func("fn", docstring="Does important work.")
        rendered = render_tier2_function(func)
        # New format omits docstring (already in Tier 1 one-liner)
        assert "Does important work." not in rendered

    def test_calls_shown(self) -> None:
        calls = [
            CallEntry(callee_name="helper", resolved_file="utils.py", call_count=1),
            CallEntry(callee_name="external_lib", is_external=True),
        ]
        func = _make_func("orchestrator", calls=calls)
        rendered = render_tier2_function(func)
        assert "depends_on:" in rendered
        assert "helper" in rendered
        assert "utils.py" in rendered
        assert "external_lib" in rendered

    def test_ambiguous_resolved_call_marked_uncertain(self) -> None:
        calls = [
            CallEntry(
                callee_name="process",
                resolved_file="a.py",
                resolved_qualified_name="process",
                resolution_confidence="ambiguous",
                match_count=3,
            )
        ]
        func = _make_func("orchestrator", calls=calls)
        rendered = render_tier2_function(func)
        assert "depends_on:" in rendered
        assert "ambiguous: 3 matches" in rendered
        assert "verify source" in rendered

    def test_used_by_shown(self) -> None:
        caller = CallerEntry(
            caller_name="main",
            caller_file="main.py",
            caller_qualified_name="main",
        )
        func = _make_func("utility", called_by=[caller])
        rendered = render_tier2_function(func)
        assert "called_by:" in rendered
        assert "main" in rendered

    def test_raises_shown(self) -> None:
        func = _make_func("risky", raises=["ValueError", "RuntimeError"])
        rendered = render_tier2_function(func)
        assert "raises:" in rendered
        assert "ValueError" in rendered
        assert "RuntimeError" in rendered

    def test_async_flag_not_in_tier2(self) -> None:
        # Async flag is shown in Tier 1 prefix, not repeated in Tier 2
        func = _make_func("async_fn", is_async=True)
        rendered = render_tier2_function(func)
        assert "async:" not in rendered

    def test_sync_flag_not_in_tier2(self) -> None:
        func = _make_func("sync_fn", is_async=False)
        rendered = render_tier2_function(func)
        assert "async:" not in rendered

    def test_decorators_shown(self) -> None:
        func = _make_func("cached", decorators=["cache", "log"])
        rendered = render_tier2_function(func)
        assert "@cache" in rendered
        assert "@log" in rendered

    def test_no_decorators_omitted(self) -> None:
        func = _make_func("plain")
        rendered = render_tier2_function(func)
        assert "decorators:" not in rendered

    def test_calls_omitted_when_empty(self) -> None:
        func = _make_func("leaf")
        rendered = render_tier2_function(func)
        assert "depends_on:" not in rendered

    def test_used_by_omitted_when_empty(self) -> None:
        func = _make_func("unused")
        rendered = render_tier2_function(func)
        assert "called_by:" not in rendered

    def test_raises_omitted_when_empty(self) -> None:
        func = _make_func("safe")
        rendered = render_tier2_function(func)
        assert "raises:" not in rendered


class TestRenderAllTier1:
    def test_sorted_by_path(self) -> None:
        files = {
            "z_module.py": FileEntry(relative_path="z_module.py", language="python", line_count=1),
            "a_module.py": FileEntry(relative_path="a_module.py", language="python", line_count=1),
            "m_module.py": FileEntry(relative_path="m_module.py", language="python", line_count=1),
        }
        rendered = render_all_tier1(files)
        a_pos = rendered.index("a_module.py")
        m_pos = rendered.index("m_module.py")
        z_pos = rendered.index("z_module.py")
        assert a_pos < m_pos < z_pos

    def test_files_separated_by_blank_line(self) -> None:
        files = {
            "a.py": FileEntry(relative_path="a.py", language="python", line_count=1),
            "b.py": FileEntry(relative_path="b.py", language="python", line_count=1),
        }
        rendered = render_all_tier1(files)
        assert "\n\n" in rendered

    def test_all_files_included(self) -> None:
        files = {
            "mod1.py": FileEntry(relative_path="mod1.py", language="python", line_count=5),
            "mod2.py": FileEntry(relative_path="mod2.py", language="python", line_count=5),
            "mod3.py": FileEntry(relative_path="mod3.py", language="python", line_count=5),
        }
        rendered = render_all_tier1(files)
        assert "mod1.py" in rendered
        assert "mod2.py" in rendered
        assert "mod3.py" in rendered

    def test_empty_files_dict(self) -> None:
        rendered = render_all_tier1({})
        assert rendered == ""
