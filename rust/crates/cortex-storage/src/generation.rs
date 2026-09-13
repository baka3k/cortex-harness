//! Generation manifests, atomic publication, and reader reference tracking.
//!
//! Port of `cortex_harness.storage.generation`. The manager owns one
//! target's active-manifest selection boundary; it never opens graph/vector
//! clients. Readers pin one validated pair and physical cleanup is deferred
//! until all pins for that pair have been released.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::Value;

use crate::admission::lock_recover;
use crate::contracts::{
    GenerationManifest, GenerationState, PhysicalTargetKey, MANIFEST_SCHEMA_VERSION,
};
use crate::errors::{StoreError, StoreResult};
use crate::targets::EffectiveStorageTopology;
use crate::util::{
    canonical_json, fsync_directory, read_optional, resolve_path, rmtree, utc_now, with_suffix,
    write_atomic,
};

pub(crate) type SharedMap = BTreeMap<String, Value>;

struct PublicationState {
    active_cache: Option<GenerationManifest>,
    active_cache_loaded: bool,
    retiring: BTreeSet<String>,
}

/// Own one target's active-manifest selection boundary.
pub struct GenerationManager {
    pub root: PathBuf,
    pub target: PhysicalTargetKey,
    pub retain: usize,
    pub manifest_path: PathBuf,
    pub generations_root: PathBuf,
    pub compatibility_root: PathBuf,
    pub retired_root: PathBuf,
    publication: Mutex<PublicationState>,
    references: Mutex<BTreeMap<String, i64>>,
    storage_compatibility: SharedMap,
    storage_topology: Option<EffectiveStorageTopology>,
}

impl GenerationManager {
    /// `GenerationManager(root, target, ...)`.
    pub fn new(
        root: &Path,
        target: PhysicalTargetKey,
        retain: usize,
        storage_compatibility: Option<SharedMap>,
        storage_topology: Option<EffectiveStorageTopology>,
    ) -> StoreResult<Arc<Self>> {
        if storage_compatibility.is_some() && storage_topology.is_some() {
            return Err(StoreError::Value(
                "provide storage compatibility or topology, not both".to_string(),
            ));
        }
        let root = resolve_path(root);
        Ok(Arc::new(Self {
            manifest_path: root.join("active-generation.json"),
            generations_root: root.join("generations"),
            compatibility_root: root.join("generation-compatibility"),
            retired_root: root.join("retired-generations"),
            root,
            target,
            retain,
            publication: Mutex::new(PublicationState {
                active_cache: None,
                active_cache_loaded: false,
                retiring: BTreeSet::new(),
            }),
            references: Mutex::new(BTreeMap::new()),
            storage_compatibility: storage_compatibility.unwrap_or_default(),
            storage_topology,
        }))
    }

    /// Construct a manager fenced by the factory's effective topology
    /// (`from_storage_factory`).
    pub fn from_storage_factory(
        root: &Path,
        factory: &crate::factory::StorageFactory,
        graph_name: &str,
        collection_name: &str,
        role: &str,
        project_scope: Option<&str>,
        retain: usize,
    ) -> StoreResult<Arc<Self>> {
        let mut role_value = crate::targets::role_value(crate::targets::RoleInput::Str(
            role.to_string(),
        ))?;
        if role_value == "document" {
            role_value = "doc".to_string();
        }
        let owner_id = if role_value == "doc" {
            factory.resolved().doc_owner_id.clone()
        } else {
            factory.resolved().code_owner_id.clone()
        };
        let target = PhysicalTargetKey::from_paths(
            &factory.resolved().instance_id,
            &owner_id,
            &factory
                .resolved()
                .falkordb_path_for_role(&role_value)?,
            factory.resolved().path_for_role(&role_value)?,
        );
        let topology = factory.effective_topology(
            Some(graph_name),
            Some(collection_name),
            &role_value,
            "unbound",
            project_scope,
        )?;
        Self::new(root, target, retain, None, Some(topology))
    }

    fn safe_generation_id(generation_id: &str) -> StoreResult<String> {
        let value = generation_id.to_string();
        let bytes = value.as_bytes();
        let valid = !bytes.is_empty()
            && bytes.len() <= 128
            && bytes[0].is_ascii_alphanumeric()
            && bytes[1..]
                .iter()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'));
        if !valid {
            return Err(StoreError::Value(
                "generation ID is not safe for compatibility metadata".to_string(),
            ));
        }
        Ok(value)
    }

    fn compatibility_path(&self, generation_id: &str) -> StoreResult<PathBuf> {
        Ok(self
            .compatibility_root
            .join(format!("{}.json", Self::safe_generation_id(generation_id)?)))
    }

    /// Durably fence a generation without deleting or pointer-flipping it.
    pub fn mark_incompatible(
        &self,
        generation_id: &str,
        reason: &str,
        provenance: &str,
    ) -> StoreResult<PathBuf> {
        if reason.trim().is_empty() {
            return Err(StoreError::Value(
                "generation incompatibility requires a reason".to_string(),
            ));
        }
        let path = self.compatibility_path(generation_id)?;
        let payload = serde_json::json!({
            "schema_version": "1",
            "generation_id": generation_id,
            "compatible": false,
            "reason": reason,
            "provenance": provenance,
        });
        {
            let mut publication = lock_recover(&self.publication);
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(StoreError::Io)?;
            }
            write_atomic(&path, &canonical_json(&payload))?;
            if publication
                .active_cache
                .as_ref()
                .is_some_and(|active| active.generation_id == generation_id)
            {
                publication.active_cache = None;
                publication.active_cache_loaded = false;
            }
        }
        Ok(path)
    }

    /// Load the incompatibility marker for a generation, when present.
    pub fn incompatibility(&self, generation_id: &str) -> StoreResult<Option<Value>> {
        let path = self.compatibility_path(generation_id)?;
        let Some(text) = read_optional(&path).map_err(StoreError::Io)? else {
            return Ok(None);
        };
        let payload: Value = serde_json::from_str(&text)
            .map_err(|err| StoreError::Value(format!("invalid compatibility marker: {err}")))?;
        if payload.get("compatible").and_then(Value::as_bool) != Some(false) {
            return Err(StoreError::Value(
                "invalid generation compatibility marker".to_string(),
            ));
        }
        Ok(Some(payload))
    }

    fn storage_compatibility_for(&self, generation_id: &str) -> StoreResult<SharedMap> {
        if let Some(topology) = &self.storage_topology {
            return Ok(topology
                .for_generation(generation_id)?
                .compatibility_metadata()
                .into_iter()
                .collect());
        }
        Ok(self.storage_compatibility.clone())
    }

    /// Allocate a new BUILDING generation manifest (`allocate`).
    pub fn allocate(
        &self,
        source_revision: &str,
        generation_id: Option<&str>,
    ) -> StoreResult<GenerationManifest> {
        let generated;
        let generation_id = Self::safe_generation_id(match generation_id {
            Some(id) => id,
            None => {
                generated = uuid::Uuid::new_v4().simple().to_string();
                generated.as_str()
            }
        })?;
        let generation_root = self.generations_root.join(&generation_id);
        let storage_compatibility = self.storage_compatibility_for(&generation_id)?;
        let manifest = GenerationManifest {
            generation_id: generation_id.clone(),
            target: self.target.clone(),
            source_revision: source_revision.to_string(),
            graph_path: generation_root
                .join("graph")
                .join("data.rdb")
                .to_string_lossy()
                .into_owned(),
            vector_path: generation_root
                .join("vector")
                .to_string_lossy()
                .into_owned(),
            state: GenerationState::Building,
            created_at: utc_now(),
            validated_at: None,
            published_at: None,
            retired_at: None,
            validation: if storage_compatibility.is_empty() {
                serde_json::Map::new()
            } else {
                storage_compatibility.clone().into_iter().collect()
            },
            schema_version: MANIFEST_SCHEMA_VERSION,
        };
        self.write_generation_record(&manifest)?;
        Ok(manifest)
    }

    fn validate_storage_target(&self, manifest: &GenerationManifest) -> StoreResult<()> {
        let expected = self.storage_compatibility_for(&manifest.generation_id)?;
        if expected.is_empty() {
            return Ok(());
        }
        let actual = manifest.validation.get("storage_compatibility");
        let Some(actual) = actual else {
            return Err(StoreError::Value(
                "generation manifest predates effective storage topology metadata; re-ingest \
                 from source instead of migrating it in place"
                    .to_string(),
            ));
        };
        let actual_map = actual.as_object().ok_or_else(|| {
            StoreError::Value(
                "generation manifest does not match the effective storage topology".to_string(),
            )
        })?;
        if canonical_json(&Value::Object(actual_map.clone()))
            != canonical_json(&Value::Object(expected.clone().into_iter().collect()))
        {
            return Err(StoreError::Value(
                "generation manifest does not match the effective storage topology".to_string(),
            ));
        }
        Ok(())
    }

    fn load_active_unlocked(&self, publication: &mut PublicationState) -> StoreResult<Option<GenerationManifest>> {
        if publication.active_cache_loaded {
            return Ok(publication.active_cache.clone());
        }
        let Some(text) = read_optional(&self.manifest_path).map_err(StoreError::Io)? else {
            publication.active_cache_loaded = true;
            publication.active_cache = None;
            return Ok(None);
        };
        let payload: Value = serde_json::from_str(&text)
            .map_err(|err| StoreError::Value(format!("invalid active generation manifest: {err}")))?;
        let manifest = GenerationManifest::from_value(&payload)?;
        if manifest.target != self.target || manifest.state != GenerationState::Published {
            return Err(StoreError::Value(
                "active generation manifest does not describe this published physical target"
                    .to_string(),
            ));
        }
        self.validate_storage_target(&manifest)?;
        self.validate_paths(&manifest)?;
        if self.incompatibility(&manifest.generation_id)?.is_some() {
            return Err(StoreError::Value(
                "active generation is structurally incompatible".to_string(),
            ));
        }
        publication.active_cache = Some(manifest.clone());
        publication.active_cache_loaded = true;
        Ok(Some(manifest))
    }

    /// Return the cached active selection (`load_active`).
    pub fn load_active(&self) -> StoreResult<Option<GenerationManifest>> {
        let mut publication = lock_recover(&self.publication);
        self.load_active_unlocked(&mut publication)
    }

    /// Recover the authoritative manifest and discard an abandoned temp file
    /// (`recover`).
    pub fn recover(&self) -> StoreResult<Option<GenerationManifest>> {
        let temporary = with_suffix(&self.manifest_path, ".tmp");
        let mut publication = lock_recover(&self.publication);
        publication.active_cache = None;
        publication.active_cache_loaded = false;
        let active = self.load_active_unlocked(&mut publication)?;
        match std::fs::remove_file(&temporary) {
            Ok(()) => {}
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
            Err(err) => return Err(StoreError::Io(err)),
        }
        Ok(active)
    }

    /// Validate and atomically make one staged graph/vector pair active
    /// (`publish`).
    pub fn publish(
        &self,
        manifest: &GenerationManifest,
        validate: impl Fn(&GenerationManifest) -> StoreResult<()>,
    ) -> StoreResult<GenerationManifest> {
        if manifest.target != self.target {
            return Err(StoreError::Value(
                "cannot publish a generation for a different physical target".to_string(),
            ));
        }
        self.validate_storage_target(manifest)?;
        self.validate_paths(manifest)?;
        if self.incompatibility(&manifest.generation_id)?.is_some() {
            return Err(StoreError::Value(
                "cannot publish a structurally incompatible generation".to_string(),
            ));
        }
        validate(manifest)?;
        // Re-check after the caller-controlled callback, then overwrite the
        // reserved envelope from the manager before serialization.
        self.validate_storage_target(manifest)?;
        let mut published_validation = manifest.validation.clone();
        let storage_compatibility = self.storage_compatibility_for(&manifest.generation_id)?;
        if !storage_compatibility.is_empty() {
            published_validation.insert(
                "storage_compatibility".to_string(),
                Value::Object(storage_compatibility.clone().into_iter().collect()),
            );
        }
        let published = GenerationManifest {
            state: GenerationState::Published,
            validated_at: Some(manifest.validated_at.clone().unwrap_or_else(utc_now)),
            published_at: Some(utc_now()),
            validation: published_validation,
            ..manifest.clone()
        };
        self.validate_storage_target(&published)?;
        {
            let _publication = lock_recover(&self.publication);
            if self
                .retired_path(&published.generation_id)
                .is_ok_and(|path| path.is_file())
            {
                return Err(StoreError::Value(
                    "cannot publish a retired or missing generation".to_string(),
                ));
            }
        }
        // Persist all diagnostic/state material before the authoritative
        // pointer swap so a failure leaves the previous active generation
        // selected.
        self.write_generation_record(&published)?;
        let mut publication = lock_recover(&self.publication);
        // Only the final target/state recheck and durable pointer swap are
        // serialized with readers selecting a generation.
        self.validate_storage_target(&published)?;
        self.validate_paths(&published)?;
        if publication.retiring.contains(&published.generation_id) {
            return Err(StoreError::Value(
                "cannot publish a generation while it is retiring".to_string(),
            ));
        }
        if self
            .retired_path(&published.generation_id)
            .is_ok_and(|path| path.is_file())
        {
            return Err(StoreError::Value(
                "cannot publish a retired or missing generation".to_string(),
            ));
        }
        if !self
            .generation_record_path(&published)?
            .is_file()
        {
            return Err(StoreError::Value(
                "cannot publish a retired or missing generation".to_string(),
            ));
        }
        if self.incompatibility(&published.generation_id)?.is_some() {
            return Err(StoreError::Value(
                "cannot publish a structurally incompatible generation".to_string(),
            ));
        }
        self.write_active_manifest_unlocked(&mut publication, &published)?;
        publication.active_cache = Some(published.clone());
        publication.active_cache_loaded = true;
        Ok(published)
    }

    /// Atomically persist one already-validated active manifest.
    pub fn write_active_manifest(&self, manifest: &GenerationManifest) -> StoreResult<()> {
        let mut publication = lock_recover(&self.publication);
        self.write_active_manifest_unlocked(&mut publication, manifest)?;
        publication.active_cache = Some(manifest.clone());
        publication.active_cache_loaded = true;
        Ok(())
    }

    fn write_active_manifest_unlocked(
        &self,
        _publication: &mut PublicationState,
        manifest: &GenerationManifest,
    ) -> StoreResult<()> {
        std::fs::create_dir_all(&self.root).map_err(StoreError::Io)?;
        write_atomic(&self.manifest_path, &manifest.to_json_string())?;
        fsync_directory(&self.root).map_err(StoreError::Io)?;
        Ok(())
    }

    fn write_generation_record(&self, manifest: &GenerationManifest) -> StoreResult<()> {
        let generation_root = self.generation_root(manifest)?;
        std::fs::create_dir_all(&generation_root).map_err(StoreError::Io)?;
        let target = self.generation_record_path(manifest)?;
        write_atomic(&target, &manifest.to_json_string())?;
        fsync_directory(&generation_root).map_err(StoreError::Io)?;
        Ok(())
    }

    fn generation_record_path(&self, manifest: &GenerationManifest) -> StoreResult<PathBuf> {
        Ok(self.generation_root(manifest)?.join("generation.json"))
    }

    fn generation_root(&self, manifest: &GenerationManifest) -> StoreResult<PathBuf> {
        let generation_id = Self::safe_generation_id(&manifest.generation_id)?;
        let lexical_root = self.generations_root.join(&generation_id);
        if lexical_root.is_symlink() {
            return Err(StoreError::Value(
                "generation root cannot be a symbolic link".to_string(),
            ));
        }
        let resolved_root = resolve_path(&lexical_root);
        let resolved_generations = resolve_path(&self.generations_root);
        if !resolved_root.starts_with(&resolved_generations) {
            return Err(StoreError::Value(
                "generation root must remain below the generation directory".to_string(),
            ));
        }
        Ok(resolved_root)
    }

    fn validate_paths(&self, manifest: &GenerationManifest) -> StoreResult<()> {
        let generation_root = self.generation_root(manifest)?;
        let graph_path = resolve_path(Path::new(&manifest.graph_path));
        let vector_path = resolve_path(Path::new(&manifest.vector_path));
        let expected_graph = generation_root.join("graph").join("data.rdb");
        let expected_vector = generation_root.join("vector");
        if graph_path != expected_graph || vector_path != expected_vector {
            return Err(StoreError::Value(
                "generation paths must be isolated below their generation ID".to_string(),
            ));
        }
        Ok(())
    }

    /// Pin the active generation for reading (`pin_active`).
    ///
    /// Publication -> reference lock order makes selection and pinning one
    /// atomic lifecycle action. The returned pin decrements the reference
    /// count when dropped; retirement waits for pins to drain.
    pub fn pin_active(&self) -> StoreResult<GenerationPin<'_>> {
        let manifest = {
            let mut publication = lock_recover(&self.publication);
            let loaded = self.load_active_unlocked(&mut publication)?;
            let Some(manifest) = loaded else {
                return Err(StoreError::Runtime(
                    "no active generation is available".to_string(),
                ));
            };
            let mut references = lock_recover(&self.references);
            *references
                .entry(manifest.generation_id.clone())
                .or_insert(0) += 1;
            manifest
        };
        Ok(GenerationPin {
            manager: self,
            manifest,
        })
    }

    /// Current pin count for one generation (`reference_count`).
    pub fn reference_count(&self, generation_id: &str) -> i64 {
        lock_recover(&self.references)
            .get(generation_id)
            .copied()
            .unwrap_or(0)
    }

    /// Remove a non-active generation only after readers have drained
    /// (`retire`).
    pub fn retire(&self, manifest: &GenerationManifest) -> StoreResult<bool> {
        self.validate_paths(manifest)?;
        {
            let mut publication = lock_recover(&self.publication);
            let active = self.load_active_unlocked(&mut publication)?;
            if active
                .as_ref()
                .is_some_and(|active| active.generation_id == manifest.generation_id)
            {
                return Ok(false);
            }
            {
                let references = lock_recover(&self.references);
                if references
                    .get(&manifest.generation_id)
                    .copied()
                    .unwrap_or(0)
                    > 0
                {
                    return Ok(false);
                }
            }
            if publication.retiring.contains(&manifest.generation_id) {
                return Ok(false);
            }
            self.write_retired_tombstone_unlocked(manifest)?;
            publication.retiring.insert(manifest.generation_id.clone());
        }
        let generation_root = self.generation_root(manifest)?;
        let result = rmtree(&generation_root);
        {
            let mut publication = lock_recover(&self.publication);
            publication.retiring.remove(&manifest.generation_id);
        }
        result.map(|()| true).map_err(StoreError::Io)
    }

    fn retired_path(&self, generation_id: &str) -> StoreResult<PathBuf> {
        Ok(self
            .retired_root
            .join(format!("{}.json", Self::safe_generation_id(generation_id)?)))
    }

    fn write_retired_tombstone_unlocked(&self, manifest: &GenerationManifest) -> StoreResult<()> {
        // Fence a generation ID durably while publication is excluded.
        std::fs::create_dir_all(&self.retired_root).map_err(StoreError::Io)?;
        let target = self.retired_path(&manifest.generation_id)?;
        let payload = serde_json::json!({
            "schema_version": 1,
            "generation_id": manifest.generation_id,
            "retired_at": utc_now(),
        });
        write_atomic(&target, &canonical_json(&payload))?;
        fsync_directory(&self.retired_root).map_err(StoreError::Io)?;
        Ok(())
    }

    /// Fail before staging when required bytes plus safety headroom do not
    /// fit (`ensure_disk_capacity`).
    pub fn ensure_disk_capacity(&self, required_bytes: i64) -> StoreResult<()> {
        if required_bytes < 0 {
            return Err(StoreError::Value(
                "required_bytes cannot be negative".to_string(),
            ));
        }
        let mut probe = if self.root.exists() {
            self.root.clone()
        } else {
            self.root
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from("/"))
        };
        while !probe.exists() && probe.as_path() != probe.parent().unwrap_or_else(|| Path::new("/")) {
            probe = probe
                .parent()
                .map(Path::to_path_buf)
                .unwrap_or_else(|| PathBuf::from("/"));
        }
        let free_bytes = crate::ffi::disk_free_bytes(&probe).map_err(StoreError::Io)? as i64;
        let safety_bytes = (required_bytes as f64 * 0.20) as i64;
        let total_required = required_bytes + safety_bytes;
        if free_bytes < total_required {
            return Err(StoreError::Io(std::io::Error::other(format!(
                "insufficient staging disk: required={total_required} free={free_bytes}"
            ))));
        }
        Ok(())
    }
}

/// A held reference to the active generation (`pin_active` context).
pub struct GenerationPin<'a> {
    manager: &'a GenerationManager,
    manifest: GenerationManifest,
}

impl GenerationPin<'_> {
    pub fn manifest(&self) -> &GenerationManifest {
        &self.manifest
    }

    pub fn generation_id(&self) -> &str {
        &self.manifest.generation_id
    }
}

impl std::ops::Deref for GenerationPin<'_> {
    type Target = GenerationManifest;

    fn deref(&self) -> &Self::Target {
        &self.manifest
    }
}

impl Drop for GenerationPin<'_> {
    fn drop(&mut self) {
        let mut references = lock_recover(&self.manager.references);
        if let Some(current) = references.get_mut(&self.manifest.generation_id) {
            if *current <= 1 {
                references.remove(&self.manifest.generation_id);
            } else {
                *current -= 1;
            }
        }
    }
}
