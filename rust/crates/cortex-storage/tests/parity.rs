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
// qdrant.rs — phase-01 engine hardening (native vector-ingest local lane).
// ---------------------------------------------------------------------------

fn phase01_store(label: &str) -> (PathBuf, PathBuf, LocalQdrantStore) {
    let root = temp_dir(label);
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
    let store_root = resolved.path_for_role(StorageRole::Code.as_str()).unwrap().to_path_buf();
    let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).unwrap();
    (root, store_root, store)
}

/// `stale_filter` golden shape (`{"must": [...], "must_not": [{"has_id": …}]}`)
/// — the delete MUST keep the has_id points and remove only the scoped stale
/// ones. Before the phase-01 fix the local engine ignored `must_not` entirely
/// and deleted the kept points too.
#[test]
fn stale_filter_delete_honours_must_not_has_id() {
    let (_root, _store_root, store) = phase01_store("stale-filter");
    store
        .create_collection("code", &serde_json::json!({"size": 2, "distance": "Cosine"}))
        .unwrap();
    let point = |id: &str, parser: &str, file: &str| {
        serde_json::json!({
            "id": id,
            "vector": [1.0, 0.0],
            "payload": {
                "parser": parser,
                "project_id_normalized": "proj",
                "root_scope": "/r",
                "file_path": file,
            },
        })
    };
    store
        .upsert(
            "code",
            &[
                point("keep-1", "go", "/r/a.go"),
                point("keep-2", "go", "/r/b.go"),
                point("stale-1", "go", "/r/deleted.go"),
                point("other-parser", "py", "/r/a.go"),
            ],
        )
        .unwrap();

    // Byte-shape of vector_sync::stale_filter (must + must_not has_id).
    let filter = serde_json::json!({
        "must": [
            {"key": "project_id_normalized", "match": {"value": "proj"}},
            {"key": "parser", "match": {"value": "go"}},
            {"key": "root_scope", "match": {"value": "/r"}},
            {"key": "file_path", "match": {"any": ["/r/deleted.go"]}},
        ],
        "must_not": [{"has_id": ["keep-1", "keep-2"]}],
    });
    store.delete("code", None, Some(&filter)).unwrap();

    let mut remaining: Vec<String> = store
        .scroll("code", None, 100, false, false, None)
        .unwrap()
        .0
        .iter()
        .map(|hit| hit["id"].as_str().unwrap().to_string())
        .collect();
    remaining.sort();
    assert_eq!(remaining, vec!["keep-1", "keep-2", "other-parser"]);
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Full-filter grammar: has_id membership, nested must_not filters, should
/// with min_should.
#[test]
fn filter_grammar_must_not_has_id_should() {
    let (_root, _store_root, store) = phase01_store("filter-grammar");
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    store
        .upsert(
            "code",
            &[
                serde_json::json!({"id": "a", "vector": [1.0], "payload": {"tag": "x"}}),
                serde_json::json!({"id": "b", "vector": [1.0], "payload": {"tag": "y"}}),
                serde_json::json!({"id": "c", "vector": [1.0], "payload": {"tag": "z"}}),
            ],
        )
        .unwrap();

    // has_id inside must: only listed ids pass.
    let hits = store
        .search(
            "code",
            &[1.0],
            10,
            Some(&serde_json::json!({"must": [{"has_id": ["a", "c"]}]})),
            true,
            false,
            None,
        )
        .unwrap();
    let mut ids: Vec<&str> = hits.iter().map(|hit| hit["id"].as_str().unwrap()).collect();
    ids.sort();
    assert_eq!(ids, vec!["a", "c"]);

    // must_not nested filter: exclude tag=x via a sub-filter.
    let hits = store
        .search(
            "code",
            &[1.0],
            10,
            Some(&serde_json::json!({
                "must_not": [{"must": [{"key": "tag", "match": {"value": "x"}}]}],
            })),
            true,
            false,
            None,
        )
        .unwrap();
    assert_eq!(hits.len(), 2, "b and c survive the must_not sub-filter");

    // should (min_should default 1): at least one condition must match.
    let hits = store
        .search(
            "code",
            &[1.0],
            10,
            Some(&serde_json::json!({
                "should": [
                    {"key": "tag", "match": {"value": "x"}},
                    {"key": "tag", "match": {"value": "y"}},
                ],
            })),
            true,
            false,
            None,
        )
        .unwrap();
    assert_eq!(hits.len(), 2, "x and y match should");
    let hits = store
        .search(
            "code",
            &[1.0],
            10,
            Some(&serde_json::json!({
                "min_should": 2,
                "should": [
                    {"key": "tag", "match": {"value": "x"}},
                    {"key": "tag", "match": {"value": "y"}},
                ],
            })),
            true,
            false,
            None,
        )
        .unwrap();
    assert!(hits.is_empty(), "no point matches both should conditions");
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// `get_collection_info` must carry the vector config in the REST shape so
/// the cortex-sync drift guard (`vector_sizes`) keeps working on local stores.
#[test]
fn collection_info_carries_vector_config_rest_shape() {
    let (_root, _store_root, store) = phase01_store("info-shape");
    store
        .create_collection("anon", &serde_json::json!({"vectors": {"size": 1024, "distance": "Cosine"}}))
        .unwrap();
    store
        .create_collection(
            "named",
            &serde_json::json!({"vectors": {"code": {"size": 512, "distance": "Cosine"}}}),
        )
        .unwrap();
    let anon = store.get_collection_info("anon").unwrap();
    assert_eq!(
        anon.pointer("/result/config/params/vectors"),
        Some(&serde_json::json!({"size": 1024, "distance": "Cosine"})),
        "anonymous wrapper must surface as the bare VectorParams"
    );
    let named = store.get_collection_info("named").unwrap();
    assert_eq!(
        named.pointer("/result/config/params/vectors/code/size"),
        Some(&serde_json::json!(512))
    );
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Legacy pickle root (`collection/` or root `storage.sqlite`, no JSON store)
/// fails closed on BOTH the writer client and the read-only reader; during
/// the re-index window (JSON present) the legacy markers may coexist.
#[test]
fn legacy_pickle_root_refused_loudly() {
    let root = temp_dir("legacy-guard");

    // Legacy-only root: writer refuses.
    std::fs::create_dir_all(root.join("collection").join("some_collection")).unwrap();
    std::fs::write(root.join("collection").join("some_collection").join("storage.sqlite"), b"x").unwrap();
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
    let store_root = resolved.path_for_role(StorageRole::Code.as_str()).unwrap().to_path_buf();
    std::fs::create_dir_all(&store_root).unwrap();
    std::fs::create_dir_all(store_root.join("collection")).unwrap();

    let writer_error =
        LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).err().expect("writer refuses legacy root");
    assert!(
        writer_error.to_string().contains("legacy qdrant-client pickle store"),
        "honest legacy message, got: {writer_error}"
    );
    let reader_error = cortex_storage::qdrant::LocalQdrantReader::open(&store_root)
        .err()
        .expect("reader refuses legacy root too");
    assert!(
        reader_error.to_string().contains("legacy qdrant-client pickle store"),
        "reader legacy message, got: {reader_error}"
    );

    // Old single-file-at-root layout variant also trips the compound key.
    let old_root = store_root.join("collection");
    std::fs::remove_dir_all(&old_root).unwrap();
    std::fs::write(store_root.join("storage.sqlite"), b"x").unwrap();
    assert!(LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).is_err());

    // Re-index window: JSON store present + legacy subtree → allowed. The
    // JSON store comes from an earlier successful flush, so open WITHOUT the
    // marker first, flush once, then let the legacy subtree coexist.
    // (`collection/` is already gone; only the root sqlite marker remains.)
    std::fs::remove_file(store_root.join("storage.sqlite")).unwrap();
    let store = LocalQdrantStore::open(&resolved, StorageRole::Code.as_str()).unwrap();
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    assert!(store_root.join("cortex-local-store.json").exists(), "flush produced the JSON store");
    std::fs::create_dir_all(store_root.join("collection")).unwrap();
    assert!(cortex_storage::qdrant::LocalQdrantReader::open(&store_root).is_ok());
    store.close();
    let _ = std::fs::remove_dir_all(&root);
}

/// Reader: missing dir → loud error; mutations via the writer become visible
/// to the reader without reopening (mtime+size revalidate); a wiped store
/// file reads as empty, never as stale cached data.
#[test]
fn readonly_reader_revalidates_snapshots() {
    let (_root, store_root, store) = phase01_store("reader-revalidate");

    let missing = store_root.join("does-not-exist");
    let err = cortex_storage::qdrant::LocalQdrantReader::open(&missing)
        .err()
        .expect("missing store dir is a loud error");
    assert!(err.to_string().contains("not found"), "got: {err}");

    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    let reader = cortex_storage::qdrant::LocalQdrantReader::open(&store_root).unwrap();
    assert_eq!(reader.list_collection_names().unwrap(), vec!["code"]);
    assert!(!reader.collection_exists("other").unwrap());

    store
        .upsert(
            "code",
            &[serde_json::json!({"id": "p1", "vector": [2.0], "payload": {"k": "v"}})],
        )
        .unwrap();
    let hits = reader
        .search("code", &[2.0], 5, None, true, false, None)
        .unwrap();
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0]["payload"]["k"], "v");
    assert_eq!(reader.count("code", None).unwrap(), 1);
    let info = reader.get_collection_info("code").unwrap();
    assert_eq!(info.pointer("/result/config/params/vectors/size"), Some(&serde_json::json!(1)));

    // Hit shape audit: {id, score, payload, vector} — no `version` field
    // (sidecar/QdrantLocal hits carry one; documented divergence).
    assert!(hits[0].get("version").is_none());
    assert!(hits[0].get("score").and_then(serde_json::Value::as_f64).is_some());

    store.delete_collection("code").unwrap();
    assert!(!reader.collection_exists("code").unwrap(), "revalidated snapshot sees the deletion");
    assert_eq!(reader.list_collection_names().unwrap(), Vec::<String>::new());
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Concurrent writer (lease-holding, flushed upserts) + lock-free reader —
/// no torn reads (every parse succeeds), no deadlock, counts converge.
#[test]
fn concurrent_writer_and_lockfree_reader() {
    let (_root, store_root, store) = phase01_store("concurrent");
    store
        .create_collection("code", &serde_json::json!({"size": 8, "distance": "Cosine"}))
        .unwrap();
    let reader = std::sync::Arc::new(
        cortex_storage::qdrant::LocalQdrantReader::open(&store_root).unwrap(),
    );
    let reader_for_thread = reader.clone();
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop_for_thread = stop.clone();
    let reader_handle = std::thread::spawn(move || {
        let mut searches = 0u64;
        while !stop_for_thread.load(std::sync::atomic::Ordering::Relaxed) {
            // Any observable state must parse cleanly — a torn read would
            // surface as Err here.
            let hits = reader_for_thread
                .search("code", &[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], 5, None, false, false, None)
                .expect("reader search must never see a torn store");
            assert!(hits.len() <= 5);
            searches += 1;
        }
        searches
    });

    let total_batches = 10u64;
    for batch in 0..total_batches {
        let points: Vec<serde_json::Value> = (0..25u64)
            .map(|index| {
                let id = batch * 25 + index;
                serde_json::json!({
                    "id": format!("point-{id}"),
                    "vector": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "payload": {"batch": batch},
                })
            })
            .collect();
        store.upsert("code", &points).unwrap();
    }
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    let searches = reader_handle.join().unwrap();
    assert_eq!(store.count("code", None).unwrap(), 250);
    assert_eq!(reader.count("code", None).unwrap(), 250, "reader converges to the final state");
    eprintln!("concurrent reader completed {searches} searches");
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Persist-mode bulk: deferred batches + one closing flush serialize the
/// store a constant number of times, versus the per-op flush that
/// re-serializes per batch (the O(n²)-between-ops cost, F5). Gate = bytes
/// + wall-time, not flush counts.
#[test]
fn persist_mode_bulk_serializes_constant_volume() {
    let (_root, _store_root, store) = phase01_store("persist-bulk");
    store
        .create_collection("code", &serde_json::json!({"size": 64, "distance": "Cosine"}))
        .unwrap();
    let batch = |offset: usize| -> Vec<serde_json::Value> {
        (0..128usize)
            .map(|index| {
                let id = (offset * 128 + index) as u64;
                serde_json::json!({
                    "id": id,
                    "vector": (0..64).map(|dim| ((id * 64 + dim) % 97) as f64 / 97.0).collect::<Vec<f64>>(),
                    "payload": {"file_path": format!("/r/file{}.go", id), "project_id_normalized": "proj"},
                })
            })
            .collect()
    };

    // Bulk leg: 16 deferred batches + 1 flush.
    let started = std::time::Instant::now();
    for offset in 0..16usize {
        store.upsert_deferred("code", &batch(offset)).unwrap();
    }
    store.flush().unwrap();
    let bulk_elapsed = started.elapsed();
    let bulk_bytes = store.flush_bytes_written();

    // Eager leg (today's status quo): 16 flushed batches on a second
    // collection of the same shape.
    store
        .create_collection("eager", &serde_json::json!({"size": 64, "distance": "Cosine"}))
        .unwrap();
    let before_eager = store.flush_bytes_written();
    let eager_started = std::time::Instant::now();
    for offset in 0..16usize {
        store.upsert("eager", &batch(offset)).unwrap();
    }
    let eager_elapsed = eager_started.elapsed();
    let eager_bytes = store.flush_bytes_written() - before_eager;

    eprintln!(
        "persist-mode bulk: {bulk_bytes} bytes in {bulk_elapsed:?}; eager per-op flush: \
         {eager_bytes} bytes in {eager_elapsed:?}"
    );
    // Bulk = 1 create + 1 final serialization; eager = 17 serializations on a
    // growing store. The mechanism gap must stay visible.
    assert!(
        bulk_bytes < eager_bytes / 4,
        "bulk serialized {bulk_bytes}B vs eager {eager_bytes}B — flush coalescing regressed?"
    );
    assert!(
        bulk_elapsed <= eager_elapsed,
        "bulk wall-time {bulk_elapsed:?} exceeded eager {eager_elapsed:?}"
    );
    assert_eq!(store.count("code", None).unwrap(), 2048);
    assert_eq!(store.count("eager", None).unwrap(), 2048);
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// `create_payload_index` is idempotent on local stores (the remote server is
/// idempotent server-side; re-issuing must not accumulate rows).
#[test]
fn payload_index_is_idempotent() {
    let (_root, _store_root, store) = phase01_store("payload-index");
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    for _ in 0..3 {
        store.create_payload_index("code", "project_id_normalized", None).unwrap();
    }
    let info = store.get_collection_info("code").unwrap();
    let indexes = info.pointer("/result/payload_indexes").unwrap().as_array().unwrap();
    assert_eq!(indexes.len(), 1, "duplicate index calls collapse: {indexes:?}");
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Euclid ranks NEAREST-first (ascending distance), matching qdrant; the
/// pre-review sort returned farthest-first (review finding 6).
#[test]
fn euclid_distance_ranks_nearest_first() {
    let (_root, _store_root, store) = phase01_store("euclid-order");
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Euclid"}))
        .unwrap();
    store
        .upsert(
            "code",
            &[
                serde_json::json!({"id": "far", "vector": [10.0], "payload": {}}),
                serde_json::json!({"id": "near", "vector": [0.1], "payload": {}}),
                serde_json::json!({"id": "mid", "vector": [3.0], "payload": {}}),
            ],
        )
        .unwrap();
    let hits = store.search("code", &[0.0], 3, None, false, false, None).unwrap();
    let ids: Vec<&str> = hits.iter().map(|hit| hit["id"].as_str().unwrap()).collect();
    assert_eq!(ids, vec!["near", "mid", "far"], "ascending distance order");
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// Ambiguous selectors refuse: `Some(&[])` ids + no filter must not fall
/// through to the filter branch (which would erase the whole collection /
/// rewrite every payload) — review finding 7.
#[test]
fn empty_ids_selector_refuses_instead_of_wiping() {
    let (_root, _store_root, store) = phase01_store("empty-ids");
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    store
        .upsert(
            "code",
            &[serde_json::json!({"id": "a", "vector": [1.0], "payload": {"k": "v"}})],
        )
        .unwrap();
    assert!(store.delete("code", Some(&[]), None).is_err());
    assert!(store.set_payload("code", &serde_json::json!({"k": "x"}), Some(&[]), None).is_err());
    assert_eq!(store.count("code", None).unwrap(), 1, "no wipe happened");
    store.close();
    let _ = std::fs::remove_dir_all(&_root);
}

/// `reload_from_disk` evicts diverged in-memory (deferred) state from both
/// the handle and the process cache, keeping the lease — the mechanism
/// behind `VectorWriteOps::discard` after a failed pass (review finding 2).
#[test]
fn reload_from_disk_discards_deferred_state() {
    let (_root, store_root, store) = phase01_store("reload-discard");
    store
        .create_collection("code", &serde_json::json!({"size": 1, "distance": "Cosine"}))
        .unwrap();
    store
        .upsert(
            "code",
            &[serde_json::json!({"id": "committed", "vector": [1.0], "payload": {}})],
        )
        .unwrap();
    // Simulate a failed pass: deferred (unflushed) mutation in memory only.
    store
        .upsert_deferred(
            "code",
            &[serde_json::json!({"id": "partial", "vector": [1.0], "payload": {}})],
        )
        .unwrap();
    store.reload_from_disk().unwrap();
    assert_eq!(store.count("code", None).unwrap(), 1, "deferred partial state is gone");
    // A fresh handle on the same cache must agree (cache was swapped too).
    let resolved2 = resolve_storage(
        &_root,
        None,
        &ResolveOverrides {
            data_home: Some(_root.to_string_lossy().into_owned()),
            ..ResolveOverrides::default()
        },
    )
    .unwrap();
    let store2 = LocalQdrantStore::open(&resolved2, StorageRole::Code.as_str()).unwrap();
    assert_eq!(store2.count("code", None).unwrap(), 1);
    let reader = cortex_storage::qdrant::LocalQdrantReader::open(&store_root).unwrap();
    assert_eq!(reader.count("code", None).unwrap(), 1);
    store.close();
    store2.close();
    let _ = std::fs::remove_dir_all(&_root);
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
