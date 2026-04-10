"""Abstract base class for language-specific file parsers.

The FileParser defines the interface that all language parsers must implement.
Language-specific logic (tree-sitter queries, node type handling) is driven by
subclasses like TreeSitterParser which consume LanguageConfig instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from abstract_engine.models import FileEntry


class FileParser(ABC):
    """Language-agnostic base for file parsers."""

    @abstractmethod
    def parse_file(self, file_path: str, source_bytes: bytes) -> FileEntry:
        """Parse a source file and return a populated FileEntry.

        Args:
            file_path: The relative path to the file within the project.
            source_bytes: The raw UTF-8 bytes of the file content.

        Returns:
            A FileEntry with all extractable structural information populated.
        """
        ...

    @abstractmethod
    def extract_function_source(
        self,
        source_bytes: bytes,
        function_name: str,
        class_name: str | None = None,
    ) -> str | None:
        """Extract the full source code of a single named function.

        Used for Tier 3 reads. Returns the complete function source including
        decorators, signature, docstring, and body.

        Args:
            source_bytes: The raw UTF-8 bytes of the file content.
            function_name: The name of the function to extract.
            class_name: If the function is a method, the name of its class.

        Returns:
            The function source as a string, or None if not found.
        """
        ...

    @abstractmethod
    def supported_extensions(self) -> list[str]:
        """Return the file extensions this parser handles.

        Returns:
            A list of extensions including the dot, e.g., ['.py'].
        """
        ...
