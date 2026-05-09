"""BackendAgent — thin facade over ts_backend_analyzer's build_backend_graph.

This module does not reimplment backend analysis.  It delegates entirely to
``tools.ts.ts_backend_analyzer`` and exposes a uniform class interface so that
pipeline orchestrators can reference it without importing the analyzer module
directly.
"""
from __future__ import annotations

from typing import Any, Optional


class BackendAgent:
    """Thin facade over ts_backend_analyzer.build_backend_graph."""

    async def build_backend_graph(
        self,
        root: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        from tools.ts.ts_backend_analyzer import build_backend_graph  # lazy import
        await build_backend_graph(root, *args, **kwargs)
