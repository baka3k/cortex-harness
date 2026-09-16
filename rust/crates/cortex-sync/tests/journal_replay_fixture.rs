//! Phase-03 gates — journal replay driver:
//!
//! 1. LEGACY fixture byte-compat (negative): the real legacy journal on disk
//!    has producers/blocks that never completed (`unconserved` manifest) —
//!    the native drain must refuse the endpoint-audit seal EXACTLY like the
//!    python consumer (fail-closed parity), never half-drain edge batches.
//! 2. SYNTHETIC journal (positive): a clean journal built through the same
//!    producer primitives drains end-to-end on a scratch ladybug store —
//!    receipts written, double-drain no-op, incompatible fingerprint fails
//!    closed.

use std::collections::BTreeMap;
use std::path::Path;

use cortex_graph_core::journal::Journal;
use cortex_graph_core::models::JournalLimits;
use cortex_graph_core::models::{BatchSpec, OperationPhase, RunMetadata};
use cortex_graph_writer::store::LadybugStore;
use cortex_sync::journal_replay::{resume_journal, ReplayConfig};

const FIXTURE_DIR: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../tests/fixtures/sync-plane-golden/journal"
);
const FIXTURE_DB: &str = "cplus-legacy.sqlite3";

fn now_epoch_s() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or_default()
}

fn open_journal(path: &Path) -> Journal {
    Journal::open(
        path,
        &path.parent().unwrap().join("artifacts"),
        JournalLimits::default(),
        Box::new(now_epoch_s),
    )
    .expect("open journal")
}

fn copy_dir(source: &Path, dest: &Path) {
    std::fs::create_dir_all(dest).expect("mkdir");
    for entry in std::fs::read_dir(source).expect("readdir") {
        let entry = entry.expect("entry");
        let target = dest.join(entry.file_name());
        if entry.path().is_dir() {
            copy_dir(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), &target).expect("copy file");
        }
    }
}

/// Fixture copy + resume-prep: flip legacy bookkeeping to the state the
/// dead producer HAD completed in reality (runs open, count-complete node
/// barriers drained, producers complete + sentinel) — python consumer trên
/// fixture nguyên trạng cũng refuse; prep chỉ mô phỏng resume hợp lệ.
fn prepare_fixture_copy() -> (tempfile::TempDir, std::path::PathBuf, String) {
    let scratch = tempfile::tempdir().expect("tempdir");
    let journal_path = scratch.path().join("cplus.sqlite3");
    std::fs::copy(Path::new(FIXTURE_DIR).join(FIXTURE_DB), &journal_path)
        .expect("copy fixture sqlite");
    let source_artifacts = Path::new(FIXTURE_DIR).join("artifacts");
    copy_dir(&source_artifacts, &scratch.path().join("artifacts"));

    let conn = rusqlite::Connection::open(&journal_path).expect("open copy");
    conn.execute("UPDATE runs SET status = 'open' WHERE status = 'blocked'", [])
        .expect("flip run statuses");
    conn.execute(
        "UPDATE barriers SET status = 'drained' WHERE name = 'phase:nodes' \
         AND produced_count = drained_count",
        [],
    )
    .expect("drain node barriers");
    conn.execute(
        "UPDATE producer_completion SET status = 'complete' WHERE status = 'open'",
        [],
    )
    .expect("complete producers");
    conn.execute(
        "INSERT INTO producer_completion (run_id, producer_id, status, updated_at) \
         SELECT DISTINCT run_id, '__journal_all_producers_complete__', 'complete', \
         datetime('now') FROM producer_completion",
        [],
    )
    .expect("insert producers-complete sentinel");
    let run_id: String = conn
        .query_row(
            "SELECT run_id FROM batches WHERE status = 'pending' GROUP BY run_id \
             ORDER BY count(*) DESC LIMIT 1",
            [],
            |row| row.get(0),
        )
        .expect("a run with pending batches exists");
    drop(conn);
    (scratch, journal_path, run_id)
}

fn load_metadata(journal_path: &Path, run_id: &str) -> serde_json::Value {
    let conn = rusqlite::Connection::open(journal_path).expect("open");
    let raw: String = conn
        .query_row(
            "SELECT metadata_json FROM runs WHERE run_id = ?1",
            [run_id],
            |row| row.get(0),
        )
        .expect("metadata_json");
    serde_json::from_str(&raw).expect("metadata json")
}

#[test]
fn legacy_fixture_unconserved_manifest_refuses_audit_seal() {
    let (scratch, journal_path, run_id) = prepare_fixture_copy();
    let metadata = load_metadata(&journal_path, &run_id);
    let config = ReplayConfig {
        path: journal_path,
        metadata: serde_json::from_value(metadata).expect("RunMetadata"),
        limits: Default::default(),
        lease_seconds: 300,
    };
    let store_path = scratch.path().join("store.lbug");
    let mut store = LadybugStore::open(&store_path, "sp3legacy").expect("open scratch store");
    let error = resume_journal(&config, &mut store, None).expect_err(
        "legacy edge batches must refuse to drain on a scratch store (python parity)",
    );
    // consumer.py `_seal_endpoint_audit_if_ready` chạy audit trước seal →
    // endpoint thiếu trên store mới → INVALID_CONTRACT (trước cả gate
    // conservation trong seal_endpoint_audit).
    assert_eq!(error.code, "invalid_contract", "{:?}", error.message);
    assert!(
        error
            .message
            .contains("missing or ambiguous endpoints"),
        "refusal must come from the endpoint audit: {:?}",
        error.message
    );
}

fn base_metadata(query_shape_version: &str) -> RunMetadata {
    serde_json::from_value(serde_json::json!({
        "project_id": "sp3legacy",
        "scope_id": "scopesp3legacy",
        "source_revision": "rev0",
        "source_snapshot": "snap0",
        "physical_target": "storage-target:v1:test",
        "generation": "gen0",
        "parser": "cplus",
        "parser_version": "1",
        "schema_fingerprint": cortex_graph_core::schema_manifest::code_graph_schema().fingerprint(),
        "query_shape_version": query_shape_version,
        "operation_versions": {"graph-write": 1},
        "contract_version": 1,
    }))
    .expect("metadata")
}

fn synthetic_run_id() -> String {
    cortex_graph_core::identity::run_id(&base_metadata("language-writer-v1").to_dict())
}

/// Build a clean journal: 1 node batch (File upserts) + 1 edge batch
/// (repository_file), no barriers — minimal claimable run.
fn build_synthetic_journal(journal_path: &Path) {
    let journal = open_journal(journal_path);
    let metadata = base_metadata("language-writer-v1");
    let run = journal.open_run(&metadata, 4, 8192).expect("open run");
    let run_id = run.run_id.clone();

    let node_rows: Vec<serde_json::Value> = ["a.cpp", "b.cpp"]
        .iter()
        .map(|name| {
            serde_json::json!({
                "id": format!("src/{name}"),
                "project_id": "sp3legacy",
                "project_id_normalized": "sp3legacy",
                "repo": "sp3legacy/repo",
            })
        })
        .collect();
    let node_artifact = journal.create_artifact(&run_id, &node_rows).expect("node artifact");
    let node_operation: BTreeMap<String, serde_json::Value> = [
        ("label", serde_json::json!("files")),
        ("phase", serde_json::json!("nodes")),
        ("version", serde_json::json!(1)),
        ("idempotent", serde_json::json!(true)),
        ("reconciliation", serde_json::json!("node_identity")),
        ("node_label", serde_json::json!("File")),
        ("identity_property", serde_json::json!("id")),
        ("row_identity_property", serde_json::json!("id")),
        ("mutation_kind", serde_json::json!("merge")),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect();
    journal
        .enqueue_batch(
            &run_id,
            BatchSpec {
                phase: OperationPhase::Nodes,
                operation_key: "graph-write/v1/nodes/files".to_string(),
                sequence: 0,
                artifact: node_artifact,
                expected_count: node_rows.len() as i64,
                required_barriers: vec![],
                produced_barriers: vec!["phase:nodes".to_string()],
                max_attempts: 5,
                operation: node_operation,
            },
        )
        .expect("enqueue node batch");

    let edge_rows: Vec<serde_json::Value> = ["a.cpp", "b.cpp"]
        .iter()
        .map(|name| {
            serde_json::json!({
                "id": format!("src/{name}"),
                "repo": "sp3legacy/repo",
                "project_id": "sp3legacy",
                "project_id_normalized": "sp3legacy",
            })
        })
        .collect();
    let edge_artifact = journal.create_artifact(&run_id, &edge_rows).expect("edge artifact");
    let edge_operation: BTreeMap<String, serde_json::Value> = [
        ("label", serde_json::json!("repo_file_edges")),
        ("phase", serde_json::json!("relationships")),
        ("version", serde_json::json!(1)),
        ("idempotent", serde_json::json!(true)),
        ("reconciliation", serde_json::json!("repository_file")),
        ("mutation_kind", serde_json::json!("merge")),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect();
    journal
        .enqueue_batch(
            &run_id,
            BatchSpec {
                phase: OperationPhase::Relationships,
                operation_key: "graph-write/v1/relationships/repo_file_edges".to_string(),
                sequence: 1,
                artifact: edge_artifact,
                expected_count: edge_rows.len() as i64,
                required_barriers: vec![],
                produced_barriers: vec![],
                max_attempts: 5,
                operation: edge_operation,
            },
        )
        .expect("enqueue edge batch");
    drop(journal);
}

fn seed_file_nodes(store_path: &Path, graph: &str) {
    use cortex_graph_writer::store::GraphStore as _;
    let mut store = LadybugStore::open(store_path, graph).expect("open store for seed");
    let rows: Vec<serde_json::Value> = ["a.cpp", "b.cpp"]
        .iter()
        .map(|name| {
            serde_json::json!({
                "id": format!("src/{name}"),
                "project_id": "sp3legacy",
                "project_id_normalized": "sp3legacy",
            })
        })
        .collect();
    let mut params = BTreeMap::new();
    params.insert("rows".to_string(), serde_json::json!(rows));
    params.insert("repo".to_string(), serde_json::json!("sp3legacy/repo"));
    store
        .execute_query(
            "UNWIND $rows AS row \
             MERGE (f:File {id: row.id}) \
             SET f.project_id = row.project_id, \
             f.project_id_normalized = row.project_id_normalized \
             WITH row, f \
             MERGE (r:Repository {name: $repo}) \
             SET r.project_id = row.project_id, \
             r.project_id_normalized = row.project_id_normalized",
            &params,
            None,
        )
        .expect("seed file+repository nodes");
}

#[test]
fn synthetic_journal_drains_end_to_end_and_double_drain_noops() {
    let scratch = tempfile::tempdir().expect("tempdir");
    let journal_path = scratch.path().join("cplus.sqlite3");
    build_synthetic_journal(&journal_path);

    let metadata: RunMetadata =
        serde_json::from_value(load_metadata(&journal_path, &synthetic_run_id()))
            .expect("RunMetadata");
    let config = ReplayConfig {
        path: journal_path.clone(),
        metadata,
        limits: Default::default(),
        lease_seconds: 300,
    };
    let store_path = scratch.path().join("store.lbug");
    let graph = "sp3legacy";
    seed_file_nodes(&store_path, graph);

    {
        let mut store = LadybugStore::open(&store_path, graph).expect("open scratch store");
        let drained = resume_journal(&config, &mut store, None).expect("first drain");
        assert_eq!(drained, 2, "node + edge batch must both replay");
    }

    // Double-drain no-op.
    {
        let mut store = LadybugStore::open(&store_path, graph).expect("reopen store");
        let drained_again = resume_journal(&config, &mut store, None).expect("second drain");
        assert_eq!(drained_again, 0, "double drain must be a no-op");
    }

    // GraphWriteReceipt nodes = bằng chứng durable của 2 batch đã ack.
    {
        use cortex_graph_writer::store::GraphStore as _;
        let mut store = LadybugStore::open(&store_path, graph).expect("reopen store");
        let records = store
            .execute_query(
                "MATCH (r:GraphWriteReceipt) RETURN count(r) AS count",
                &BTreeMap::new(),
                None,
            )
            .expect("receipt count");
        let count = records[0]
            .get("count")
            .and_then(serde_json::Value::as_i64)
            .expect("count");
        assert_eq!(count, 2, "receipt nodes must exist per drained batch");
    }

    // Metadata hỏng fingerprint → run identity đổi → get_run None → Ok(0)
    // (python parity: `if journal.get_run(run_id(...)) is None: return 0`).
    // INCOMPATIBLE_SCHEMA chỉ bật khi run tồn tại mà fingerprint lệch —
    // không construct được qua identity bền vững (fingerprint là một phần
    // của run_id); preflight vẫn giữ fail-closed cho đường đó.
    let mut bad = load_metadata(&journal_path, &synthetic_run_id());
    bad["schema_fingerprint"] = serde_json::json!("0".repeat(64));
    let bad_config = ReplayConfig {
        path: journal_path,
        metadata: serde_json::from_value(bad).expect("RunMetadata"),
        limits: Default::default(),
        lease_seconds: 300,
    };
    let mut store = LadybugStore::open(&store_path, graph).expect("reopen");
    let drained_bad = resume_journal(&bad_config, &mut store, None).expect("unknown-run drain");
    assert_eq!(drained_bad, 0, "unknown run identity must drain nothing");
}
