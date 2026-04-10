"""Abstract Representation Engine — compressed, multi-tiered codebase views.

The ARE takes a real codebase on disk and produces a compressed abstract view
that an expensive AI model reads instead of raw source files. It produces three
tiers of detail: ultra-concise (Tier 1), enriched (Tier 2), and full source
(Tier 3).
"""

from abstract_engine.index import AbstractIndex
from abstract_engine.models import (
    SCHEMA_VERSION,
    AttributeEntry,
    CallEntry,
    CallerEntry,
    ClassEntry,
    ConstantEntry,
    FileEntry,
    FunctionEntry,
    FunctionLocator,
    ImportEntry,
    ParameterEntry,
    TypeEntry,
)
from abstract_engine.renderer import (
    render_all_tier1,
    render_tier1_class,
    render_tier1_file,
    render_tier1_function,
    render_tier2_function,
)

__all__ = [
    "AbstractIndex",
    "AttributeEntry",
    "CallEntry",
    "CallerEntry",
    "ClassEntry",
    "ConstantEntry",
    "FileEntry",
    "FunctionEntry",
    "FunctionLocator",
    "ImportEntry",
    "ParameterEntry",
    "SCHEMA_VERSION",
    "TypeEntry",
    "render_all_tier1",
    "render_tier1_class",
    "render_tier1_file",
    "render_tier1_function",
    "render_tier2_function",
]
