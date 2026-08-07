"""Canonical graph schema contracts and automatic preflight."""

from .manifest import CODE_GRAPH_SCHEMA, GraphSchemaManifest, SchemaIndex
from .preflight import SchemaEnsureResult, SchemaPreflightError, ensure_schema

__all__ = [
    "CODE_GRAPH_SCHEMA",
    "GraphSchemaManifest",
    "SchemaEnsureResult",
    "SchemaIndex",
    "SchemaPreflightError",
    "ensure_schema",
]
