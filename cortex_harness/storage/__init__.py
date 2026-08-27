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
    BackendMode,
    InvalidStorageIdentityError,
    QdrantStorageRole,
    RemoteStorageConfig,
    ResolvedStorage,
    StorageRole,
    resolve_performance_profile,
    resolve_storage,
    storage_overlay,
    validate_backend_config,
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
from .qdrant_remote import (
    RemoteQdrantStore,
    get_remote_client,
    reset_remote_clients,
)
from .errors import BackendConnectionError
from .factory import QdrantStore, StorageFactory, create_storage
from .targets import (
    ENV_EFFECTIVE_GRAPH_FINGERPRINT,
    ENV_EFFECTIVE_GRAPH_TARGET,
    ENV_EFFECTIVE_TOPOLOGY,
    ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT,
    ENV_EFFECTIVE_VECTOR_FINGERPRINT,
    ENV_EFFECTIVE_VECTOR_TARGET,
    EffectiveStorageTarget,
    EffectiveStorageTopology,
    canonical_remote_endpoint,
    effective_graph_target_from_env,
)
from .remote_probe import (
    ProbeResult,
    ProvisionResult,
    force_local_active,
    probe_all,
    probe_falkordb,
    probe_qdrant,
    provision_falkordb_graph,
    provision_qdrant_collection,
    render_provision_line,
    setup_remote_falkordb_schema,
)

__all__ = [
    "DEFAULT_FALKORDB_PATH",
    "DEFAULT_INSTANCE_ID",
    "DEFAULT_QDRANT_PATH",
    "STORAGE_SCHEMA_VERSION",
    "BackendConnectionError",
    "BackendMode",
    "ENV_EFFECTIVE_GRAPH_FINGERPRINT",
    "ENV_EFFECTIVE_GRAPH_TARGET",
    "ENV_EFFECTIVE_TOPOLOGY",
    "ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT",
    "ENV_EFFECTIVE_VECTOR_FINGERPRINT",
    "ENV_EFFECTIVE_VECTOR_TARGET",
    "EffectiveStorageTarget",
    "EffectiveStorageTopology",
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
    "RemoteQdrantStore",
    "RemoteStorageConfig",
    "ResolvedStorage",
    "StorageRole",
    "StorageLease",
    "StorageLeaseConflictError",
    "StoreGateway",
    "StoreGatewayError",
    "StoreHealth",
    "MigrationItem",
    "QdrantStore",
    "StorageFactory",
    "assert_owner_stopped",
    "build_filter",
    "canonical_remote_endpoint",
    "create_storage",
    "effective_graph_target_from_env",
    "get_client",
    "get_remote_client",
    "reset_clients",
    "reset_remote_clients",
    "resolve_storage",
    "resolve_performance_profile",
    "ensure_layout",
    "load_manifest",
    "manifest_payload",
    "migrate_legacy_layout",
    "storage_overlay",
    "validate_backend_config",
    "validate_storage_identity",
    "ProbeResult",
    "ProvisionResult",
    "force_local_active",
    "probe_all",
    "probe_falkordb",
    "probe_qdrant",
    "provision_falkordb_graph",
    "provision_qdrant_collection",
    "render_provision_line",
    "setup_remote_falkordb_schema",
]
