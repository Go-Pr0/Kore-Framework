"""Tests for the Python file parser."""

from __future__ import annotations

import pytest

from abstract_engine.tree_sitter_parser import TreeSitterParser
from abstract_engine.lang.python import PYTHON_CONFIG

parser = TreeSitterParser(PYTHON_CONFIG)


def _src(code: str) -> bytes:
    return code.encode("utf-8")


class TestSupportedExtensions:
    def test_returns_py_extensions(self) -> None:
        exts = parser.supported_extensions()
        assert ".py" in exts
        assert ".pyi" in exts


class TestModuleDocstring:
    def test_extracts_module_docstring(self) -> None:
        src = _src('"""My module does stuff."""\n\nx = 1\n')
        entry = parser.parse_file("mod.py", src)
        assert entry.module_docstring == "My module does stuff."

    def test_no_module_docstring_when_code_first(self) -> None:
        src = _src("x = 1\n")
        entry = parser.parse_file("mod.py", src)
        assert entry.module_docstring is None

    def test_multiline_module_docstring_first_line(self) -> None:
        src = _src('"""\nFirst line.\n\nMore detail.\n"""\n')
        entry = parser.parse_file("mod.py", src)
        assert entry.module_docstring == "First line."


class TestBasicFunctionExtraction:
    def test_name(self) -> None:
        src = _src("def my_func(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert "my_func" in entry.functions

    def test_return_type(self) -> None:
        src = _src("def greet(name: str) -> str:\n    return name\n")
        entry = parser.parse_file("f.py", src)
        func = entry.functions["greet"]
        assert func.return_type == "str"

    def test_no_return_type(self) -> None:
        src = _src("def nothing(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["nothing"].return_type is None

    def test_docstring_first_line(self) -> None:
        src = _src('def foo():\n    """Brief description."""\n    pass\n')
        entry = parser.parse_file("f.py", src)
        assert entry.functions["foo"].docstring_first_line == "Brief description."

    def test_multiline_docstring(self) -> None:
        src = _src('def foo():\n    """\n    First line.\n\n    Details.\n    """\n    pass\n')
        entry = parser.parse_file("f.py", src)
        assert entry.functions["foo"].docstring_first_line == "First line."
        assert "Details" in (entry.functions["foo"].docstring_full or "")

    def test_no_docstring(self) -> None:
        src = _src("def bar(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["bar"].docstring_first_line is None

    def test_qualified_name_top_level(self) -> None:
        src = _src("def top_level(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["top_level"].qualified_name == "top_level"


class TestParameters:
    def test_plain_param(self) -> None:
        src = _src("def f(x): pass\n")
        params = parser.parse_file("f.py", src).functions["f"].parameters
        assert len(params) == 1
        assert params[0].name == "x"
        assert params[0].type_annotation is None

    def test_typed_param(self) -> None:
        src = _src("def f(x: int) -> None: pass\n")
        params = parser.parse_file("f.py", src).functions["f"].parameters
        assert params[0].name == "x"
        assert params[0].type_annotation == "int"

    def test_default_param(self) -> None:
        src = _src("def f(x=10): pass\n")
        params = parser.parse_file("f.py", src).functions["f"].parameters
        assert params[0].has_default is True
        assert params[0].default_value == "10"

    def test_typed_default_param(self) -> None:
        src = _src("def f(x: int = 5) -> None: pass\n")
        params = parser.parse_file("f.py", src).functions["f"].parameters
        assert params[0].type_annotation == "int"
        assert params[0].has_default is True
        assert params[0].default_value == "5"

    def test_args_kwargs(self) -> None:
        src = _src("def f(*args: int, **kwargs: str) -> None: pass\n")
        params = parser.parse_file("f.py", src).functions["f"].parameters
        args_p = next(p for p in params if p.name == "args")
        kwargs_p = next(p for p in params if p.name == "kwargs")
        assert args_p.is_variadic is True
        assert kwargs_p.is_keyword_variadic is True

    def test_self_param_present(self) -> None:
        src = _src("class C:\n    def m(self): pass\n")
        entry = parser.parse_file("c.py", src)
        method = entry.classes["C"].methods["m"]
        names = [p.name for p in method.parameters]
        assert "self" in names


class TestDecorators:
    def test_property_decorator(self) -> None:
        src = _src("class C:\n    @property\n    def name(self) -> str:\n        return ''\n")
        entry = parser.parse_file("c.py", src)
        assert entry.classes["C"].methods["name"].is_property is True

    def test_classmethod_decorator(self) -> None:
        src = _src("class C:\n    @classmethod\n    def create(cls) -> 'C':\n        pass\n")
        entry = parser.parse_file("c.py", src)
        assert entry.classes["C"].methods["create"].is_classmethod is True

    def test_staticmethod_decorator(self) -> None:
        src = _src("class C:\n    @staticmethod\n    def validate(x: int) -> bool:\n        return True\n")
        entry = parser.parse_file("c.py", src)
        assert entry.classes["C"].methods["validate"].is_staticmethod is True

    def test_abstractmethod_decorator(self) -> None:
        src = _src(
            "from abc import abstractmethod\n"
            "class Base:\n"
            "    @abstractmethod\n"
            "    def run(self) -> None: ...\n"
        )
        entry = parser.parse_file("c.py", src)
        assert entry.classes["Base"].methods["run"].is_abstract is True

    def test_custom_decorator_stored(self) -> None:
        src = _src("@my_decorator\ndef foo(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert "my_decorator" in entry.functions["foo"].decorators


class TestAsyncFunctions:
    def test_async_detection(self) -> None:
        src = _src("async def fetch(url: str) -> str:\n    return ''\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["fetch"].is_async is True

    def test_non_async(self) -> None:
        src = _src("def regular(): pass\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["regular"].is_async is False


class TestGenerators:
    def test_generator_detection(self) -> None:
        src = _src("def gen():\n    yield 1\n    yield 2\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["gen"].is_generator is True

    def test_non_generator(self) -> None:
        src = _src("def plain():\n    return 1\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["plain"].is_generator is False


class TestVisibility:
    def test_public_function(self) -> None:
        src = _src("def public_func(): pass\n")
        assert parser.parse_file("f.py", src).functions["public_func"].visibility == "public"

    def test_protected_function(self) -> None:
        src = _src("def _protected(): pass\n")
        assert parser.parse_file("f.py", src).functions["_protected"].visibility == "protected"

    def test_private_function(self) -> None:
        src = _src("def __private(): pass\n")
        assert parser.parse_file("f.py", src).functions["__private"].visibility == "private"

    def test_dunder_is_public(self) -> None:
        src = _src("def __init__(self): pass\n")
        assert parser.parse_file("f.py", src).functions["__init__"].visibility == "public"

    def test_dunder_in_class_is_public(self) -> None:
        src = _src("class C:\n    def __repr__(self) -> str:\n        return ''\n")
        method = parser.parse_file("c.py", src).classes["C"].methods["__repr__"]
        assert method.visibility == "public"


class TestDataclassExtraction:
    def test_dataclass_fields(self) -> None:
        src = _src(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Point:\n"
            "    x: float\n"
            "    y: float\n"
            "    label: str = 'default'\n"
        )
        entry = parser.parse_file("c.py", src)
        cls = entry.classes["Point"]
        assert cls.is_dataclass is True
        names = [a.name for a in cls.instance_attributes]
        assert "x" in names
        assert "y" in names
        assert "label" in names

        label = next(a for a in cls.instance_attributes if a.name == "label")
        assert label.has_default is True


class TestProtocolClass:
    def test_protocol_detection(self) -> None:
        src = _src(
            "from typing import Protocol\n"
            "class Drawable(Protocol):\n"
            "    def draw(self) -> None: ...\n"
        )
        entry = parser.parse_file("c.py", src)
        assert entry.classes["Drawable"].is_protocol is True


class TestClassMethods:
    def test_method_extraction(self) -> None:
        src = _src(
            "class MyService:\n"
            "    def run(self, data: str) -> str:\n"
            "        return data\n"
        )
        entry = parser.parse_file("c.py", src)
        cls = entry.classes["MyService"]
        assert "run" in cls.methods
        method = cls.methods["run"]
        assert method.return_type == "str"

    def test_method_qualified_name(self) -> None:
        src = _src("class C:\n    def work(self): pass\n")
        method = parser.parse_file("c.py", src).classes["C"].methods["work"]
        assert method.qualified_name == "C.work"

    def test_nested_class(self) -> None:
        src = _src(
            "class Outer:\n"
            "    class Inner:\n"
            "        def inner_method(self): pass\n"
        )
        entry = parser.parse_file("c.py", src)
        assert "Outer" in entry.classes


class TestImportExtraction:
    def test_plain_import(self) -> None:
        src = _src("import os\n")
        imports = parser.parse_file("m.py", src).imports
        assert any(i.module == "os" for i in imports)

    def test_from_import(self) -> None:
        src = _src("from typing import List, Dict\n")
        imports = parser.parse_file("m.py", src).imports
        assert len(imports) == 1
        assert imports[0].is_from_import is True
        assert "List" in imports[0].names
        assert "Dict" in imports[0].names

    def test_import_alias(self) -> None:
        src = _src("import numpy as np\n")
        imports = parser.parse_file("m.py", src).imports
        assert any(i.alias == "np" for i in imports)

    def test_from_import_alias(self) -> None:
        src = _src("from package.module import Service as Svc\n")
        imports = parser.parse_file("m.py", src).imports
        assert len(imports) == 1
        assert imports[0].module == "package.module"
        assert imports[0].names == ["Service"]
        assert imports[0].alias == "Svc"

    def test_mixed_from_import_aliases_split_entries(self) -> None:
        src = _src("from package.module import Plain, Service as Svc\n")
        imports = parser.parse_file("m.py", src).imports
        assert any(i.names == ["Plain"] and i.alias is None for i in imports)
        assert any(i.names == ["Service"] and i.alias == "Svc" for i in imports)

    def test_wildcard_import(self) -> None:
        src = _src("from utils import *\n")
        imports = parser.parse_file("m.py", src).imports
        assert any(i.is_wildcard for i in imports)


class TestConstantExtraction:
    def test_all_caps_constant(self) -> None:
        src = _src("MAX_RETRIES = 3\n")
        constants = parser.parse_file("m.py", src).constants
        assert "MAX_RETRIES" in constants
        assert constants["MAX_RETRIES"].value == "3"

    def test_mixed_case_not_constant(self) -> None:
        src = _src("myVar = 1\n")
        constants = parser.parse_file("m.py", src).constants
        assert "myVar" not in constants

    def test_constant_with_type_annotation(self) -> None:
        src = _src("VERSION: str = '1.0'\n")
        # Only ALL_CAPS matches; VERSION has uppercase so it should be captured.
        # But "Version" style won't be — test that ALL_CAPS works.
        src2 = _src("MAX_SIZE: int = 100\n")
        constants = parser.parse_file("m.py", src2).constants
        assert "MAX_SIZE" in constants


class TestCallExtraction:
    def test_calls_in_function_body(self) -> None:
        src = _src(
            "def process(data: str) -> str:\n"
            "    cleaned = strip(data)\n"
            "    result = transform(cleaned)\n"
            "    return result\n"
        )
        entry = parser.parse_file("f.py", src)
        calls = entry.functions["process"].calls
        call_names = [c.callee_name for c in calls]
        assert "strip" in call_names
        assert "transform" in call_names

    def test_method_calls(self) -> None:
        src = _src(
            "def work():\n"
            "    self.helper()\n"
            "    obj.method()\n"
        )
        entry = parser.parse_file("f.py", src)
        calls = entry.functions["work"].calls
        call_names = [c.callee_name for c in calls]
        assert any("helper" in n for n in call_names)

    def test_no_calls(self) -> None:
        src = _src("def pure(x: int) -> int:\n    return x + 1\n")
        entry = parser.parse_file("f.py", src)
        assert entry.functions["pure"].calls == []


class TestExtractFunctionSource:
    def test_returns_exact_source(self) -> None:
        src = _src("def hello() -> str:\n    return 'world'\n")
        result = parser.extract_function_source(src, "hello")
        assert result is not None
        assert "def hello" in result
        assert "return 'world'" in result

    def test_includes_decorators(self) -> None:
        src = _src("@my_decorator\ndef decorated(): pass\n")
        result = parser.extract_function_source(src, "decorated")
        assert result is not None
        assert "@my_decorator" in result

    def test_method_extraction(self) -> None:
        src = _src(
            "class C:\n"
            "    def method(self) -> int:\n"
            "        return 42\n"
        )
        result = parser.extract_function_source(src, "method", class_name="C")
        assert result is not None
        assert "def method" in result
        assert "return 42" in result

    def test_nonexistent_function_returns_none(self) -> None:
        src = _src("def real(): pass\n")
        assert parser.extract_function_source(src, "missing") is None

    def test_nonexistent_class_returns_none(self) -> None:
        src = _src("class Foo:\n    def m(self): pass\n")
        assert parser.extract_function_source(src, "m", class_name="Bar") is None


class TestParseErrors:
    def test_broken_syntax_does_not_crash(self) -> None:
        # tree-sitter is lenient — it may flag error nodes
        broken = _src("def broken(x:\n    pass\n")
        entry = parser.parse_file("broken.py", broken)
        # Must not raise; relative_path always set
        assert entry.relative_path == "broken.py"

    def test_parse_error_flag_set(self, sample_project) -> None:
        broken_path = sample_project / "broken.py"
        src = broken_path.read_bytes()
        entry = parser.parse_file("broken.py", src)
        # tree-sitter should detect the error node
        assert entry.parse_error is True

    def test_parse_error_detail_set(self, sample_project) -> None:
        broken_path = sample_project / "broken.py"
        src = broken_path.read_bytes()
        entry = parser.parse_file("broken.py", src)
        assert entry.parse_error_detail is not None
        assert len(entry.parse_error_detail) > 0

    def test_nested_parse_error_flag_set(self) -> None:
        src = _src(
            "def outer():\n"
            "    if True:\n"
            "        return (\n"
        )
        entry = parser.parse_file("nested_broken.py", src)
        assert entry.parse_error is True

    def test_parse_error_detail_includes_line_number(self) -> None:
        src = _src("x = 1\ndef broken(:\n    pass\n")
        entry = parser.parse_file("detail.py", src)
        assert entry.parse_error is True
        assert entry.parse_error_detail is not None
        # Detail should include line location info, not generic message
        assert "L" in entry.parse_error_detail

    def test_parse_error_detail_multiple_errors(self) -> None:
        src = _src(
            "def a(:\n    pass\ndef b(:\n    pass\n"
        )
        entry = parser.parse_file("multi.py", src)
        assert entry.parse_error is True
        assert entry.parse_error_detail is not None
        # Should report multiple errors (not just the first)
        assert ";" in entry.parse_error_detail or "+" in entry.parse_error_detail

    def test_parse_error_detail_nested_shows_location(self) -> None:
        src = _src(
            "def outer():\n"
            "    if True:\n"
            "        x = (\n"
        )
        entry = parser.parse_file("nested.py", src)
        assert entry.parse_error is True
        assert entry.parse_error_detail is not None
        assert "L" in entry.parse_error_detail


class TestLineCount:
    def test_line_count_single_line(self) -> None:
        src = _src("x = 1")
        entry = parser.parse_file("f.py", src)
        assert entry.line_count == 1

    def test_line_count_multiline(self) -> None:
        src = _src("x = 1\ny = 2\nz = 3\n")
        entry = parser.parse_file("f.py", src)
        assert entry.line_count == 3


class TestFileLanguage:
    def test_language_is_python(self) -> None:
        src = _src("x = 1\n")
        entry = parser.parse_file("f.py", src)
        assert entry.language == "python"


class TestSampleFile:
    """Integration tests using the shared sample Python source."""

    @pytest.fixture(autouse=True)
    def _load(self, sample_project):
        py_file = sample_project / "sample.py"
        src = py_file.read_bytes()
        self.entry = parser.parse_file("sample.py", src)

    def test_module_docstring_extracted(self) -> None:
        assert self.entry.module_docstring is not None
        assert "Sample module" in self.entry.module_docstring

    def test_public_function_present(self) -> None:
        assert "simple_function" in self.entry.functions

    def test_async_function_detected(self) -> None:
        assert "async_fetch" in self.entry.functions
        assert self.entry.functions["async_fetch"].is_async is True

    def test_generator_detected(self) -> None:
        assert "generator_func" in self.entry.functions
        assert self.entry.functions["generator_func"].is_generator is True

    def test_protected_function_visibility(self) -> None:
        assert "_protected_helper" in self.entry.functions
        assert self.entry.functions["_protected_helper"].visibility == "protected"

    def test_private_function_visibility(self) -> None:
        assert "__private_helper" in self.entry.functions
        assert self.entry.functions["__private_helper"].visibility == "private"

    def test_dunder_function_is_public(self) -> None:
        assert "__dunder_like__" in self.entry.functions
        assert self.entry.functions["__dunder_like__"].visibility == "public"

    def test_dataclass_detected(self) -> None:
        assert "Point" in self.entry.classes
        assert self.entry.classes["Point"].is_dataclass is True

    def test_protocol_detected(self) -> None:
        assert "Drawable" in self.entry.classes
        assert self.entry.classes["Drawable"].is_protocol is True

    def test_constants_extracted(self) -> None:
        assert "MAX_RETRIES" in self.entry.constants

    def test_imports_extracted(self) -> None:
        assert len(self.entry.imports) > 0
        modules = [i.module for i in self.entry.imports]
        assert "os" in modules or "sys" in modules or "__future__" in modules

    def test_tier1_prerendered(self) -> None:
        assert self.entry.tier1_text != ""
        assert "sample.py" in self.entry.tier1_text
