"""
Tool registry stub.

Phase 2 will implement:
  - A typed registry mapping tool names to callables.
  - JSON-schema generation for LLM tool-use payloads.
  - Execution context (auth, per-conversation state).
"""
from __future__ import annotations

from typing import Any, Callable


class ToolRegistry:
    """Stub — will be implemented in Phase 2."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable] = {}

    def register(self, name: str, fn: Callable) -> None:
        self._tools[name] = fn

    async def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}")
        return await self._tools[name](**kwargs)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())


# Module-level singleton
registry = ToolRegistry()
