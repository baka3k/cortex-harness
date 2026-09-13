//! Behavioral-parity unit tests for the Rust storage port.
//!
//! Golden values (fingerprints, canonical endpoints, resolved paths) were
//! generated with the reference Python layer in the harness venv
//! (`.venv/bin/python`, repo root on `sys.path`) from
//! `cortex_harness.storage`.

use std::path::{Path, PathBuf};

use cortex_storage::config::{
    resolve_storage, validate_storage_identity, ResolveOverrides, StorageRole,
};
use cortex_storage::contracts::{
    GatewayErrorCode, GenerationState, IngestionJobState, StoreGatewayError,
};
use cortex_storage::StoreError;
use cortex_storage::gateway::{GatewayLimits, StoreGateway};
use cortex_storage::qdrant::LocalQdrantStore;
use cortex_storage::contracts::PhysicalTargetKey;
use cortex_storage::targets::{
    canonical_remote_endpoint, EffectiveStorageTarget, EffectiveStorageTopology,
};
use cortex_storage::{BoundedLane, LaneLimits, StorageLease, StorageLeaseConflictError};

fn temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "cortex-storage-parity-{}-{}",
        label,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).expect("temp dir");
    dir
}

// ---------------------------------------------------------------------------
// targets.rs — canonical endpoints + fingerprints (Python golden values).
// ---------------------------------------------------------------------------

#[test]
fn canonical_remote_endpoint_matches_python() {
    assert_eq!(
        canonical_remote_endpoint("redis://User:PaSs@LocalHost:80/", "redis").unwrap(), // sensitive-guard:allow (ten flag / test sample)
        ("redis://localhost:80".to_string(), Some("User".to_string()))
    );
    assert_eq!(
        canonical_remote_endpoint("localhost:6379", "redis").unwrap(),
        ("redis://localhost:6379".to_string(), None)
    );
    let (unix_endpoint, principal) =
        canonical_remote_endpoint("unix:///tmp/x.sock", "redis").unwrap();
    assert!(unix_endpoint.starts_with("unix:"), "{unix_endpoint}");
    assert!(unix_endpoint.ends_with("/x.sock"));
    assert_eq!(principal, None);
    assert!(canonical_remote_endpoint("", "redis").is_err());
    assert!(canonical_remote_endpoint("http://host:abc/", "http").is_err());
    assert!(canonical_remote_endpoint("http://host:99999/", "http").is_err());
}

#[test]
fn effective_target_fingerprints_match_python() {
    // Golden (Python): local_graph_target("/tmp/cortex-parity-demo",
    // graph="hyper_graph", role="code")
    let graph = EffectiveStorageTarget::create(
        "graph",
        "falkordb",
        "file",
        "/tmp/cortex-parity-demo",
        "hyper_graph",
        "code",
        false,
        None,
        None,
        None,
        1,
    )
    .unwrap();
    assert_eq!(
        graph.canonical_json(),
        "{\"component\":\"graph\",\"location\":\"/private/tmp/cortex-parity-demo\",\"mode\":\"file\",\"namespace\":\"hyper_graph\",\"provider\":\"falkordb\",\"role\":\"code\",\"schema_version\":1,\"tls\":false}"
    );
    assert_eq!(
        graph.fingerprint(),
        "storage-target:v1:c1f412fb79410eb4649e431aed2e79d74e37fc26796f3e6da3c2b639f1d1e1df"
    );

    // Golden (Python): remote_vector_target(
    //   "http://User@Example.COM:80/qdrant/", collection="code_col", role="doc")
    let vector = EffectiveStorageTarget::create(
        "vector",
        "qdrant",
        "remote",
        "http://User@Example.COM:80/qdrant/",
        "code_col",
        "doc",
        false,
        None,
        None,
        None,
        1,
    )
    .unwrap();
    assert_eq!(
        vector.canonical_json(),
        "{\"component\":\"vector\",\"location\":\"http://example.com:80/qdrant\",\"mode\":\"remote\",\"namespace\":\"code_col\",\"principal_fingerprint\":\"sha256:04f8996da763b7a969b1028ee3007569eaf3a635486ddab211d512c85b9df8fb\",\"provider\":\"qdrant\",\"role\":\"doc\",\"schema_version\":1,\"tls\":false}"
    );
    assert_eq!(
        vector.fingerprint(),
        "storage-target:v1:9b347e087e65b80ec61ffa0d2b0f1cab53aa4cd0218a1752a5227514f5f6aa79"
    );
}

#[test]
fn topology_fingerprint_matches_python() {
    let graph = EffectiveStorageTarget::create(
        "graph",
        "falkordb",
        "file",
        "/tmp/cortex-parity-demo",
        "hyper_graph",
        "code",
        false,
        None,
        None,
        None,
        1,
    )
    .unwrap();
    let vector = EffectiveStorageTarget::create(
        "vector",
        "qdrant",
        "file",
        "/tmp/cortex-parity-demo-vec",
        "code_col",
        "code",
        false,
        None,
        None,
        None,
        1,
    )
    .unwrap();
    let topology =
        EffectiveStorageTopology::create("proj-a", "local", false, "gen-1", graph, vector).unwrap();
    assert_eq!(
        topology.fingerprint(),
        "storage-topology:v1:6aad687308e335b27eb5df438522cc3c635d1b5b12344fc279ab6dbd185c6f8b"
    );
    // for_generation changes the generation fence only.
    let rebound = topology.for_generation("gen-2").unwrap();
    assert_ne!(rebound.fingerprint(), topology.fingerprint());
    assert_eq!(rebound.graph.fingerprint(), topology.graph.fingerprint());
    assert_eq!(
        rebound.compatibility_metadata()["generation_id"],
        serde_json::json!("gen-2")
    );
}

#[test]
fn physical_target_key_normalizes_like_python() {
    let key = PhysicalTargetKey::from_paths(
        "Stress",
        "Code",
        Path::new("/tmp/cortex-parity-demo"),
        Path::new("/tmp/cortex-parity-demo-vec"),
    );
    assert_eq!(key.instance_id, "stress");
    assert_eq!(key.owner_id, "code");
    assert!(key.graph_path.starts_with("/private/tmp/"), "{}", key.graph_path);
    assert_eq!(key.value(), format!(
        "stress|code|{}|{}",
        key.graph_path,
        key.vector_path
    ));
    let (first, second) = key.canonical_paths();
    assert!(first <= second);
}

// ---------------------------------------------------------------------------
// config.rs — resolution semantics.
// ---------------------------------------------------------------------------

#[test]
fn resolve_storage_matches_python_layout() {
    // Golden (Python): resolve_storage("/tmp/proj", data_home="/tmp/cortex-parity-home")
    let resolved = resolve_storage(
        Path::new("/tmp/proj"),
        None,
        &ResolveOverrides {
            data_home: Some("/tmp/cortex-parity-home".to_string()),
            ..ResolveOverrides::default()
        },
    )
    .unwrap();
    assert_eq!(
        resolved.instance_root.to_string_lossy(),
        "/private/tmp/cortex-parity-home/v1/instances/default"
    );
    assert_eq!(
        resolved.qdrant_code_path.to_string_lossy(),
        "/private/tmp/cortex-parity-home/v1/instances/default/qdrant/code"
    );
    assert_eq!(
        resolved.falkordb_doc_path.to_string_lossy(),
        "/private/tmp/cortex-parity-home/v1/instances/default/falkordb/doc/data.rdb"
    );
    assert_eq!(
        resolved.ladybug_code_path.to_string_lossy(),
        "/private/tmp/cortex-parity-home/v1/instances/default/ladybug/code/code.lbug/hyper_graph"
    );
    assert_eq!(resolved.path_provenance, "explicit-absolute-override");
}

#[test]
fn resolve_storage_anchors_relative_data_home_to_account_home() {
    let resolved = resolve_storage(
        Path::new("/tmp/proj"),
        None,
        &ResolveOverrides {
            data_home: Some("sampledb".to_string()),
            ..ResolveOverrides::default()
        },
    )
    .unwrap();
    assert_eq!(resolved.path_provenance, "explicit-relative-anchored-to-home");
    assert!(resolved
        .instance_root
        .to_string_lossy()
        .contains("/.cortext-harness/sampledb/v1/instances/default"));
}

#[test]
fn storage_identity_validation() {
    assert_eq!(
        validate_storage_identity(Some("My_Instance-2"), "instance_id").unwrap(),
        "my_instance-2"
    );
    assert!(validate_storage_identity(Some("-leading"), "x").is_err());
    assert!(validate_storage_identity(Some("has space"), "x").is_err());
    assert!(validate_storage_identity(Some("abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz_0123456789012345"), "x").is_err());
    assert!(validate_storage_identity(Some(""), "x").is_err());
    let long = "a".repeat(65);
    assert!(validate_storage_identity(Some(long.as_str()), "x").is_err());
    // The Python error message carries a literal `{value!r}` tail (the
    // reference message is not an f-string); parity keeps the literal.
    let err = validate_storage_identity(Some("BAD!"), "instance_id").unwrap_err();
    assert!(err.to_string().ends_with("got {value!r}"), "{}", err);
}

// ---------------------------------------------------------------------------
// lease.rs — flock conflict and release semantics.
// ---------------------------------------------------------------------------

#[test]
fn lease_conflict_and_release() {
    let root = temp_dir("lease");
    let target = root.join("store.rdb");
    let mut first = StorageLease::new(&target, "inst", "code", "falkordb")
        .acquire()
        .expect("first acquire");
    let second = StorageLease::new(&target, "inst", "code", "falkordb");
    let err = second.acquire().expect_err("conflict").1;
    let StorageLeaseConflictError { message, .. } = &err;
    assert!(
        message.starts_with("Embedded falkordb store is already owned: "),
        "{message}"
    );
    assert!(message.contains("Current lease: {\"acquired_at\""));
    assert!(message.contains("CORTEX_STORAGE_INSTANCE/CORTEX_STORAGE_OWNER"));
    first.release();
    // After release, re-acquisition succeeds.
    StorageLease::new(&target, "inst", "code", "falkordb")
        .acquire()
        .expect("re-acquire")
        .release();
    let _ = std::fs::remove_dir_all(&root);
}

// ---------------------------------------------------------------------------
// gateway.rs — lifecycle, publication, pins, freshness, ingestion jobs.
// ---------------------------------------------------------------------------

fn gateway_for(root: &Path) -> std::sync::Arc<StoreGateway> {
    let target = PhysicalTargetKey::from_paths(
        "parity",
        "code",
        &root.join("graph.rdb"),
        &root.join("vectors"),
    );
    let manager = cortex_storage::GenerationManager::new(
        &root.join("generations"),
        target.clone(),
        1,
        None,
        None,
    )
    .unwrap();
    StoreGateway::new(
        target,
        &root.join("generations"),
        Some(GatewayLimits::default()),
        Some(manager),
        None,
        None,
    )
    .unwrap()
}

#[test]
fn gateway_lifecycle_and_freshness() {
    let root = temp_dir("gateway");
    let gateway = gateway_for(&root);

    // Not ready: STORE_MAINTENANCE.
    let err = gateway
        .query(|_| Ok(()), "graph", 0, None, None)
        .err()
        .unwrap();
    assert_eq!(err.gateway_code_str(), Some("STORE_MAINTENANCE"));

    gateway.start().expect("start");
    assert_eq!(gateway.lifecycle().as_str(), "READY");

    // No committed generation yet: STORE_MAINTENANCE with parity message.
    let err = gateway
        .query(|manifest| Ok(manifest.generation_id.clone()), "graph", 0, None, None)
        .err()
        .unwrap();
    assert_eq!(err.gateway_code_str(), Some("STORE_MAINTENANCE"));
    let StoreError::Gateway(details) = &err else { panic!() };
    assert_eq!(details.message, "store has no committed generation");

    // Publish generation-1 through the write lane.
    let manifest = gateway.generations.allocate("rev-1", Some("generation-1")).unwrap();
    let published = gateway
        .publish(&manifest, |_| Ok(()))
        .expect("publish");
    assert_eq!(published.state, GenerationState::Published);
    assert!(!gateway.health().ready, "no probes configured");

    // Query pins the active generation and reports freshness.
    let (served, freshness) = gateway
        .query(
            |manifest| Ok(format!("served:{}", manifest.generation_id)),
            "graph",
            0,
            None,
            None,
        )
        .expect("query");
    assert_eq!(served, "served:generation-1");
    assert_eq!(freshness.served_generation, "generation-1");
    assert_eq!(freshness.source_revision, "rev-1");
    assert_eq!(freshness.ingestion_state, None);

    // Swap generations: new query sees the new store.
    let manifest2 = gateway.generations.allocate("rev-2", Some("generation-2")).unwrap();
    gateway.publish(&manifest2, |_| Ok(())).expect("publish 2");
    let (served2, freshness2) = gateway
        .query(
            |manifest| Ok(format!("served:{}", manifest.generation_id)),
            "graph",
            0,
            None,
            None,
        )
        .expect("query 2");
    assert_eq!(served2, "served:generation-2");
    assert_eq!(freshness2.served_generation, "generation-2");

    // Submit ingestion and observe it in freshness metadata.
    let job = gateway
        .submit_ingest("ingest-key-1", "rev-3", 512)
        .expect("submit");
    assert_eq!(job.state, IngestionJobState::Queued);
    let (_, freshness3) = gateway
        .query(|_| Ok(()), "graph", 0, None, None)
        .expect("query 3");
    assert_eq!(freshness3.ingestion_state, Some(IngestionJobState::Queued));

    // Idempotent resubmit returns the same job.
    let again = gateway
        .submit_ingest("ingest-key-1", "rev-3", 512)
        .expect("resubmit");
    assert_eq!(again.job_id, job.job_id);

    // Conflicting revision for the same key -> INGESTION_ALREADY_RUNNING.
    let err = gateway
        .submit_ingest("ingest-key-1", "rev-OTHER", 512)
        .err()
        .unwrap();
    assert_eq!(err.gateway_code_str(), Some("INGESTION_ALREADY_RUNNING"));

    // Invalid transition is a ValueError in Python.
    let err = gateway
        .update_job(&job.job_id, IngestionJobState::Completed, Default::default())
        .err()
        .unwrap();
    assert!(
        err.to_string().starts_with("invalid ingestion job transition: QUEUED -> COMPLETED"),
        "{err}"
    );

    // Cancel the queued job.
    let cancelled = gateway
        .cancel_ingest(&job.job_id)
        .expect("cancel")
        .expect("job");
    assert_eq!(cancelled.state, IngestionJobState::Cancelled);
    assert!(cancelled.cancel_requested_at.is_some());

    // Durable job store exists and matches the target.
    let jobs_path = root.join("generations").join("ingestion-jobs.json");
    assert!(jobs_path.is_file());
    let payload: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&jobs_path).unwrap()).unwrap();
    assert_eq!(payload["schema_version"], 1);
    assert_eq!(payload["target"], gateway.target.value().as_str());

    gateway.close(None).expect("close");
    assert_eq!(gateway.lifecycle().as_str(), "STOPPED");
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn gateway_job_recovery_after_restart() {
    let root = temp_dir("gateway-recovery");
    let gateway = gateway_for(&root);
    gateway.start().expect("start");
    let manifest = gateway.generations.allocate("rev-1", Some("generation-1")).unwrap();
    gateway.publish(&manifest, |_| Ok(())).expect("publish");
    let job = gateway
        .submit_ingest("recover-key", "rev-1", 0)
        .expect("submit");
    gateway
        .update_job(&job.job_id, IngestionJobState::Writing, Default::default())
        .expect("to writing");
    gateway.close(None).expect("close");

    // Restart: WRITING jobs become AMBIGUOUS (owner died mid-store-op).
    let gateway2 = gateway_for(&root);
    gateway2.start().expect("restart");
    let recovered = gateway2.get_ingestion_status(&job.job_id).expect("recovered");
    assert_eq!(recovered.state, IngestionJobState::Ambiguous);
    assert_eq!(
        recovered.detail.get("recovery").and_then(serde_json::Value::as_str),
        Some("owner_restarted_during_store_operation")
    );
    gateway2.close(None).expect("close 2");
    let _ = std::fs::remove_dir_all(&root);
}

// ---------------------------------------------------------------------------
// admission.rs — limits validation parity.
// ---------------------------------------------------------------------------

#[test]
fn lane_limits_validation() {
    assert!(LaneLimits::new(0, 32, 1024).validate().is_err());
    assert!(LaneLimits::new(1, 32, -1).validate().is_err());
    assert!(LaneLimits::new(1, 32, 1024).validate().is_ok());
    assert!(BoundedLane::new("x", LaneLimits::new(0, 1, 1)).is_err());
}

#[test]
fn gateway_error_renders_like_python_dict() {
    let error = StoreGatewayError::new(GatewayErrorCode::Overloaded, "queue is full")
        .retryable()
        .with_retry_after_ms(100);
    let rendered = error.to_value();
    assert_eq!(rendered["code"], "OVERLOADED");
    assert_eq!(rendered["retryable"], true);
    assert_eq!(rendered["retry_after_ms"], 100);
    assert_eq!(rendered["message"], "queue is full");
}

// ---------------------------------------------------------------------------
// qdrant.rs — local embedded boundary (exact brute-force engine).
// ---------------------------------------------------------------------------

#[test]
fn local_qdrant_store_boundary() {
    let root = temp_dir("qdrant-local");
    let resolved = resolve_storage(
        &root,
        None,
        &ResolveOverrides {
            data_home: Some(root.to_string_lossy().into_owned()),
            ..ResolveOverrides::default()
        },
    )
    .unwrap();
    resolved.ensure_directories().unwrap();
    let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).unwrap();
    assert!(!store.collection_exists("code"));
    store
        .create_collection("code", &serde_json::json!({"size": 3, "distance": "Cosine"}))
        .unwrap();
    assert!(store.collection_exists("code"));
    assert_eq!(store.list_collection_names().unwrap(), vec!["code"]);

    store
        .upsert(
            "code",
            &[
                serde_json::json!({"id": 1, "vector": [1.0, 0.0, 0.0], "payload": {"file": "a.py", "score": 2}}),
                serde_json::json!({"id": 2, "vector": [0.0, 1.0, 0.0], "payload": {"file": "b.py"}}),
            ],
        )
        .unwrap();

    let hits = store
        .search("code", &[1.0, 0.0, 0.0], 2, None, true, false, None)
        .unwrap();
    assert_eq!(hits.len(), 2);
    assert_eq!(hits[0]["id"], 1);
    assert_eq!(hits[0]["payload"]["file"], "a.py");

    let filtered = serde_json::json!({"must": [{"key": "file", "match": {"value": "b.py"}}]});
    let hits = store
        .search("code", &[0.0, 1.0, 0.0], 5, Some(&filtered), true, false, None)
        .unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0]["id"], 2);

    assert_eq!(store.count("code", None).unwrap(), 2);

    let (page, next) = store.scroll("code", None, 1, true, false, None).unwrap();
    assert_eq!(page.len(), 1);
    assert_eq!(next, Some(serde_json::json!(2)));

    let retrieved = store
        .retrieve("code", &[serde_json::json!(2), serde_json::json!(99)], true, false)
        .unwrap();
    assert_eq!(retrieved.len(), 1);

    store
        .set_payload("code", &serde_json::json!({"tag": "x"}), Some(&[serde_json::json!(1)]), None)
        .unwrap();
    let one = store.retrieve("code", &[serde_json::json!(1)], true, false).unwrap();
    assert_eq!(one[0]["payload"]["tag"], "x");
    assert_eq!(one[0]["payload"]["file"], "a.py", "set_payload merges");
    store
        .overwrite_payload("code", &serde_json::json!({"tag": "y"}), Some(&[serde_json::json!(1)]), None)
        .unwrap();
    let one = store.retrieve("code", &[serde_json::json!(1)], true, false).unwrap();
    assert_eq!(one[0]["payload"].as_object().unwrap().len(), 1);

    store
        .delete("code", Some(&[serde_json::json!(1)]), None)
        .unwrap();
    assert_eq!(store.count("code", None).unwrap(), 1);

    store.delete_collection("code").unwrap();
    assert!(!store.collection_exists("code"));

    store.close();
    let _ = std::fs::remove_dir_all(&root);
}

// ---------------------------------------------------------------------------
// GatewayLimits::from_profile lane budgets.
// ---------------------------------------------------------------------------

#[test]
fn gateway_limits_from_profile() {
    let profile = cortex_storage::PerformanceProfile {
        name: "balanced".to_string(),
        graph_readers: 2,
        vector_readers: 2,
        writer_slots: 1,
        control_slots: 2,
        max_queue_items: 64,
        max_queue_bytes: 1024 * 1024,
        request_timeout_seconds: 10.0,
        disk_safety_fraction: 0.25,
    };
    let limits = GatewayLimits::from_profile(&profile);
    assert_eq!(limits.graph_read.concurrency, 2);
    assert_eq!(limits.write.max_queue_items, 16);
    assert_eq!(limits.control.max_queue_items, 8);
    assert_eq!(limits.drain_timeout_seconds, 20.0);
}
