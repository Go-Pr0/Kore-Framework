"""Tests for model serialisation round-trips."""

from __future__ import annotations

from abstract_engine.models import (
    AttributeEntry,
    CallerEntry,
    CallEntry,
    ClassEntry,
    ConstantEntry,
    FileEntry,
    FunctionEntry,
    FunctionLocator,
    ImportEntry,
    ParameterEntry,
    TypeEntry,
)


class TestParameterEntry:
    def test_round_trip_plain(self) -> None:
        p = ParameterEntry(name="x")
        assert ParameterEntry.from_dict(p.to_dict()) == p

    def test_round_trip_full(self) -> None:
        p = ParameterEntry(
            name="items",
            type_annotation="list[int]",
            has_default=True,
            default_value="[]",
            is_variadic=True,
            is_keyword_variadic=False,
        )
        assert ParameterEntry.from_dict(p.to_dict()) == p

    def test_to_dict_keys(self) -> None:
        p = ParameterEntry(name="foo")
        d = p.to_dict()
        expected_keys = {
            "name",
            "type_annotation",
            "has_default",
            "default_value",
            "is_variadic",
            "is_keyword_variadic",
        }
        assert set(d.keys()) == expected_keys


class TestCallEntry:
    def test_round_trip_unresolved(self) -> None:
        c = CallEntry(callee_name="helper")
        assert CallEntry.from_dict(c.to_dict()) == c

    def test_round_trip_resolved(self) -> None:
        c = CallEntry(
            callee_name="parse",
            resolved_file="parser.py",
            resolved_qualified_name="Parser.parse",
            is_external=False,
            call_count=3,
        )
        assert CallEntry.from_dict(c.to_dict()) == c

    def test_round_trip_external(self) -> None:
        c = CallEntry(callee_name="requests.get", is_external=True)
        result = CallEntry.from_dict(c.to_dict())
        assert result.is_external is True


class TestCallerEntry:
    def test_round_trip(self) -> None:
        c = CallerEntry(
            caller_name="main",
            caller_file="main.py",
            caller_qualified_name="main",
        )
        assert CallerEntry.from_dict(c.to_dict()) == c


class TestImportEntry:
    def test_round_trip_simple(self) -> None:
        i = ImportEntry(module="os")
        assert ImportEntry.from_dict(i.to_dict()) == i

    def test_round_trip_from(self) -> None:
        i = ImportEntry(
            module="typing",
            names=["List", "Dict"],
            is_from_import=True,
        )
        assert ImportEntry.from_dict(i.to_dict()) == i

    def test_round_trip_wildcard(self) -> None:
        i = ImportEntry(module="utils", is_wildcard=True, is_from_import=True)
        assert ImportEntry.from_dict(i.to_dict()) == i

    def test_round_trip_alias(self) -> None:
        i = ImportEntry(module="numpy", alias="np")
        assert ImportEntry.from_dict(i.to_dict()) == i


class TestConstantEntry:
    def test_round_trip(self) -> None:
        c = ConstantEntry(name="MAX_SIZE", value="100", type_annotation="int")
        assert ConstantEntry.from_dict(c.to_dict()) == c

    def test_round_trip_no_value(self) -> None:
        c = ConstantEntry(name="PLACEHOLDER")
        assert ConstantEntry.from_dict(c.to_dict()) == c


class TestAttributeEntry:
    def test_round_trip_plain(self) -> None:
        a = AttributeEntry(name="count")
        assert AttributeEntry.from_dict(a.to_dict()) == a

    def test_round_trip_full(self) -> None:
        a = AttributeEntry(
            name="timeout",
            type_annotation="float",
            has_default=True,
            default_value="30.0",
            visibility="protected",
        )
        assert AttributeEntry.from_dict(a.to_dict()) == a


class TestFunctionEntry:
    def _make_func(self, name: str = "do_thing") -> FunctionEntry:
        return FunctionEntry(
            name=name,
            qualified_name=f"MyClass.{name}",
            file_path="mymodule.py",
            start_line=10,
            end_line=20,
            start_byte=100,
            end_byte=300,
            is_async=True,
            is_generator=False,
            is_property=False,
            is_classmethod=False,
            is_staticmethod=False,
            is_abstract=False,
            visibility="public",
            decorators=["cached_property"],
            parameters=[
                ParameterEntry(name="self"),
                ParameterEntry(name="x", type_annotation="int", has_default=True, default_value="0"),
            ],
            return_type="str",
            docstring_first_line="Does the thing.",
            docstring_full="Does the thing.\n\nIn more detail.",
            calls=[CallEntry(callee_name="helper", call_count=2)],
            called_by=[CallerEntry(caller_name="main", caller_file="main.py")],
            raises=["ValueError"],
            tier2_text="tier2 rendered text",
        )

    def test_round_trip(self) -> None:
        func = self._make_func()
        restored = FunctionEntry.from_dict(func.to_dict())
        assert restored.name == func.name
        assert restored.qualified_name == func.qualified_name
        assert restored.is_async == func.is_async
        assert restored.return_type == func.return_type
        assert len(restored.parameters) == 2
        assert restored.parameters[1].has_default is True
        assert len(restored.calls) == 1
        assert restored.calls[0].callee_name == "helper"
        assert restored.calls[0].call_count == 2
        assert len(restored.called_by) == 1
        assert restored.called_by[0].caller_name == "main"
        assert restored.raises == ["ValueError"]
        assert restored.tier2_text == "tier2 rendered text"
        assert restored.visibility == "public"

    def test_from_dict_defaults(self) -> None:
        """from_dict must handle missing optional fields gracefully."""
        minimal = {"name": "foo"}
        func = FunctionEntry.from_dict(minimal)
        assert func.name == "foo"
        assert func.qualified_name == ""
        assert func.parameters == []
        assert func.calls == []
        assert func.called_by == []
        assert func.raises == []
        assert func.visibility == "public"


class TestClassEntry:
    def test_round_trip_with_methods(self) -> None:
        method = FunctionEntry(
            name="compute",
            qualified_name="Calculator.compute",
            file_path="calc.py",
            visibility="public",
        )
        cls = ClassEntry(
            name="Calculator",
            file_path="calc.py",
            start_line=1,
            end_line=50,
            base_classes=["Base"],
            is_dataclass=False,
            is_protocol=False,
            is_abstract=False,
            docstring_first_line="A calculator class.",
            methods={"compute": method},
            class_attributes=[AttributeEntry(name="PI", type_annotation="float")],
            instance_attributes=[AttributeEntry(name="result", type_annotation="float")],
        )

        restored = ClassEntry.from_dict(cls.to_dict())
        assert restored.name == "Calculator"
        assert restored.base_classes == ["Base"]
        assert "compute" in restored.methods
        assert restored.methods["compute"].qualified_name == "Calculator.compute"
        assert len(restored.class_attributes) == 1
        assert restored.class_attributes[0].name == "PI"
        assert len(restored.instance_attributes) == 1
        assert restored.instance_attributes[0].name == "result"

    def test_round_trip_dataclass(self) -> None:
        cls = ClassEntry(
            name="Point",
            is_dataclass=True,
            instance_attributes=[
                AttributeEntry(name="x", type_annotation="float"),
                AttributeEntry(name="y", type_annotation="float"),
            ],
        )
        restored = ClassEntry.from_dict(cls.to_dict())
        assert restored.is_dataclass is True
        assert len(restored.instance_attributes) == 2

    def test_round_trip_protocol(self) -> None:
        cls = ClassEntry(name="Drawable", is_protocol=True)
        restored = ClassEntry.from_dict(cls.to_dict())
        assert restored.is_protocol is True


class TestFileEntry:
    def test_round_trip_minimal(self) -> None:
        entry = FileEntry(relative_path="src/app.py", language="python", line_count=10)
        restored = FileEntry.from_dict(entry.to_dict())
        assert restored.relative_path == "src/app.py"
        assert restored.language == "python"
        assert restored.line_count == 10

    def test_round_trip_with_content(self) -> None:
        func = FunctionEntry(name="main", visibility="public", file_path="app.py")
        cls = ClassEntry(name="App", file_path="app.py")
        imp = ImportEntry(module="os")
        const = ConstantEntry(name="VERSION", value='"1.0"')

        entry = FileEntry(
            relative_path="app.py",
            language="python",
            line_count=100,
            last_modified=1234567890.0,
            content_hash="abc123",
            module_docstring="App module.",
            imports=[imp],
            classes={"App": cls},
            functions={"main": func},
            constants={"VERSION": const},
            tier1_text="tier1 text here",
            parse_error=False,
        )

        restored = FileEntry.from_dict(entry.to_dict())
        assert restored.module_docstring == "App module."
        assert len(restored.imports) == 1
        assert "App" in restored.classes
        assert "main" in restored.functions
        assert "VERSION" in restored.constants
        assert restored.tier1_text == "tier1 text here"
        assert restored.parse_error is False

    def test_round_trip_parse_error(self) -> None:
        entry = FileEntry(
            relative_path="bad.py",
            parse_error=True,
            parse_error_detail="SyntaxError at line 5",
        )
        restored = FileEntry.from_dict(entry.to_dict())
        assert restored.parse_error is True
        assert restored.parse_error_detail == "SyntaxError at line 5"

    def test_nested_structures_survive(self) -> None:
        """Deeply nested structures (class with methods with calls) survive round-trip."""
        call = CallEntry(callee_name="helper", resolved_file="helpers.py", call_count=2)
        method = FunctionEntry(
            name="run",
            qualified_name="Worker.run",
            calls=[call],
            called_by=[CallerEntry(caller_name="main", caller_file="main.py")],
        )
        cls = ClassEntry(
            name="Worker",
            methods={"run": method},
            instance_attributes=[AttributeEntry(name="queue")],
        )
        entry = FileEntry(
            relative_path="worker.py",
            language="python",
            classes={"Worker": cls},
        )

        restored = FileEntry.from_dict(entry.to_dict())
        r_method = restored.classes["Worker"].methods["run"]
        assert r_method.qualified_name == "Worker.run"
        assert len(r_method.calls) == 1
        assert r_method.calls[0].resolved_file == "helpers.py"
        assert len(r_method.called_by) == 1
        assert r_method.called_by[0].caller_name == "main"


class TestTypeEntry:
    def test_round_trip_interface(self) -> None:
        te = TypeEntry(
            name="User",
            kind="interface",
            file_path="types.ts",
            start_line=5,
            source_text="interface User { id: number; }",
            fields=[AttributeEntry(name="id", type_annotation="number")],
        )
        restored = TypeEntry.from_dict(te.to_dict())
        assert restored.name == "User"
        assert restored.kind == "interface"
        assert len(restored.fields) == 1
        assert restored.fields[0].name == "id"

    def test_round_trip_type_alias(self) -> None:
        te = TypeEntry(name="UserId", kind="type_alias", source_text="type UserId = string | number;")
        restored = TypeEntry.from_dict(te.to_dict())
        assert restored.kind == "type_alias"

    def test_round_trip_enum(self) -> None:
        te = TypeEntry(
            name="Status",
            kind="enum",
            fields=[
                AttributeEntry(name="Active"),
                AttributeEntry(name="Inactive"),
            ],
        )
        restored = TypeEntry.from_dict(te.to_dict())
        assert len(restored.fields) == 2


class TestFunctionLocator:
    def test_round_trip(self) -> None:
        loc = FunctionLocator(
            file_path="service.py",
            class_name="UserService",
            function_name="get_user",
            qualified_name="UserService.get_user",
        )
        restored = FunctionLocator.from_dict(loc.to_dict())
        assert restored.file_path == "service.py"
        assert restored.class_name == "UserService"
        assert restored.qualified_name == "UserService.get_user"
