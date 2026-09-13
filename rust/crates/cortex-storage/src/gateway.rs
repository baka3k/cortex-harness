//! Single-process owner for bounded generation-pinned store access.
//!
//! Port of `cortex_harness.storage.gateway`. The Python gateway is asyncio
//! based; this port is blocking-thread based but preserves the observable
//! contract: per-lane bounded admission, owner lifecycle, generation pinning,
//! ingestion job state machine with durable recovery, and health/metrics
//! projections.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
use std::thread_local;
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use crate::admission::{lock_recover, BoundedLane, LaneLimits};
use crate::contracts::{
    FreshnessMetadata, GatewayErrorCode, GenerationManifest, IngestionJob, JobChanges,
    IngestionJobState, OwnerLifecycleState, PerformanceProfile, PhysicalTargetKey,
    StoreGatewayError, StoreHealth,
};
use crate::errors::{StoreError, StoreResult};
use crate::generation::GenerationManager;
use crate::lease::StorageLease;
use crate::util::{canonical_json, fsync_directory, utc_now, write_atomic};

thread_local! {
    /// The serialized gateway lane executing on this thread (`_execution_context`).
    static CURRENT_LANE: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

fn set_current_lane(lane: &str) -> Option<String> {
    CURRENT_LANE.with(|cell| cell.borrow_mut().replace(lane.to_string()))
}

fn clear_current_lane(previous: Option<String>) {
    CURRENT_LANE.with(|cell| *cell.borrow_mut() = previous);
}

/// Run `body` as if executing on the named lane.
pub fn run_in_lane<T>(lane: &str, body: impl FnOnce() -> StoreResult<T>) -> StoreResult<T> {
    let previous = set_current_lane(lane);
    let result = body();
    clear_current_lane(previous);
    result
}

/// Allowed ingestion-job transitions (`_JOB_TRANSITIONS`).
pub fn job_transitions(state: IngestionJobState) -> BTreeSet<IngestionJobState> {
    use IngestionJobState::*;
    match state {
        Queued => [Preparing, Writing, Cancelled, Failed, Superseded]
            .into_iter()
            .collect(),
        Preparing => [Writing, Cancelled, Failed, Superseded].into_iter().collect(),
        Writing => [Validating, Cancelled, Failed, Ambiguous].into_iter().collect(),
        Validating => [Publishing, Cancelled, Failed, Ambiguous].into_iter().collect(),
        Publishing => [Completed, Failed, Ambiguous].into_iter().collect(),
        _ => BTreeSet::new(),
    }
}

/// Gateway lane budgets (`GatewayLimits`).
#[derive(Debug, Clone)]
pub struct GatewayLimits {
    pub graph_read: LaneLimits,
    pub vector_read: LaneLimits,
    pub write: LaneLimits,
    pub control: LaneLimits,
    pub drain_timeout_seconds: f64,
}

impl Default for GatewayLimits {
    fn default() -> Self {
        Self {
            graph_read: LaneLimits::default(),
            vector_read: LaneLimits::default(),
            write: LaneLimits::new(1, 8, 4 * 1024 * 1024),
            control: LaneLimits::new(1, 4, 4 * 1024 * 1024),
            drain_timeout_seconds: 30.0,
        }
    }
}

impl GatewayLimits {
    pub fn validate(&self) -> StoreResult<()> {
        if self.drain_timeout_seconds <= 0.0 {
            return Err(StoreError::Value(
                "gateway drain timeout must be positive".to_string(),
            ));
        }
        Ok(())
    }

    /// Translate the sole validated performance object into lane budgets
    /// (`from_profile`).
    pub fn from_profile(profile: &PerformanceProfile) -> Self {
        Self {
            graph_read: LaneLimits::new(
                profile.graph_readers.max(1) as usize,
                profile.max_queue_items.max(0) as usize,
                profile.max_queue_bytes,
            ),
            vector_read: LaneLimits::new(
                profile.vector_readers.max(1) as usize,
                profile.max_queue_items.max(0) as usize,
                profile.max_queue_bytes,
            ),
            write: LaneLimits::new(
                profile.writer_slots.max(1) as usize,
                (profile.max_queue_items / 4).max(1) as usize,
                profile.max_queue_bytes,
            ),
            control: LaneLimits::new(
                profile.control_slots.max(1) as usize,
                (profile.max_queue_items / 8).max(1) as usize,
                profile.max_queue_bytes,
            ),
            drain_timeout_seconds: (profile.request_timeout_seconds * 2.0).max(1.0),
        }
    }
}

/// A synchronous readiness probe supplied by the adapter layer.
pub type Probe = Arc<dyn Fn(&GenerationManifest) -> StoreResult<()> + Send + Sync>;

/// A gateway-owned backend handle closed during drain.
pub trait GatewayResource: Send {
    fn close(&mut self) -> StoreResult<()>;
}

/// Resource registry entry: (key, lane, handle).
type ResourceEntry = (String, String, Box<dyn GatewayResource>);

#[derive(Default)]
struct JobBook {
    jobs: Vec<IngestionJob>,
    keys: BTreeMap<(String, String), String>,
    accepting_jobs: bool,
}

struct ProbeState {
    generation: Option<String>,
    last_probe_at: Option<String>,
    error: Option<String>,
}

/// Coordinates one physical graph/vector target without opening clients.
///
/// Backend adapters pass an operation that uses a manifest's explicit paths;
/// the gateway never silently acquires a second client for an active target.
pub struct StoreGateway {
    pub target: PhysicalTargetKey,
    pub limits: GatewayLimits,
    pub generations: Arc<GenerationManager>,
    lifecycle: Mutex<OwnerLifecycleState>,
    leases: Mutex<Vec<StorageLease>>,
    jobs: Mutex<JobBook>,
    job_changed: std::sync::Condvar,
    job_persist_lock: Mutex<()>,
    jobs_path: std::path::PathBuf,
    active_generation: OnceLock<Mutex<Option<GenerationManifest>>>,
    graph_probe: Option<Probe>,
    vector_probe: Option<Probe>,
    probe: Mutex<ProbeState>,
    lanes: BTreeMap<String, BoundedLane>,
    started: AtomicBool,
    resources: Mutex<Vec<ResourceEntry>>,
}

impl StoreGateway {
    /// `StoreGateway(target, generation_root, ...)`.
    pub fn new(
        target: PhysicalTargetKey,
        generation_root: &std::path::Path,
        limits: Option<GatewayLimits>,
        generation_manager: Option<Arc<GenerationManager>>,
        graph_probe: Option<Probe>,
        vector_probe: Option<Probe>,
    ) -> StoreResult<Arc<Self>> {
        let limits = limits.unwrap_or_default();
        limits.validate()?;
        if let Some(manager) = &generation_manager
            && manager.target != target {
                return Err(StoreError::Value(
                    "generation manager target does not match gateway target".to_string(),
                ));
            }
        if graph_probe.is_some() != vector_probe.is_some() {
            return Err(StoreError::Value(
                "graph and vector readiness probes must be configured together".to_string(),
            ));
        }
        let generations = match generation_manager {
            Some(manager) => manager,
            None => GenerationManager::new(generation_root, target.clone(), 1, None, None)?,
        };
        let lanes = BTreeMap::from([
            (
                "graph".to_string(),
                BoundedLane::new("graph-read", limits.graph_read.clone())?,
            ),
            (
                "vector".to_string(),
                BoundedLane::new("vector-read", limits.vector_read.clone())?,
            ),
            (
                "write".to_string(),
                BoundedLane::new("storage-write", limits.write.clone())?,
            ),
            (
                "control".to_string(),
                BoundedLane::new("control-health", limits.control.clone())?,
            ),
        ]);
        let jobs_path = crate::util::resolve_path(generation_root).join("ingestion-jobs.json");
        Ok(Arc::new(Self {
            lifecycle: Mutex::new(OwnerLifecycleState::Starting),
            leases: Mutex::new(Vec::new()),
            jobs: Mutex::new(JobBook::default()),
            job_changed: std::sync::Condvar::new(),
            job_persist_lock: Mutex::new(()),
            jobs_path,
            active_generation: OnceLock::new(),
            graph_probe,
            vector_probe,
            probe: Mutex::new(ProbeState {
                generation: None,
                last_probe_at: None,
                error: None,
            }),
            lanes,
            started: AtomicBool::new(false),
            resources: Mutex::new(Vec::new()),
            target,
            limits,
            generations,
        }))
    }

    /// Create a lease-safe gateway with topology-fenced publication
    /// (`from_storage_factory`).
    #[allow(clippy::too_many_arguments)]
    pub fn from_storage_factory(
        factory: &crate::factory::StorageFactory,
        generation_root: &std::path::Path,
        graph_name: &str,
        collection_name: &str,
        role: &str,
        project_scope: Option<&str>,
        limits: Option<GatewayLimits>,
        graph_probe: Option<Probe>,
        vector_probe: Option<Probe>,
    ) -> StoreResult<Arc<Self>> {
        let manager = GenerationManager::from_storage_factory(
            generation_root,
            factory,
            graph_name,
            collection_name,
            role,
            project_scope,
            1,
        )?;
        Self::new(
            manager.target.clone(),
            generation_root,
            limits,
            Some(manager),
            graph_probe,
            vector_probe,
        )
    }

    fn lifecycle_lock(&self) -> MutexGuard<'_, OwnerLifecycleState> {
        lock_recover(&self.lifecycle)
    }

    pub fn lifecycle(&self) -> OwnerLifecycleState {
        *self.lifecycle_lock()
    }

    fn active_cell(&self) -> &Mutex<Option<GenerationManifest>> {
        self.active_generation
            .get_or_init(|| Mutex::new(None))
    }

    pub fn active_generation(&self) -> Option<GenerationManifest> {
        lock_recover(self.active_cell()).clone()
    }

    fn probe_lock(&self) -> MutexGuard<'_, ProbeState> {
        lock_recover(&self.probe)
    }

    fn lane(&self, name: &str) -> StoreResult<&BoundedLane> {
        self.lanes
            .get(name)
            .ok_or_else(|| StoreError::Value(format!("unsupported gateway lane: {name}")))
    }

    /// Acquire the process leases and reach READY (`start`).
    pub fn start(self: &Arc<Self>) -> StoreResult<()> {
        if self.lifecycle() != OwnerLifecycleState::Starting {
            return Ok(());
        }
        // The process lease is intentionally acquired before any backend
        // resource is created; partial startup unwinds deterministically.
        let (path_a, path_b) = self.target.canonical_paths();
        let graph_path = std::path::PathBuf::from(&self.target.graph_path);
        let mut acquired: Vec<StorageLease> = Vec::new();
        let mut lease_error: Option<StoreError> = None;
        for path in [path_a, path_b] {
            let backend = if path == graph_path { "falkordb" } else { "qdrant" };
            match StorageLease::new(&path, &self.target.instance_id, &self.target.owner_id, backend)
                .acquire()
            {
                Ok(lease) => acquired.push(lease),
                Err((_, conflict)) => {
                    lease_error = Some(StoreError::Runtime(conflict.message));
                    break;
                }
            }
        }
        if let Some(error) = lease_error {
            for lease in acquired.iter_mut().rev() {
                lease.release();
            }
            return Err(error);
        }
        *lock_recover(&self.leases) = acquired;

        // Lane execution is permit-bounded on the caller thread; the Python
        // per-lane executors exist to route non-preemptive store calls, which
        // blocking callers model directly.
        self.started.store(true, Ordering::SeqCst);
        let result: StoreResult<()> = (|| {
            *self.lifecycle_lock() = OwnerLifecycleState::Recovering;
            let active = run_in_lane("control", || self.generations.recover())?;
            *lock_recover(self.active_cell()) = active.clone();
            self.load_jobs()?;
            *self.lifecycle_lock() = OwnerLifecycleState::Warming;
            *self.lifecycle_lock() = OwnerLifecycleState::Ready;
            {
                let mut jobs = lock_recover(&self.jobs);
                jobs.accepting_jobs = true;
            }
            self.job_changed.notify_all();
            if active.is_some()
                && let (Some(graph_probe), Some(vector_probe)) =
                    (self.graph_probe.clone(), self.vector_probe.clone())
                {
                    self.refresh_readiness(Some(graph_probe), Some(vector_probe))?;
                }
            crate::runtime::register_gateway(self.clone());
            Ok(())
        })();
        if let Err(startup_error) = result {
            self.begin_drain();
            crate::runtime::unregister_gateway(self.as_ref());
            let resource_error = self.close_owned_resources();
            if let Err(resource_error) = resource_error {
                // A possibly-live backend handle must remain fenced: keep the
                // leases so close() can be retried.
                *self.lifecycle_lock() = OwnerLifecycleState::Draining;
                crate::runtime::register_gateway(self.clone());
                return Err(StoreError::Runtime(format!(
                    "{resource_error} (during failed startup: {startup_error})"
                )));
            }
            {
                let mut leases = lock_recover(&self.leases);
                leases.iter_mut().rev().for_each(|lease| lease.release());
                leases.clear();
            }
            *self.lifecycle_lock() = OwnerLifecycleState::Stopped;
            return Err(startup_error);
        }
        Ok(())
    }

    /// Synchronously stop admission for first-signal shutdown handling.
    pub fn begin_drain(&self) {
        {
            let mut lifecycle = self.lifecycle_lock();
            if *lifecycle == OwnerLifecycleState::Stopped {
                return;
            }
            *lifecycle = OwnerLifecycleState::Draining;
        }
        let mut jobs = lock_recover(&self.jobs);
        jobs.accepting_jobs = false;
        drop(jobs);
        self.job_changed.notify_all();
    }

    /// Drain the lanes, persist jobs, and release leases (`close`).
    pub fn close(&self, timeout_seconds: Option<f64>) -> StoreResult<()> {
        if self.lifecycle() == OwnerLifecycleState::Stopped {
            return Ok(());
        }
        self.begin_drain();
        let timeout = match timeout_seconds {
            Some(timeout) => {
                if timeout <= 0.0 {
                    return Err(StoreError::Value(
                        "gateway close timeout must be positive".to_string(),
                    ));
                }
                timeout
            }
            None => self.limits.drain_timeout_seconds,
        };
        let deadline = Instant::now() + Duration::from_secs_f64(timeout);
        for lane in self.lanes.values() {
            if !lane.wait_idle_until(deadline) {
                // Retain leases: a non-preemptive store call may still be
                // using them; lifecycle stays DRAINING for retry.
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::DeadlineExceeded,
                    "store gateway drain deadline elapsed",
                )
                .with_details(
                    json!({"lifecycle": self.lifecycle().as_str()})
                        .as_object()
                        .cloned()
                        .unwrap_or_default(),
                )
                .into());
            }
        }
        // Persistence failure must not turn graceful teardown into a
        // lease/resource leak; recovery reconciles from the last durable
        // snapshot on next start.
        let close_error: Option<StoreError> = self.persist_jobs().err();
        let resource_error = self.close_owned_resources().err();
        if let Some(resource_error) = resource_error {
            // Do not release process ownership while a backend handle may
            // still be open.
            *self.lifecycle_lock() = OwnerLifecycleState::Draining;
            return Err(StoreError::Runtime(match close_error {
                Some(close_error) => format!("{resource_error} (persist failure: {close_error})"),
                None => resource_error.to_string(),
            }));
        }
        {
            let mut leases = lock_recover(&self.leases);
            leases.iter_mut().rev().for_each(|lease| lease.release());
            leases.clear();
        }
        *self.lifecycle_lock() = OwnerLifecycleState::Stopped;
        crate::runtime::unregister_gateway(self);
        match close_error {
            Some(close_error) => Err(close_error),
            None => Ok(()),
        }
    }

    /// Close registered resources in reverse registration order, retaining
    /// failed handles for retry (`_close_owned_resources`). Closing runs
    /// outside the registry lock so a resource may re-enter the gateway.
    fn close_owned_resources(&self) -> StoreResult<()> {
        let mut taken: Vec<ResourceEntry> = std::mem::take(&mut *lock_recover(&self.resources));
        let mut first_error: Option<StoreError> = None;
        let mut retained: Vec<ResourceEntry> = Vec::new();
        while let Some((key, lane, mut resource)) = taken.pop() {
            match resource.close() {
                Ok(()) => {}
                Err(err) => {
                    if first_error.is_none() {
                        first_error = Some(err);
                    }
                    retained.push((key, lane, resource));
                }
            }
        }
        lock_recover(&self.resources).extend(retained);
        match first_error {
            Some(err) => Err(err),
            None => Ok(()),
        }
    }

    /// Return one gateway-owned adapter handle, closing a raced duplicate
    /// (`get_or_create_resource`).
    pub fn get_or_create_resource(
        &self,
        key: &str,
        factory: impl FnOnce() -> StoreResult<Box<dyn GatewayResource>>,
        lane: &str,
    ) -> StoreResult<()> {
        if key.trim().is_empty() {
            return Err(StoreError::Value("gateway resource key is required".to_string()));
        }
        if !self.started.load(Ordering::SeqCst) || !self.lanes.contains_key(lane) {
            return Err(StoreError::Value(format!(
                "gateway resource lane is not running: {lane}"
            )));
        }
        let lane_matches = CURRENT_LANE.with(|cell| {
            cell.borrow()
                .as_deref()
                .is_some_and(|current| current == lane)
        });
        if !lane_matches {
            return Err(StoreError::Runtime(
                "gateway resources must be opened from their named executor lane".to_string(),
            ));
        }
        {
            let resources = lock_recover(&self.resources);
            if let Some((_, existing_lane, _)) = resources.iter().find(|(k, _, _)| k == key) {
                if existing_lane != lane {
                    return Err(StoreError::Value(
                        "gateway resource key is already bound to another lane".to_string(),
                    ));
                }
                return Ok(());
            }
        }
        let resource = factory()?;
        let mut resources = lock_recover(&self.resources);
        match resources.iter().find(|(k, _, _)| k == key) {
            Some((_, existing_lane, _)) if existing_lane != lane => Err(StoreError::Value(
                "gateway resource key is already bound to another lane".to_string(),
            )),
            Some(_) => Ok(()),
            None => {
                resources.push((key.to_string(), lane.to_string(), resource));
                Ok(())
            }
        }
    }

    /// Execute one generation-pinned read through a read lane (`query`).
    pub fn query<T>(
        &self,
        operation: impl FnOnce(&GenerationManifest) -> StoreResult<T> + Send,
        lane: &str,
        estimated_bytes: i64,
        deadline_seconds: Option<f64>,
        request_id: Option<&str>,
    ) -> StoreResult<(T, FreshnessMetadata)> {
        let request_id = request_id
            .map(str::to_string)
            .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
        if self.lifecycle() != OwnerLifecycleState::Ready {
            return Err(StoreGatewayError::new(
                GatewayErrorCode::StoreMaintenance,
                "store owner is not ready",
            )
            .retryable()
            .with_details(
                json!({"correlation_id": request_id})
                    .as_object()
                    .cloned()
                    .unwrap_or_default(),
            )
            .into());
        }
        if !matches!(lane, "graph" | "vector" | "control") {
            return Err(StoreError::Value(format!("unsupported query lane: {lane}")));
        }
        let deadline =
            deadline_seconds.map(|seconds| Instant::now() + Duration::from_secs_f64(seconds));
        let lane_handle = self.lane(lane)?;
        let result = lane_handle.run(
            || {
                let manifest = match self.generations.pin_active() {
                    Ok(pin) => pin,
                    Err(StoreError::Runtime(message))
                        if message == "no active generation is available" =>
                    {
                        return Err(StoreGatewayError::new(
                            GatewayErrorCode::StoreMaintenance,
                            "store has no committed generation",
                        )
                        .retryable()
                        .with_details(
                            json!({"correlation_id": request_id})
                                .as_object()
                                .cloned()
                                .unwrap_or_default(),
                        )
                        .into());
                    }
                    Err(err) => return Err(err),
                };
                let result = run_in_lane(lane, || operation(&manifest))?;
                if deadline.is_some_and(|deadline| Instant::now() > deadline) {
                    return Err(StoreGatewayError::new(
                        GatewayErrorCode::DeadlineExceeded,
                        format!("{lane} query deadline elapsed during execution"),
                    )
                    .retryable()
                    .into());
                }
                let active_job = lock_recover(&self.jobs)
                    .jobs
                    .iter()
                    .find(|job| !job.state.terminal())
                    .map(|job| job.state);
                let metadata = FreshnessMetadata {
                    served_generation: manifest.generation_id.clone(),
                    source_revision: manifest.source_revision.clone(),
                    last_committed_at: manifest.published_at.clone(),
                    ingestion_state: active_job,
                };
                Ok((result, metadata))
            },
            estimated_bytes,
            deadline,
        );
        match result {
            Ok(value) => Ok(value),
            Err(StoreError::Gateway(mut err)) => {
                err.setdefault("correlation_id", Value::from(request_id));
                err.setdefault(
                    "active_generation",
                    self.active_generation()
                        .map(|manifest| Value::from(manifest.generation_id))
                        .unwrap_or(Value::Null),
                );
                Err(StoreError::Gateway(err))
            }
            Err(other) => Err(other),
        }
    }

    /// Execute one staging write through the target's sole writer lane
    /// (`write`).
    pub fn write<T>(
        &self,
        operation: impl FnOnce() -> StoreResult<T> + Send,
        estimated_bytes: i64,
        deadline_seconds: Option<f64>,
    ) -> StoreResult<T> {
        if self.lifecycle() != OwnerLifecycleState::Ready {
            return Err(StoreGatewayError::new(
                GatewayErrorCode::StoreMaintenance,
                "store owner is not ready",
            )
            .retryable()
            .into());
        }
        let deadline =
            deadline_seconds.map(|seconds| Instant::now() + Duration::from_secs_f64(seconds));
        self.lane("write")?.run(
            || run_in_lane("write", operation),
            estimated_bytes,
            deadline,
        )
    }

    /// Validate and atomically make one staged graph/vector pair active
    /// (`publish`).
    pub fn publish(
        &self,
        manifest: &GenerationManifest,
        validate: impl Fn(&GenerationManifest) -> StoreResult<()> + Send + Sync,
    ) -> StoreResult<GenerationManifest> {
        let graph_probe = self.graph_probe.clone();
        let vector_probe = self.vector_probe.clone();
        let validate_candidate = |candidate: &GenerationManifest| -> StoreResult<()> {
            validate(candidate)?;
            if let (Some(graph_probe), Some(vector_probe)) = (&graph_probe, &vector_probe) {
                StoreGateway::run_readiness_probes_sync(candidate, graph_probe, vector_probe)?;
            }
            Ok(())
        };
        let manager = self.generations.clone();
        match self.write(
            || manager.publish(manifest, validate_candidate),
            0,
            None,
        ) {
            Ok(published) => {
                *lock_recover(self.active_cell()) = Some(published.clone());
                if self.graph_probe.is_some() {
                    let mut probe = self.probe_lock();
                    probe.generation = Some(published.generation_id.clone());
                    probe.last_probe_at = Some(utc_now());
                    probe.error = None;
                }
                Ok(published)
            }
            Err(publication_error) => {
                // Re-read the authoritative pointer for every failure because
                // the pointer swap can succeed before a later fsync reports
                // an error.
                match run_in_lane("control", || self.generations.recover()) {
                    Err(_) => {
                        *lock_recover(self.active_cell()) = None;
                        let mut probe = self.probe_lock();
                        probe.generation = None;
                        probe.error = Some("PublicationReconciliationError".to_string());
                        Err(publication_error)
                    }
                    Ok(active) => {
                        *lock_recover(self.active_cell()) = active.clone();
                        if active
                            .as_ref()
                            .is_some_and(|active| active.generation_id == manifest.generation_id)
                            && self.graph_probe.is_some()
                        {
                            let mut probe = self.probe_lock();
                            probe.generation = None;
                            probe.last_probe_at = Some(utc_now());
                            probe.error = Some(publication_error.kind_name().to_string());
                        }
                        Err(publication_error)
                    }
                }
            }
        }
    }

    fn run_readiness_probes_sync(
        manifest: &GenerationManifest,
        graph_probe: &Probe,
        vector_probe: &Probe,
    ) -> StoreResult<()> {
        graph_probe(manifest)?;
        vector_probe(manifest)?;
        Ok(())
    }

    /// Refresh cached representative graph/vector probes on control capacity
    /// (`refresh_readiness`).
    pub fn refresh_readiness(
        &self,
        graph_probe: Option<Probe>,
        vector_probe: Option<Probe>,
    ) -> StoreResult<StoreHealth> {
        let Some(graph_probe) = graph_probe else {
            return Err(StoreError::Value(
                "a graph readiness probe is required".to_string(),
            ));
        };
        let Some(vector_probe) = vector_probe else {
            return Err(StoreError::Value(
                "a vector readiness probe is required".to_string(),
            ));
        };
        let outcome = self.query(
            |manifest| {
                StoreGateway::run_readiness_probes_sync(manifest, &graph_probe, &vector_probe)
            },
            "control",
            0,
            None,
            None,
        );
        let mut probe = self.probe_lock();
        match outcome {
            Ok(((), freshness)) => {
                probe.generation = Some(freshness.served_generation);
                probe.last_probe_at = Some(utc_now());
                probe.error = None;
            }
            Err(err) => {
                probe.generation = None;
                probe.last_probe_at = Some(utc_now());
                probe.error = Some(err.kind_name().to_string());
            }
        }
        Ok(self.health())
    }

    /// Retire only a non-active generation with no pinned readers (`retire`).
    pub fn retire(&self, manifest: &GenerationManifest) -> StoreResult<bool> {
        let manager = self.generations.clone();
        let manifest = manifest.clone();
        self.lane("control")?.run(
            || run_in_lane("control", || manager.retire(&manifest)),
            0,
            None,
        )
    }

    /// Submit one ingestion job with idempotency and queue budgets
    /// (`submit_ingest`).
    pub fn submit_ingest(
        &self,
        idempotency_key: &str,
        source_revision: &str,
        estimated_bytes: i64,
    ) -> StoreResult<IngestionJob> {
        if idempotency_key.trim().is_empty() || source_revision.trim().is_empty() {
            return Err(StoreError::Value(
                "idempotency_key and source_revision are required".to_string(),
            ));
        }
        if estimated_bytes < 0 {
            return Err(StoreError::Value(
                "estimated_bytes cannot be negative".to_string(),
            ));
        }
        let key = (self.target.value(), idempotency_key.to_string());
        let job = {
            let mut jobs = lock_recover(&self.jobs);
            if !jobs.accepting_jobs {
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::StoreMaintenance,
                    "store owner is not accepting ingestion",
                )
                .retryable()
                .into());
            }
            if let Some(existing_id) = jobs.keys.get(&key) {
                let existing = jobs
                    .jobs
                    .iter()
                    .find(|job| &job.job_id == existing_id)
                    .cloned();
                if let Some(existing) = existing {
                    if existing.source_revision != source_revision {
                        return Err(StoreGatewayError::new(
                            GatewayErrorCode::IngestionAlreadyRunning,
                            "idempotency key is already bound to another source revision",
                        )
                        .with_details(
                            json!({"job_id": existing.job_id})
                                .as_object()
                                .cloned()
                                .unwrap_or_default(),
                        )
                        .into());
                    }
                    return Ok(existing);
                }
            }
            let active_jobs: Vec<&IngestionJob> =
                jobs.jobs.iter().filter(|job| !job.state.terminal()).collect();
            let queued_bytes: i64 = active_jobs
                .iter()
                .filter_map(|job| job.detail.get("estimated_bytes").and_then(Value::as_i64))
                .sum();
            if active_jobs.len() as u64 >= self.limits.write.max_queue_items as u64
                || queued_bytes + estimated_bytes > self.limits.write.max_queue_bytes
            {
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::Overloaded,
                    "ingestion queue is full",
                )
                .retryable()
                .with_retry_after_ms(100)
                .with_details(
                    json!({
                        "queued_items": active_jobs.len(),
                        "queued_bytes": queued_bytes,
                        "capacity": self.limits.write.max_queue_items,
                        "byte_capacity": self.limits.write.max_queue_bytes,
                    })
                    .as_object()
                    .cloned()
                    .unwrap_or_default(),
                )
                .into());
            }
            let job = IngestionJob {
                job_id: uuid::Uuid::new_v4().simple().to_string(),
                target: self.target.clone(),
                idempotency_key: idempotency_key.to_string(),
                source_revision: source_revision.to_string(),
                state: IngestionJobState::Queued,
                submitted_at: utc_now(),
                updated_at: utc_now(),
                queue_position: Some(
                    jobs.jobs
                        .iter()
                        .filter(|value| value.state == IngestionJobState::Queued)
                        .count() as i64,
                ),
                generation_id: None,
                detail: {
                    let mut detail = Map::new();
                    detail.insert("estimated_bytes".to_string(), Value::from(estimated_bytes));
                    detail
                },
                cancel_requested_at: None,
            };
            jobs.keys.insert(key, job.job_id.clone());
            jobs.jobs.push(job.clone());
            drop(jobs);
            self.job_changed.notify_all();
            job
        };
        self.persist_jobs()?;
        Ok(job)
    }

    /// Transition one ingestion job (`update_job`).
    pub fn update_job(
        &self,
        job_id: &str,
        state: IngestionJobState,
        changes: JobChanges,
    ) -> StoreResult<IngestionJob> {
        let updated = {
            let mut jobs = lock_recover(&self.jobs);
            if !jobs.accepting_jobs {
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::StoreMaintenance,
                    "store owner is draining ingestion state",
                )
                .retryable()
                .into());
            }
            let index = jobs
                .jobs
                .iter()
                .position(|job| job.job_id == job_id)
                .ok_or_else(|| {
                    StoreError::Value(format!("ingestion job not found: {job_id}"))
                })?;
            let job = &jobs.jobs[index];
            if job.state.terminal() && state != job.state {
                return Err(StoreError::Value(
                    "terminal ingestion jobs cannot transition".to_string(),
                ));
            }
            if state != job.state && !job_transitions(job.state).contains(&state) {
                return Err(StoreError::Value(format!(
                    "invalid ingestion job transition: {} -> {}",
                    job.state.as_str(),
                    state.as_str()
                )));
            }
            let updated = job.with_state(state, changes);
            jobs.jobs[index] = updated.clone();
            drop(jobs);
            self.job_changed.notify_all();
            updated
        };
        self.persist_jobs()?;
        Ok(updated)
    }

    /// Fetch one ingestion job (`get_ingestion_status`).
    pub fn get_ingestion_status(&self, job_id: &str) -> Option<IngestionJob> {
        lock_recover(&self.jobs)
            .jobs
            .iter()
            .find(|job| job.job_id == job_id)
            .cloned()
    }

    /// Cancel queued work or record cancellation for a running call
    /// (`cancel_ingest`).
    pub fn cancel_ingest(&self, job_id: &str) -> StoreResult<Option<IngestionJob>> {
        let updated = {
            let mut jobs = lock_recover(&self.jobs);
            if !jobs.accepting_jobs {
                return Err(StoreGatewayError::new(
                    GatewayErrorCode::StoreMaintenance,
                    "store owner is draining ingestion state",
                )
                .retryable()
                .into());
            }
            let index = match jobs.jobs.iter().position(|job| job.job_id == job_id) {
                Some(index) => index,
                None => return Ok(None),
            };
            let job = jobs.jobs[index].clone();
            if job.state.terminal() {
                return Ok(Some(job));
            }
            let now = job
                .cancel_requested_at
                .clone()
                .unwrap_or_else(utc_now);
            let updated = if matches!(
                job.state,
                IngestionJobState::Queued | IngestionJobState::Preparing
            ) {
                job.with_state(
                    IngestionJobState::Cancelled,
                    JobChanges {
                        cancel_requested_at: Some(Some(now)),
                        queue_position: Some(None),
                        ..JobChanges::default()
                    },
                )
            } else {
                job.with_state(
                    job.state,
                    JobChanges {
                        cancel_requested_at: Some(Some(now)),
                        ..JobChanges::default()
                    },
                )
            };
            jobs.jobs[index] = updated.clone();
            drop(jobs);
            self.job_changed.notify_all();
            updated
        };
        self.persist_jobs()?;
        Ok(Some(updated))
    }

    /// Wait for a terminal job state within the caller's bounded timeout
    /// (`wait_for_ingestion`).
    pub fn wait_for_ingestion(
        &self,
        job_id: &str,
        timeout_seconds: Option<f64>,
    ) -> Option<IngestionJob> {
        match timeout_seconds {
            None => {
                let mut jobs = lock_recover(&self.jobs);
                loop {
                    let found = jobs
                        .jobs
                        .iter()
                        .find(|job| job.job_id == job_id)
                        .cloned();
                    match found {
                        Some(job) if job.state.terminal() => return Some(job),
                        None => return None,
                        _ => {
                            jobs = self
                                .job_changed
                                .wait(jobs)
                                .unwrap_or_else(|poisoned| poisoned.into_inner());
                        }
                    }
                }
            }
            Some(timeout) if timeout <= 0.0 => self.get_ingestion_status(job_id),
            Some(timeout) => {
                let deadline = Instant::now() + Duration::from_secs_f64(timeout);
                let mut jobs = lock_recover(&self.jobs);
                loop {
                    let found = jobs
                        .jobs
                        .iter()
                        .find(|job| job.job_id == job_id)
                        .cloned();
                    match found {
                        Some(job) if job.state.terminal() => return Some(job),
                        None => return None,
                        _ => {}
                    }
                    let remaining = deadline.saturating_duration_since(Instant::now());
                    if remaining.is_zero() {
                        return self.get_ingestion_status(job_id);
                    }
                    let (next, wait_result) = self
                        .job_changed
                        .wait_timeout(jobs, remaining)
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    jobs = next;
                    if wait_result.timed_out() {
                        return self.get_ingestion_status(job_id);
                    }
                }
            }
        }
    }

    /// Persist the job book through the control lane (`_persist_jobs`).
    fn persist_jobs(&self) -> StoreResult<()> {
        if !self.started.load(Ordering::SeqCst) {
            return Ok(());
        }
        let _guard = lock_recover(&self.job_persist_lock);
        let payload = {
            let jobs = lock_recover(&self.jobs);
            json!({
                "schema_version": 1,
                "target": self.target.value(),
                "jobs": jobs.jobs.iter().map(IngestionJob::to_value).collect::<Vec<_>>(),
            })
        };
        let encoded_bytes = crate::util::default_dumps_sorted(&payload).len() as i64;
        let jobs_path = self.jobs_path.clone();
        self.lane("control")?.run(
            || {
                run_in_lane("control", || write_jobs_file(&jobs_path, &payload))
            },
            encoded_bytes,
            None,
        )
    }

    /// Load and reconcile the durable job book (`_load_jobs`).
    fn load_jobs(&self) -> StoreResult<()> {
        let text = match crate::util::read_optional(&self.jobs_path).map_err(StoreError::Io)? {
            Some(text) => text,
            None => return Ok(()),
        };
        let payload: Value = serde_json::from_str(&text)
            .map_err(|err| StoreError::Value(format!("invalid ingestion job store: {err}")))?;
        if payload.get("schema_version").and_then(Value::as_i64) != Some(1)
            || payload.get("target").and_then(Value::as_str)
                != Some(self.target.value().as_str())
        {
            return Err(StoreError::Value(
                "ingestion job store does not match this physical target".to_string(),
            ));
        }
        let items = payload
            .get("jobs")
            .and_then(Value::as_array)
            .ok_or_else(|| StoreError::Value("invalid ingestion job store".to_string()))?;
        let active = self.active_generation();
        let mut recovered: Vec<IngestionJob> = Vec::new();
        let mut keys: BTreeMap<(String, String), String> = BTreeMap::new();
        for item in items {
            let mut job = IngestionJob::from_value(item)?;
            if job.target != self.target {
                return Err(StoreError::Value(
                    "ingestion job targets a different physical store".to_string(),
                ));
            }
            match job.state {
                IngestionJobState::Preparing => {
                    job = job.with_state(
                        IngestionJobState::Queued,
                        JobChanges {
                            detail: Some(with_recovery_detail(&job, "preparation_requeued_after_restart")),
                            ..JobChanges::default()
                        },
                    );
                }
                IngestionJobState::Publishing => {
                    let reconciled = active
                        .as_ref()
                        .is_some_and(|active| {
                            job.generation_id.as_deref() == Some(active.generation_id.as_str())
                                && job.source_revision == active.source_revision
                        });
                    job = if reconciled {
                        job.with_state(
                            IngestionJobState::Completed,
                            JobChanges {
                                detail: Some(with_recovery_detail(&job, "publication_reconciled")),
                                queue_position: Some(None),
                                ..JobChanges::default()
                            },
                        )
                    } else {
                        job.with_state(
                            IngestionJobState::Ambiguous,
                            JobChanges {
                                detail: Some(with_recovery_detail(
                                    &job,
                                    "publication_not_selected_by_active_manifest",
                                )),
                                ..JobChanges::default()
                            },
                        )
                    };
                }
                IngestionJobState::Writing | IngestionJobState::Validating => {
                    job = job.with_state(
                        IngestionJobState::Ambiguous,
                        JobChanges {
                            detail: Some(with_recovery_detail(
                                &job,
                                "owner_restarted_during_store_operation",
                            )),
                            ..JobChanges::default()
                        },
                    );
                }
                _ => {}
            }
            keys.insert(
                (self.target.value(), job.idempotency_key.clone()),
                job.job_id.clone(),
            );
            recovered.push(job);
        }
        {
            let mut jobs = lock_recover(&self.jobs);
            jobs.jobs = recovered;
            jobs.keys = keys;
        }
        self.job_changed.notify_all();
        self.persist_jobs()?;
        Ok(())
    }

    /// Bounded owner health projection (`health`).
    pub fn health(&self) -> StoreHealth {
        let active = self.active_generation();
        let probe = self.probe_lock();
        let probes_ready = self.graph_probe.is_some()
            && self.vector_probe.is_some()
            && active.is_some()
            && probe.generation.as_deref()
                == active.as_ref().map(|manifest| manifest.generation_id.as_str())
            && probe.error.is_none();
        let lane_snapshot = |name: &str| {
            self.lanes
                .get(name)
                .map(BoundedLane::snapshot)
                .unwrap_or_default()
        };
        let graph = lane_snapshot("graph");
        let vector = lane_snapshot("vector");
        let write = lane_snapshot("write");
        let ready = self.lifecycle() == OwnerLifecycleState::Ready
            && active.is_some()
            && probes_ready;
        StoreHealth {
            target: self.target.clone(),
            lifecycle: self.lifecycle(),
            active_generation: active.map(|manifest| manifest.generation_id),
            active_readers: graph.active + vector.active,
            queued_reads: graph.queued_items + vector.queued_items,
            queued_writes: write.queued_items,
            ready,
            updated_at: utc_now(),
            probe_generation: probe.generation.clone(),
            last_probe_at: probe.last_probe_at.clone(),
            probe_error: probe.error.clone(),
        }
    }

    /// Bounded in-memory projection without probing storage (`metrics`).
    pub fn metrics(&self) -> Value {
        let jobs = lock_recover(&self.jobs);
        let mut job_counts = Map::new();
        for state in IngestionJobState::ALL {
            let count = jobs
                .jobs
                .iter()
                .filter(|job| job.state == state)
                .count();
            job_counts.insert(state.as_str().to_string(), Value::from(count));
        }
        let mut lanes = Map::new();
        for (name, lane) in &self.lanes {
            lanes.insert(name.clone(), lane.snapshot().to_value());
        }
        let probe_state = {
            let probe = self.probe_lock();
            (
                probe.generation.clone(),
                probe.last_probe_at.clone(),
                probe.error.clone(),
            )
        };
        json!({
            "lifecycle": self.lifecycle().as_str(),
            "active_generation": self.active_generation().map(|manifest| Value::from(manifest.generation_id)).unwrap_or(Value::Null),
            "ready": self.health().ready,
            "probe_generation": probe_state.0.map(Value::from).unwrap_or(Value::Null),
            "last_probe_at": probe_state.1.map(Value::from).unwrap_or(Value::Null),
            "probe_error": probe_state.2.map(Value::from).unwrap_or(Value::Null),
            "lanes": Value::Object(lanes),
            "jobs": Value::Object(job_counts),
        })
    }
}

fn with_recovery_detail(job: &IngestionJob, note: &str) -> Map<String, Value> {
    let mut detail = job.detail.clone();
    detail.insert("recovery".to_string(), Value::from(note));
    detail
}

fn write_jobs_file(jobs_path: &std::path::Path, payload: &Value) -> StoreResult<()> {
    if let Some(parent) = jobs_path.parent() {
        std::fs::create_dir_all(parent).map_err(StoreError::Io)?;
    }
    write_atomic(jobs_path, &canonical_json(payload))?;
    fsync_directory(jobs_path.parent().unwrap_or_else(|| std::path::Path::new("/")))
        .map_err(StoreError::Io)?;
    Ok(())
}
