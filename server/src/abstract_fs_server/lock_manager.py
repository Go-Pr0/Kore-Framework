"""Function-level lock manager for concurrent write pipelines.

Manages asyncio locks keyed by fully qualified function identifiers
(e.g., ``src/api/router.py::UserController.get_user``) so that multiple
agents can modify different functions in the same file concurrently
without conflicting.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict


class LockManager:
    """Per-function asyncio lock registry.

    Each lock is keyed by ``"file_path::qualified_name"`` so two agents
    targeting different functions in the same file never block each other.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, file_path: str, qualified_name: str) -> None:
        """Acquire the lock for *file_path::qualified_name*.

        Blocks until the lock is available.
        """
        key = f"{file_path}::{qualified_name}"
        await self._locks[key].acquire()

    def release(self, file_path: str, qualified_name: str) -> None:
        """Release the lock for *file_path::qualified_name*.

        No-op if the lock is not currently held.
        """
        key = f"{file_path}::{qualified_name}"
        if key in self._locks and self._locks[key].locked():
            self._locks[key].release()
