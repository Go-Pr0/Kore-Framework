"""Shared fixtures for abstract_engine tests."""

from __future__ import annotations

import pytest


SAMPLE_PYTHON = '''\
"""Sample module for testing the Python parser.

This module contains various Python constructs for comprehensive test coverage.
"""

from __future__ import annotations

import os
import sys
from typing import Protocol, runtime_checkable
from dataclasses import dataclass
from abc import ABC, abstractmethod

MAX_RETRIES: int = 3
DEFAULT_TIMEOUT = 30.0
VERSION = "1.0.0"


def simple_function(x: int, y: int) -> int:
    """Add two integers together.

    Returns the sum.
    """
    return x + y


async def async_fetch(url: str, timeout: float = 10.0) -> str:
    """Fetch data from a URL asynchronously."""
    return ""


def generator_func(items: list) -> str:
    """Yield items one by one."""
    for item in items:
        yield item


def with_variadic(*args: int, **kwargs: str) -> None:
    """Accept variadic positional and keyword arguments."""
    result = sum(args)
    return None


def raises_errors(value: int) -> int:
    """Function that raises exceptions."""
    if value < 0:
        raise ValueError("Value must be non-negative")
    if value > 100:
        raise RuntimeError("Value too large")
    return value


def _protected_helper(data: str) -> str:
    """A protected helper function."""
    return data.strip()


def __private_helper(x: int) -> int:
    """A private helper function."""
    return x * 2


def __dunder_like__(value: str) -> str:
    """Dunder-style function is public."""
    return value


@dataclass
class Point:
    """A 2D point."""

    x: float
    y: float
    label: str = "default"


@dataclass
class Rectangle:
    """A rectangle with width and height."""

    width: float
    height: float

    def area(self) -> float:
        """Compute the area."""
        return self.width * self.height


@runtime_checkable
class Drawable(Protocol):
    """Protocol for drawable objects."""

    def draw(self) -> None:
        """Draw the object."""
        ...

    def get_bounds(self) -> tuple[float, float, float, float]:
        """Return (x, y, w, h)."""
        ...


class BaseService(ABC):
    """Abstract base service."""

    @abstractmethod
    def process(self, data: str) -> str:
        """Process the data."""
        ...

    @classmethod
    def create(cls) -> "BaseService":
        """Create a new instance."""
        ...

    @staticmethod
    def validate(value: str) -> bool:
        """Validate a value."""
        return bool(value)

    @property
    def name(self) -> str:
        """The service name."""
        return "base"


class ConcreteService(BaseService):
    """Concrete implementation of BaseService."""

    def __init__(self, config: dict) -> None:
        """Initialise the service."""
        self.config = config
        self._cache: dict = {}

    def process(self, data: str) -> str:
        """Process by calling helper."""
        cleaned = _protected_helper(data)
        return cleaned.upper()

    class NestedHelper:
        """A nested helper class."""

        def help(self) -> str:
            """Return help text."""
            return "helping"
'''


BROKEN_PYTHON = '''\
def broken_function(x:
    # syntax error — no closing paren, no body
    pass
'''


@pytest.fixture
def sample_project(tmp_path):
    """Create a temp directory with sample Python files."""
    py_file = tmp_path / "sample.py"
    py_file.write_text(SAMPLE_PYTHON, encoding="utf-8")

    broken_file = tmp_path / "broken.py"
    broken_file.write_text(BROKEN_PYTHON, encoding="utf-8")

    return tmp_path
