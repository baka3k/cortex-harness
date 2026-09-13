//! Unit test Phase 02: `manifest_candidates` per reconciliation mode — gồm
//! invalid identity → REJECTED và Type external dedupe theo file_path.

use cortex_graph_core::journal_manifest::manifest_candidates;
use cortex_graph_core::identity::sha256_hex;
use cortex_graph_core::models::{
    ArtifactRef, BatchSpec, OperationPhase, RunMetadata,
};
use cortex_graph_core::identity::run_fingerprint;
use serde_json::{json, Value};
use std::collections::BTreeMap;

fn metadata() -> RunMetadata {
    RunMetadata {
        project_id: "demo".into(),
        scope_id: "demo".into(),
        source_revision: "rev".into(),
        source_snapshot: "snap".into(),
        physical_target: "local".into(),
        generation: "gen".into(),
        parser: "python".into(),
        parser_version: "1.0".into(),
        schema_fingerprint: "sfp".into(),
        query_shape_version: "qv".into(),
        contract_version: 1,
        operation_versions: BTreeMap::new(),
    }
}

fn spec(operation: BTreeMap<String, Value>) -> BatchSpec {
    BatchSpec {
        phase: OperationPhase::Nodes,
        operation_key: "op".into(),
        sequence: 0,
        artifact: ArtifactRef {
            sha256: "abc".into(),
            relative_path: "r/abc.jsonl".into(),
            byte_count: 1,
            row_count: 1,
        },
        expected_count: 1,
        required_barriers: vec![],
        produced_barriers: vec![],
        max_attempts: 5,
        operation,
    }
}

fn candidates_for(rows: Vec<Value>, operation: BTreeMap<String, Value>) -> Vec<_Candidate> {
    let run_run = cortex_graph_core::models::RunRecord {
        run_id: "run".into(),
        fingerprint: run_fingerprint(&serde_json::to_value(metadata()).unwrap()),
        metadata: metadata(),
        status: cortex_graph_core::models::RunStatus::Open,
        created_at: "2026-09-13T00:00:00+00:00".into(),
        updated_at: "2026-09-13T00:00:00+00:00".into(),
        retention_until: "2026-09-20T00:00:00+00:00".into(),
        error_code: None,
    };
    let sp = spec(operation);
    let (_, candidates) = manifest_candidates(&run_run, &sp, &rows, "job-1");
    candidates
}

// Alias ngắn cho đọc dễ.
use cortex_graph_core::journal_manifest::ManifestCandidate as _Candidate;

#[test]
fn node_identity_happy_and_duplicate_external_type() {
    let candidates = candidates_for(
        vec![
            json!({"id": "fn-1", "comment": "a"}),
            json!({"kind": "external", "id": "T-1", "file_path": "x.cpp"}),
            json!({"kind": "external", "id": "T-1", "file_path": "y.cpp"}),
        ],
        BTreeMap::from([
            ("reconciliation".into(), Value::from("node_identity")),
            ("node_label".into(), Value::from("Type")),
            ("identity_property".into(), Value::from("id")),
        ]),
    );
    assert_eq!(candidates.len(), 3);
    assert!(candidates.iter().all(|c| c.scope.as_deref() == Some("demo")));
    // Row 2 và 3: file_path khác nhau nhưng digest giống (external Type bỏ
    // file_path khỏi payload identity).
    assert_eq!(candidates[1].payload_digest, candidates[2].payload_digest);
    assert_ne!(candidates[0].payload_digest, candidates[1].payload_digest);
    assert_eq!(candidates[0].identity_type, "string");
    assert_eq!(candidates[0].identity_json, "\"fn-1\"");
}

#[test]
fn node_identity_missing_identity_is_rejected_with_scope() {
    let candidates = candidates_for(
        vec![json!({"comment": "no id here"})],
        BTreeMap::from([
            ("reconciliation".into(), Value::from("node_identity")),
            ("node_label".into(), Value::from("Function")),
            ("identity_property".into(), Value::from("id")),
        ]),
    );
    assert_eq!(candidates.len(), 1);
    assert_eq!(candidates[0].disposition, "rejected");
    assert_eq!(candidates[0].identity_type, "invalid");
    assert_eq!(candidates[0].identity_json, "null");
    // scope vẫn set (được gán trước khi thử identity).
    assert_eq!(candidates[0].scope.as_deref(), Some("demo"));
}

#[test]
fn cleanup_reconciliations_emit_no_candidates() {
    for mode in ["file_cleanup", "orphan_unknown_cleanup"] {
        let candidates = candidates_for(
            vec![json!({"id": "x"})],
            BTreeMap::from([("reconciliation".into(), Value::from(mode))]),
        );
        assert!(candidates.is_empty(), "{mode} phải bỏ qua row");
    }
}

#[test]
fn call_edge_and_site_edges() {
    let candidates = candidates_for(
        vec![
            json!({"caller_id": "a", "callee_id": "b"}),
            json!({"caller_id": "a", "callee_id": "b", "site_id": "s1"}),
            json!({"caller_id": "a", "callee_id": "b", "site_id": ""}),
        ],
        BTreeMap::from([
            ("reconciliation".into(), Value::from("call_edge")),
            ("producer_id".into(), Value::from("p")),
        ]),
    );
    assert_eq!(candidates.len(), 3);
    assert_eq!(candidates[0].relationship_type, "CALLS");
    assert_eq!(candidates[0].identity_type, "edge_key");
    assert_eq!(candidates[0].endpoints.len(), 2);
    assert_eq!(candidates[0].endpoints[0].0, "source");
    // call_edge không có edge property → identity_json không chứa key "edge"
    assert!(!candidates[0].identity_json.contains("site_id"));
    // Dòng 2: identity_json chứa edge [site_id,...] — "call_edge" dù có site_id
    // vẫn KHÔNG dùng site (edge_property chỉ áp dụng call_site) → giống dòng 0.
    assert_eq!(candidates[1].identity_json, candidates[0].identity_json);
}

#[test]
fn call_site_missing_site_id_is_rejected() {
    let candidates = candidates_for(
        vec![json!({"caller_id": "a", "callee_id": "b", "site_id": ""})],
        BTreeMap::from([
            ("reconciliation".into(), Value::from("call_site")),
            ("producer_id".into(), Value::from("p")),
        ]),
    );
    assert_eq!(candidates.len(), 1);
    assert_eq!(candidates[0].disposition, "rejected");
    // Python error path: relationship_type = row.get("rel_type") or "invalid"
    // — call_site rows không mang rel_type → "invalid".
    assert_eq!(candidates[0].relationship_type, "invalid");
}

#[test]
fn possible_call_site_uses_possible_calls_relationship() {
    let candidates = candidates_for(
        vec![json!({"caller_id": "a", "callee_id": "b", "site_id": "s-9"})],
        BTreeMap::from([
            ("reconciliation".into(), Value::from("possible_call_site")),
            ("producer_id".into(), Value::from("p")),
        ]),
    );
    assert_eq!(candidates[0].relationship_type, "POSSIBLE_CALLS");
    assert!(candidates[0].identity_json.contains("site_id"));
    assert_eq!(candidates[0].disposition, "staged_unique");
}

#[test]
fn repository_file_shape() {
    let candidates = candidates_for(
        vec![json!({"repo": "r1", "id": "f1"})],
        BTreeMap::from([("reconciliation".into(), Value::from("repository_file"))]),
    );
    assert_eq!(candidates[0].relationship_type, "HAS_FILE");
    assert!(candidates[0].identity_json.contains("Repository"));
    assert!(candidates[0].identity_json.contains("HAS_FILE"));
}

#[test]
fn typed_relationship_keyed_edge_missing_identity_rejected() {
    let candidates = candidates_for(
        vec![json!({
            "source_label": "Class", "target_label": "Function",
            "source_id": "c1", "target_id": "f1", "rel_type": "HAS_METHOD",
            "edge_property": "order", "edge_id": ""
        })],
        BTreeMap::from([("reconciliation".into(), Value::from("typed_relationship"))]),
    );
    assert_eq!(candidates[0].disposition, "rejected");
    assert_eq!(candidates[0].relationship_type, "HAS_METHOD");
}

#[test]
fn typed_relationship_happy_path_includes_edge_component() {
    let candidates = candidates_for(
        vec![json!({
            "source_label": "Class", "target_label": "Function",
            "source_id": "c1", "target_id": 7, "rel_type": "HAS_METHOD",
            "edge_property": "order", "edge_id": 3
        })],
        BTreeMap::from([("reconciliation".into(), Value::from("typed_relationship"))]),
    );
    assert_eq!(candidates[0].disposition, "staged_unique");
    // target int → integer identity; edge component [order, integer, 3].
    assert!(candidates[0].identity_json.contains(r#""edge":["order","integer","3"]"#));
    assert!(candidates[0].identity_json.contains(r#""target":["Function","id","integer","7"]"#));
}

#[test]
fn unsupported_reconciliation_is_rejected_as_edge() {
    let candidates = candidates_for(
        vec![json!({"rel_type": "UNKNOWN"})],
        BTreeMap::from([("reconciliation".into(), Value::from("unsupported"))]),
    );
    assert_eq!(candidates[0].disposition, "rejected");
    assert_eq!(candidates[0].relationship_type, "UNKNOWN");
}

#[test]
fn manifest_id_is_deterministic() {
    let a = candidates_for(vec![json!({"id": "x"})], BTreeMap::from([
        ("reconciliation".into(), Value::from("node_identity")),
        ("node_label".into(), Value::from("Function")),
    ]));
    let b = candidates_for(vec![json!({"id": "x"})], BTreeMap::from([
        ("reconciliation".into(), Value::from("node_identity")),
        ("node_label".into(), Value::from("Function")),
    ]));
    assert_eq!(a[0].manifest_id, b[0].manifest_id);
    // manifest_id = sha256(canonical {"job_id","kind","row"})
    let expected = sha256_hex(
        br#"{"job_id":"job-1","kind":"node","row":0}"#,
    );
    assert_eq!(a[0].manifest_id, expected);
}

#[test]
fn quarantine_legacy_targets_fences_superseded_runs() {
    use cortex_graph_core::journal::Journal;

    let temp = std::env::temp_dir().join("cortex_journal_quarantine_test");
    let _ = std::fs::remove_dir_all(&temp);
    std::fs::create_dir_all(&temp).expect("temp dir");
    let journal = Journal::open(
        &temp.join("j.sqlite"),
        &temp.join("artifacts"),
        cortex_graph_core::models::JournalLimits::default(),
        Box::new(|| 1_000_000.0),
    )
    .expect("open journal");
    let meta = metadata();
    let run = journal.open_run(&meta, 0, 0).expect("open run");

    // Run "legacy" cùng project/scope/parser nhưng physical_target cũ.
    let mut legacy_meta = metadata();
    legacy_meta.physical_target = "old-target".into();
    let legacy_run = journal.open_run(&legacy_meta, 0, 0).expect("open legacy run");

    // metadata hiện tại (physical_target = "local") + danh sách superseded
    // targets → run legacy bị quarantine, run hiện tại không.
    let quarantined = journal
        .quarantine_legacy_targets(&meta, &["old-target".to_string()])
        .expect("quarantine");
    assert_eq!(quarantined, 1);
    let fenced = journal
        .get_run(&legacy_run.run_id)
        .expect("get run")
        .expect("run tồn tại");
    assert_eq!(fenced.status, cortex_graph_core::models::RunStatus::Quarantined);
    assert_eq!(
        fenced.error_code,
        Some(cortex_graph_core::models::TerminalErrorCode::IncompatibleSchema)
    );
    let current = journal.get_run(&run.run_id).expect("get run").expect("run tồn tại");
    assert_eq!(current.status, cortex_graph_core::models::RunStatus::Open);

    // Idempotent: run đã quarantine không còn active → 0.
    let again = journal
        .quarantine_legacy_targets(&meta, &["old-target".to_string()])
        .expect("quarantine lại");
    assert_eq!(again, 0);

    // Target trùng physical_target hiện tại bị bỏ qua khỏi tập match.
    let same = journal
        .quarantine_legacy_targets(&meta, &[meta.physical_target.clone(), "".to_string()])
        .expect("quarantine same target");
    assert_eq!(same, 0);
}
