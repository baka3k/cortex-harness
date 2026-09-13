//! Storage factory — single backend-resolution entry point.
//!
//! Port of `cortex_harness.storage.factory`. The factory resolves the
//! effective graph/vector targets and selects concrete backend adapters.
//! Driver construction for embedded FalkorDB/LadybugDB engines lives in the
//! Python `tools.graph.driver` layer and is out of crate scope: this port
//! returns a `GraphDriverSelection` describing what would be constructed
//! (same target identity, lease identity, and provider-selection errors).


use crate::config::{validate_backend_config, BackendMode, RemoteStorageConfig, ResolvedStorage};
use crate::errors::{StoreError, StoreResult};
use crate::qdrant::LocalQdrantStore;
use crate::qdrant_remote::RemoteQdrantStore;
use crate::targets::{
    environment_flag_enabled, local_graph_target, local_vector_target, remote_graph_target,
    remote_vector_target, EffectiveStorageTarget, EffectiveStorageTopology, RoleInput,
};

/// Emergency rollback: force every project onto the local backend even when
/// `storage_backend` says otherwise.
pub const ENV_FORCE_LOCAL: &str = "CORTEX_STORAGE_BACKEND_FORCE_LOCAL";

/// Minimal project-target descriptor mirroring the fields the Python factory
/// consumes from `ProjectTargets`.
#[derive(Debug, Clone, Default)]
pub struct TargetSpec {
    pub storage_backend: String,
    pub project_id: String,
    pub remote_config: Option<serde_json::Map<String, serde_json::Value>>,
    pub provider: Option<String>,
    pub code_graph: Option<String>,
    pub doc_graph: Option<String>,
    pub code_qdrant_collection: Option<String>,
    pub doc_qdrant_collection: Option<String>,
}

/// Which graph driver the Python factory would construct.
#[derive(Debug, Clone, PartialEq)]
pub enum GraphDriverSelection {
    /// Remote FalkorDB server (`FalkorDBDriver(uri=..., graph=...)`).
    RemoteFalkordb {
        uri: String,
        password: Option<String>, // sensitive-guard:allow (ten flag / test sample)
        ssl: bool,
        graph: String,
    },
    /// Embedded FalkorDBLite `.rdb` file (`FalkorDBDriver(path=...)`).
    LocalFalkordb {
        path: String,
        graph: String,
        owner_id: String,
        instance_id: String,
    },
    /// Embedded LadybugDB store file (`LadybugDriver(path=...)`).
    Ladybug {
        path: String,
        graph: String,
        owner_id: String,
        instance_id: String,
    },
    /// Remote Neo4j bolt server.
    RemoteNeo4j {
        uri: String,
        graph: String,
    },
}

/// The Qdrant adapter selected for a role.
#[allow(clippy::large_enum_variant)]
#[derive(Debug)]
pub enum QdrantStoreHandle {
    Local(LocalQdrantStore),
    Remote(RemoteQdrantStore),
}

impl QdrantStoreHandle {
    pub fn list_collection_names(&self) -> StoreResult<Vec<String>> {
        match self {
            QdrantStoreHandle::Local(store) => store.list_collection_names(),
            QdrantStoreHandle::Remote(store) => store.list_collection_names(),
        }
    }

    pub fn close(&self) {
        match self {
            QdrantStoreHandle::Local(store) => store.close(),
            QdrantStoreHandle::Remote(store) => store.close(),
        }
    }
}

/// Resolve the correct backend instances for one project.
pub struct StorageFactory {
    requested_mode: BackendMode,
    mode: BackendMode,
    forced_local: bool,
    remote: Option<RemoteStorageConfig>,
    resolved: ResolvedStorage,
    project_scope: String,
    graph_provider: String,
    code_graph: Option<String>,
    doc_graph: Option<String>,
    code_collection: Option<String>,
    doc_collection: Option<String>,
}

impl StorageFactory {
    /// `StorageFactory(backend_mode=..., resolved=..., ...)`.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        backend_mode: BackendMode,
        resolved: &ResolvedStorage,
        remote: Option<RemoteStorageConfig>,
        project_scope: &str,
        graph_provider: &str,
        code_graph: Option<String>,
        doc_graph: Option<String>,
        code_collection: Option<String>,
        doc_collection: Option<String>,
    ) -> StoreResult<Self> {
        // Emergency rollback: force local even when remote is requested.
        let forced_local = environment_flag_enabled(
            std::env::var(ENV_FORCE_LOCAL).ok().as_deref(),
        );
        let (mode, remote) = if forced_local {
            (BackendMode::Local, None)
        } else {
            let remote = if backend_mode == BackendMode::Remote {
                remote
            } else {
                None
            };
            (backend_mode, remote)
        };
        let mut provider = graph_provider.trim().to_lowercase();
        if matches!(provider.as_str(), "kuzu" | "lbug" | "lady-bug") {
            provider = "ladybug".to_string();
        }
        let factory = Self {
            requested_mode: backend_mode,
            mode,
            forced_local,
            remote,
            resolved: resolved.clone(),
            project_scope: if project_scope.trim().is_empty() {
                "unbound".to_string()
            } else {
                project_scope.to_string()
            },
            graph_provider: provider,
            code_graph: code_graph.or_else(|| resolved.code_graph.clone()),
            doc_graph: doc_graph.or_else(|| resolved.doc_graph.clone()),
            code_collection: code_collection.or_else(|| resolved.code_collection.clone()),
            doc_collection: doc_collection.or_else(|| resolved.doc_collection.clone()),
        };
        Ok(factory)
    }

    /// Build a factory from a parsed target spec plus resolved paths
    /// (`from_targets`).
    pub fn from_targets(targets: &TargetSpec, resolved: &ResolvedStorage) -> StoreResult<Self> {
        let Some(mode) = BackendMode::parse(&targets.storage_backend) else {
            return Err(StoreError::Value(format!(
                "unknown storage_backend {:?} on project {:?}",
                targets.storage_backend, targets.project_id
            )));
        };
        let remote = if mode == BackendMode::Remote {
            validate_backend_config("remote", targets.remote_config.as_ref(), "falkordb")?.1
        } else {
            None
        };
        Self::new(
            mode,
            resolved,
            remote,
            &targets.project_id,
            targets.provider.as_deref().unwrap_or("falkordb"),
            targets.code_graph.clone(),
            targets.doc_graph.clone(),
            targets.code_qdrant_collection.clone(),
            targets.doc_qdrant_collection.clone(),
        )
    }

    pub fn backend_mode(&self) -> BackendMode {
        self.mode
    }

    pub fn resolved(&self) -> &ResolvedStorage {
        &self.resolved
    }

    pub fn requested_backend_mode(&self) -> BackendMode {
        self.requested_mode
    }

    pub fn forced_local(&self) -> bool {
        self.forced_local
    }

    pub fn is_remote(&self) -> bool {
        self.mode == BackendMode::Remote
    }

    pub fn graph_provider(&self) -> &str {
        &self.graph_provider
    }

    fn role(role: &str) -> StoreResult<String> {
        crate::targets::role_value(RoleInput::Str(role.to_string()))
    }

    /// Describe the graph target selected before any connection attempt.
    pub fn effective_graph_target(
        &self,
        graph_name: Option<&str>,
        role: &str,
    ) -> StoreResult<EffectiveStorageTarget> {
        let role_value = Self::role(role)?;
        let namespace = match graph_name {
            Some(name) => Some(name.to_string()),
            None => {
                if role_value == "doc" {
                    self.doc_graph.clone()
                } else {
                    self.code_graph.clone()
                }
            }
        };
        let Some(namespace) = namespace.filter(|name| !name.is_empty()) else {
            return Err(StoreError::Value(
                "effective graph target requires a graph name".to_string(),
            ));
        };
        if self.graph_provider == "ladybug" {
            // Embedded-only provider: always a file target on the Ladybug
            // store, never a remote endpoint.
            return local_graph_target(
                self.resolved.ladybug_path_for_role(&role_value)?,
                &namespace,
                RoleInput::Str(role_value),
                "ladybug",
            );
        }
        if self.mode == BackendMode::Remote
            && self
                .remote
                .as_ref()
                .and_then(|remote| remote.falkordb_uri.as_deref())
                .is_some_and(|uri| !uri.is_empty())
        {
            let remote = self.remote.as_ref().unwrap();
            return remote_graph_target(
                remote.falkordb_uri.as_deref().unwrap_or_default(),
                &namespace,
                RoleInput::Str(role_value),
                remote.falkordb_password.as_deref(), // sensitive-guard:allow (ten flag / test sample)
                None,
                remote.falkordb_ssl,
                "falkordb",
            );
        }
        local_graph_target(
            self.resolved.falkordb_path_for_role(&role_value)?,
            &namespace,
            RoleInput::Str(role_value),
            "falkordb",
        )
    }

    /// Describe the effective Qdrant server or local directory.
    pub fn effective_vector_target(
        &self,
        collection_name: Option<&str>,
        role: &str,
    ) -> StoreResult<EffectiveStorageTarget> {
        let role_value = Self::role(role)?;
        let namespace = match collection_name {
            Some(name) => Some(name.to_string()),
            None => {
                if role_value == "doc" {
                    self.doc_collection.clone()
                } else {
                    self.code_collection.clone()
                }
            }
        };
        let Some(namespace) = namespace.filter(|name| !name.is_empty()) else {
            return Err(StoreError::Value(
                "effective vector target requires a collection name".to_string(),
            ));
        };
        if self.mode == BackendMode::Remote
            && self
                .remote
                .as_ref()
                .and_then(|remote| remote.qdrant_url.as_deref())
                .is_some_and(|url| !url.is_empty())
        {
            let remote = self.remote.as_ref().unwrap();
            return remote_vector_target(
                remote.qdrant_url.as_deref().unwrap_or_default(),
                &namespace,
                RoleInput::Str(role_value),
                remote.qdrant_api_key.as_deref(),
            );
        }
        local_vector_target(
            self.resolved.path_for_role(&role_value)?,
            &namespace,
            RoleInput::Str(role_value),
        )
    }

    /// Return the canonical graph/vector topology used for compatibility.
    #[allow(clippy::too_many_arguments)]
    pub fn effective_topology(
        &self,
        graph_name: Option<&str>,
        collection_name: Option<&str>,
        role: &str,
        generation_id: &str,
        project_scope: Option<&str>,
    ) -> StoreResult<EffectiveStorageTopology> {
        EffectiveStorageTopology::create(
            project_scope.unwrap_or(&self.project_scope),
            self.requested_mode.as_str(),
            self.forced_local,
            if generation_id.is_empty() {
                "unbound"
            } else {
                generation_id
            },
            self.effective_graph_target(graph_name, role)?,
            self.effective_vector_target(collection_name, role)?,
        )
    }

    /// Return a Qdrant store for `role` using the project's backend
    /// (`get_qdrant_store`).
    ///
    /// Falls back to local when `storage_backend == remote` but
    /// `remote.qdrant_url` is unset.
    pub fn get_qdrant_store(&self, role: &str) -> StoreResult<QdrantStoreHandle> {
        let role_value = Self::role(role)?;
        if self.mode == BackendMode::Remote
            && self
                .remote
                .as_ref()
                .and_then(|remote| remote.qdrant_url.as_deref())
                .is_some_and(|url| !url.is_empty())
        {
            let remote = self.remote.as_ref().unwrap();
            return Ok(QdrantStoreHandle::Remote(RemoteQdrantStore::new(
                remote.qdrant_url.as_deref().unwrap_or_default(),
                remote.qdrant_api_key.as_deref(),
                None,
                Some(&role_value),
            )?));
        }
        Ok(QdrantStoreHandle::Local(LocalQdrantStore::open(
            &self.resolved,
            &role_value,
        )?))
    }

    /// Which graph driver would be constructed for `graph_name`
    /// (`get_falkordb_driver` / `get_ladybug_driver` selection semantics).
    pub fn graph_driver_selection(
        &self,
        graph_name: &str,
        role: &str,
    ) -> StoreResult<GraphDriverSelection> {
        let role_value = Self::role(role)?;
        let owner_id = if role_value == "doc" {
            self.resolved.doc_owner_id.clone()
        } else {
            self.resolved.code_owner_id.clone()
        };
        let remote_uri = self
            .remote
            .as_ref()
            .and_then(|remote| remote.falkordb_uri.as_deref())
            .filter(|uri| !uri.is_empty());
        if self.graph_provider == "ladybug" {
            // Ladybug is local-only: a remote FalkorDB selection fails closed.
            if remote_uri.is_some() && self.mode == BackendMode::Remote {
                return Err(StoreError::Value(
                    "ladybug is local-only; the project selects a remote FalkorDB server. Use \
                     get_falkordb_driver or unset remote.falkordb_uri."
                        .to_string(),
                ));
            }
            return Ok(GraphDriverSelection::Ladybug {
                path: self
                    .resolved
                    .ladybug_path_for_role(&role_value)?
                    .to_string_lossy()
                    .into_owned(),
                graph: graph_name.to_string(),
                owner_id,
                instance_id: self.resolved.instance_id.clone(),
            });
        }
        if let Some(uri) = remote_uri
            .filter(|_| self.mode == BackendMode::Remote)
        {
            let remote = self.remote.as_ref().unwrap();
            return Ok(GraphDriverSelection::RemoteFalkordb {
                uri: uri.to_string(),
                password: remote.falkordb_password.clone(), // sensitive-guard:allow (ten flag / test sample)
                ssl: remote.falkordb_ssl,
                graph: graph_name.to_string(),
            });
        }
        Ok(GraphDriverSelection::LocalFalkordb {
            path: self
                .resolved
                .falkordb_path_for_role(&role_value)?
                .to_string_lossy()
                .into_owned(),
            graph: graph_name.to_string(),
            owner_id,
            instance_id: self.resolved.instance_id.clone(),
        })
    }
}

/// One-call factory used by ingest scripts and MCP tools (`create_storage`).
pub fn create_storage(
    targets: &TargetSpec,
    project_root: Option<&std::path::Path>,
    resolved: Option<&ResolvedStorage>,
) -> StoreResult<StorageFactory> {
    let owned;
    let resolved_ref = match resolved {
        Some(existing) => existing,
        None => {
            let root = project_root.unwrap_or_else(|| std::path::Path::new("."));
            owned = crate::config::resolve_storage(root, None, &Default::default())?;
            &owned
        }
    };
    StorageFactory::from_targets(targets, resolved_ref)
}


