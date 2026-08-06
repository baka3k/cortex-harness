"""Local-storage configuration layer for Cortex Harness.

Provides a single source of truth for centralized Qdrant and FalkorDB
storage paths. Resolves default paths from the current account's application
data root, never a source-project directory, and exposes typed paths that
the rest of the application can consume without re-deriving network-style
configuration values.

This module intentionally has no runtime side effects on import: it only
defines immutable dataclasses and a :func:`resolve_storage` entry point.
"""

from __future__ import annotations

from .config import (
    DEFAULT_FALKORDB_PATH,
    DEFAULT_QDRANT_PATH,
    DEFAULT_INSTANCE_ID,
    STORAGE_SCHEMA_VERSION,
    InvalidStorageIdentityError,
    QdrantStorageRole,
    ResolvedStorage,
    StorageRole,
    resolve_storage,
    storage_overlay,
    validate_storage_identity,
)
from .layout import ensure_layout, load_manifest, manifest_payload
from .lease import StorageLease, StorageLeaseConflictError, assert_owner_stopped
from .migration import MigrationItem, migrate_legacy_layout
from .qdrant import LocalQdrantStore, build_filter, get_client, reset_clients

__all__ = [
    "DEFAULT_FALKORDB_PATH",
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_QDRANT_PATH",
    "STORAGE_SCHEMA_VERSION",
    "InvalidStorageIdentityError",
    "LocalQdrantStore",
    "QdrantStorageRole",
    "ResolvedStorage",
    "StorageRole",
    "StorageLease",
    "StorageLeaseConflictError",
    "MigrationItem",
    "assert_owner_stopped",
    "build_filter",
    "get_client",
    "reset_clients",
    "resolve_storage",
    "ensure_layout",
    "load_manifest",
    "manifest_payload",
    "migrate_legacy_layout",
    "storage_overlay",
    "validate_storage_identity",
]
