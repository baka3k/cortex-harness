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
    resolve_performance_profile,
    resolve_storage,
    storage_overlay,
    validate_storage_identity,
)
from .contracts import (
    FreshnessMetadata,
    GatewayErrorCode,
    GenerationManifest,
    GenerationState,
    IngestionJob,
    IngestionJobState,
    OwnerLifecycleState,
    PerformanceProfile,
    PhysicalTargetKey,
    StoreGatewayError,
    StoreHealth,
)
from .gateway import GatewayLimits, StoreGateway
from .generation import GenerationManager
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
    "FreshnessMetadata",
    "GatewayErrorCode",
    "GatewayLimits",
    "GenerationManager",
    "GenerationManifest",
    "GenerationState",
    "IngestionJob",
    "IngestionJobState",
    "LocalQdrantStore",
    "QdrantStorageRole",
    "OwnerLifecycleState",
    "PerformanceProfile",
    "PhysicalTargetKey",
    "ResolvedStorage",
    "StorageRole",
    "StorageLease",
    "StorageLeaseConflictError",
    "StoreGateway",
    "StoreGatewayError",
    "StoreHealth",
    "MigrationItem",
    "assert_owner_stopped",
    "build_filter",
    "get_client",
    "reset_clients",
    "resolve_storage",
    "resolve_performance_profile",
    "ensure_layout",
    "load_manifest",
    "manifest_payload",
    "migrate_legacy_layout",
    "storage_overlay",
    "validate_storage_identity",
]
