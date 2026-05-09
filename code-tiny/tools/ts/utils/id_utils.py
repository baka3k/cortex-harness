"""Symbol ID generation utilities.

All stable ID construction functions live here so that any module that needs
to build or look up a symbol ID imports from a single authoritative source
rather than duplicating the logic.
"""
from __future__ import annotations

import uuid
from typing import Optional


def _symbol_id(scope: Optional[str], name: str, arity: int, rel_path: str) -> str:
    qualified = f"{scope}::{name}" if scope else name
    return f"{qualified}/{arity}@{rel_path}"


def _qualified_name(scope: Optional[str], name: str) -> str:
    return f"{scope}::{name}" if scope else name


def _type_id(qualified: str) -> str:
    return qualified


def _namespace_id(name: str) -> str:
    return f"namespace::{name}"


def _anonymous_name(prefix: str, node: object) -> str:  # node: tree-sitter Node
    start = getattr(node, "start_point", (0, 0))
    return f"Anonymous{prefix}@{start[0] + 1}:{start[1] + 1}"


def _stable_point_id(symbol_id: str) -> str:
    """Deterministic Qdrant point ID (UUID5) derived from a symbol ID string."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))
