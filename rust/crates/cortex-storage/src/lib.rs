//! Rust port of the CortexHarness storage layer
//! (`cortex_harness.storage`).
//!
//! Behavioral parity first: leases reproduce portalocker's `flock`
//! semantics through libc, bounded lanes reproduce the asyncio admission
//! contract (OVERLOADED / DEADLINE_EXCEEDED with identical codes and
//! counters), generation managers reproduce atomic pointer swaps with
//! reader pinning, and config/targets/factory reproduce canonical
//! fingerprints byte-for-byte.
//!
//! Ported modules:
//! - [`lease`]: cross-process `StorageLease` (libc flock) + expired-lease
//!   recovery
//! - [`admission`]: `BoundedLane` / `LaneLimits`
//! - [`generation`]: `GenerationManager` with reader pins and atomic
//!   publication
//! - [`gateway`]: `StoreGateway`, owner lifecycle, ingestion job state
//!   machine
//! - [`config`] / [`targets`] / [`factory`]: env/instance resolution,
//!   `EffectiveStorageTarget`, provider selection, active flip
//! - [`layout`] / [`migration`]: `~/.cortext-harness/v1/instances/...`,
//!   Ladybug store naming, non-destructive migration
//! - [`qdrant`] / [`qdrant_remote`] / [`remote_probe`]: local embedded +
//!   remote REST adapters, health probes
//! - [`contracts`] / [`errors`]: stable codes and typed errors
//! - [`runtime`]: process gateway registry and status projection

pub mod admission;
pub mod config;
pub mod contracts;
pub mod errors;
pub mod factory;
pub mod ffi;
pub mod gateway;
pub mod generation;
pub mod layout;
pub mod lease;
pub mod migration;
pub mod qdrant;
pub mod qdrant_remote;
pub mod remote_probe;
pub mod runtime;
pub mod targets;
pub mod util;

pub use admission::{BoundedLane, LaneLimits, LaneSnapshot};
pub use config::{
    default_data_home, resolve_performance_profile, resolve_storage, storage_overlay,
    validate_backend_config, validate_storage_identity, BackendMode, RemoteStorageConfig,
    ResolvedStorage, ResolveOverrides, StorageRole, DEFAULT_DATA_DIRNAME, DEFAULT_INSTANCE_ID,
    DEFAULT_LADYBUG_GRAPH, ENV_CODE_OWNER, ENV_DATA_HOME, ENV_DOC_OWNER, ENV_FALKORDB_CODE,
    ENV_FALKORDB_DOC, ENV_FALKORDB_PATH, ENV_GRAPH_PROVIDER, ENV_INSTANCE, ENV_LADYBUG_CODE,
    ENV_LADYBUG_DOC, ENV_LADYBUG_PATH, ENV_PERFORMANCE_PROFILE, ENV_QDRANT_BASE, ENV_QDRANT_CODE,
    ENV_QDRANT_DOC, STORAGE_SCHEMA_VERSION,
};
pub use contracts::{
    CaseFoldExt, FreshnessMetadata, GatewayErrorCode, GenerationManifest, GenerationState,
    IngestionJob, IngestionJobState, OwnerLifecycleState, PerformanceProfile, PhysicalTargetKey,
    StoreGatewayError, StoreHealth, MANIFEST_SCHEMA_VERSION,
};
pub use errors::{BackendConnectionError, StoreError, StoreResult};
pub use factory::{create_storage, GraphDriverSelection, QdrantStoreHandle, StorageFactory, TargetSpec, ENV_FORCE_LOCAL};
pub use gateway::{GatewayLimits, StoreGateway, Probe};
pub use generation::{GenerationManager, GenerationPin};
pub use layout::{
    ensure_layout, ladybug_graph_path, ladybug_owner_store_dir, ladybug_store_file_name,
    load_manifest, manifest_payload, LADYBUG_STORE_SUFFIX,
};
pub use lease::{
    assert_owner_stopped, recover_expired_leases, LeaseRecovery, StorageLease,
    StorageLeaseConflictError,
};
pub use migration::{migrate_legacy_layout, MigrationItem};
pub use qdrant::{
    build_filter, get_client, local_native_enabled, reset_clients, LocalQdrantReader,
    LocalQdrantStore, LOCAL_NATIVE_DEFAULT, VECTOR_BACKEND_ENV,
};
pub use qdrant_remote::{get_remote_client, reset_remote_clients, RemoteQdrantStore};
pub use remote_probe::{
    force_local_active, probe_all, probe_falkordb, probe_qdrant, provision_falkordb_graph,
    provision_qdrant_collection, render_provision_line, setup_remote_falkordb_schema,
    ProbeResult, ProvisionResult,
};
pub use runtime::{
    active_gateways, begin_gateway_drain, close_active_gateways, register_gateway,
    storage_runtime_status, store_gateway_enabled, unregister_gateway, ENV_STORE_GATEWAY_ENABLED,
};
pub use targets::{
    canonical_local_target, canonical_remote_endpoint, effective_graph_target_from_env,
    endpoint_uses_tls, environment_flag_enabled, local_graph_target, local_vector_target,
    remote_graph_target, remote_vector_target, EffectiveStorageTarget, EffectiveStorageTopology,
    TARGET_SCHEMA_VERSION,
};
