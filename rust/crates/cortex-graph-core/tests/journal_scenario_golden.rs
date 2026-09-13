//! Scenario replay: chạy đúng kịch bản `journal_scenario.json` (ghi từ
//! `SQLiteJournal` Python thật) qua Rust `Journal` và so từng kết quả.
//! `fencing_token` mask khi so (random mỗi bên); mọi field khác phải khớp
//! (job_id/run_id/artifact.sha256 là parity thật của canonical_json).

use cortex_graph_core::journal::{inspect_journal, Journal};
use cortex_graph_core::models::{
    BatchSpec, BatchStatus, JournalLimits, OperationPhase, RunMetadata, TerminalErrorCode,
};
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fixture {
    now_epoch: f64,
    metadata_a: Value,
    metadata_b: Value,
    metadata_c: Value,
    artifact_rows: Vec<Value>,
    artifact_rows_b: Vec<Value>,
    artifact_rows_c1: Vec<Value>,
    artifact_rows_c2: Vec<Value>,
    ops: Vec<OpRecord>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
struct OpRecord {
    op: String,
    #[serde(default)]
    result: Option<Value>,
    #[serde(default)]
    error: Option<Value>,
}

const TOLERANCE: f64 = 1e-9;

fn compare(actual: &Value, expected: &Value, context: &str) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            assert!(
                (a - b).abs() <= TOLERANCE,
                "{context}: rust={a} python={b}"
            );
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{context}: độ dài array khác");
            for (idx, (av, bv)) in a.iter().zip(b.iter()).enumerate() {
                compare(av, bv, &format!("{context}[{idx}]"));
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            for (key, expected_value) in b {
                if key == "fencing_token" {
                    continue; // random mỗi bên — chỉ cần tồn tại
                }
                let actual_value = a.get(key).unwrap_or_else(|| {
                    panic!("{context}: thiếu key `{key}` (rust={actual:?})")
                });
                compare(actual_value, expected_value, &format!("{context}.{key}"));
            }
        }
        _ => assert_eq!(actual, expected, "{context}"),
    }
}

#[test]
fn scenario_replay_matches_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/journal_scenario.json"))
            .expect("fixture hợp lệ");
    let clock_epoch = fixture.now_epoch;

    let temp_dir = std::env::temp_dir().join("cortex_graph_core_scenario");
    std::fs::create_dir_all(&temp_dir).expect("temp dir");
    let db_path = temp_dir.join("scenario.sqlite");
    let artifact_root = temp_dir.join("artifacts");
    let _ = std::fs::remove_dir_all(&temp_dir);
    std::fs::create_dir_all(&temp_dir).expect("temp dir");

    let journal = Journal::open(
        &db_path,
        &artifact_root,
        JournalLimits::default(),
        Box::new(move || clock_epoch),
    )
    .expect("mở journal");

    let meta_a: RunMetadata =
        serde_json::from_value(fixture.metadata_a.clone()).expect("metadata_a");
    let meta_b: RunMetadata =
        serde_json::from_value(fixture.metadata_b.clone()).expect("metadata_b");

    // Index ops theo tên để replay theo thứ tự ghi được.
    let mut op_index = 0usize;
    fn check(
        actual: Result<Value, cortex_graph_core::models::JournalError>,
        expected: &OpRecord,
        context: &str,
    ) {
        match (&actual, &expected.error) {
            (Ok(value), None) => {
                // result null hợp lệ (vd. claim_batch trả None trên queue rỗng).
                let expected_value = expected.result.clone().unwrap_or(Value::Null);
                compare(value, &expected_value, context);
            }
            (Err(error), Some(expected_error)) => {
                let code = expected_error
                    .get("code")
                    .and_then(Value::as_str)
                    .expect("error code");
                assert_eq!(error.code, code, "{context}: error code khác Python");
            }
            (Ok(_), Some(_)) => panic!("{context}: Python lỗi nhưng Rust thành công"),
            (Err(error), None) => panic!("{context}: Rust lỗi nhưng Python thành công: {error}"),
        }
    }

    macro_rules! check_next {
        ($actual:expr, $context:expr) => {{
            let outcome: Result<Value, cortex_graph_core::models::JournalError> = $actual;
            check(outcome, &fixture.ops[op_index], $context);
            op_index += 1;
        }};
    }

    // 1. open_run meta_a (mới)
    let run_a = journal.open_run(&meta_a, 0, 0).expect("open_run meta_a");
    check_next!(Ok(serde_json::to_value(&run_a).unwrap()), "open_run#1");
    // 2. open_run lại → resume
    let resumed = journal.open_run(&meta_a, 0, 0).expect("resume");
    assert_eq!(resumed.run_id, run_a.run_id);
    check_next!(Ok(serde_json::to_value(&resumed).unwrap()), "open_run#2 resume");
    // 3. open_run meta_b (parser khác)
    let run_b = journal.open_run(&meta_b, 0, 0).expect("open_run meta_b");
    check_next!(Ok(serde_json::to_value(&run_b).unwrap()), "open_run#3 meta_b");
    // 4. find_resumable_run
    let resumable = journal.find_resumable_run(&meta_a).expect("find_resumable");
    check_next!(Ok(match &resumable {
            Some(record) => serde_json::to_value(record).unwrap(),
            None => Value::Null,
        }), "find_resumable_run");
    // 5. list_runs
    let runs = journal.list_runs().expect("list_runs");
    check_next!(Ok(serde_json::to_value(&runs).unwrap()), "list_runs");

    // 6. create_artifact
    let artifact = journal
        .create_artifact(&run_a.run_id, &fixture.artifact_rows)
        .expect("create_artifact");
    check_next!(Ok(serde_json::to_value(&artifact).unwrap()), "create_artifact");

    // 7. open_barrier
    let barrier = journal
        .open_barrier(&run_a.run_id, "barrier-tables")
        .expect("open_barrier");
    check_next!(Ok(serde_json::to_value(&barrier).unwrap()), "open_barrier");

    // 8-9. enqueue spec1 + duplicate
    let make_spec = |phase: OperationPhase, operation_key: &str, sequence: i64,
                     required: Vec<String>, produced: Vec<String>| BatchSpec {
        phase,
        operation_key: operation_key.to_string(),
        sequence,
        artifact: artifact.clone(),
        expected_count: fixture.artifact_rows.len() as i64,
        required_barriers: required,
        produced_barriers: produced,
        max_attempts: 5,
        operation: BTreeMap::new(),
    };
    let spec1 = make_spec(
        OperationPhase::Nodes,
        "node.upsert",
        0,
        vec![],
        vec!["barrier-tables".to_string()],
    );
    let batch1 = journal.enqueue_batch(&run_a.run_id, spec1.clone()).expect("enqueue spec1");
    check_next!(Ok(serde_json::to_value(&batch1).unwrap()), "enqueue spec1");
    let duplicate = journal.enqueue_batch(&run_a.run_id, spec1.clone()).expect("enqueue spec1 dup");
    check_next!(Ok(serde_json::to_value(&duplicate).unwrap()), "enqueue spec1 idempotent");
    let spec2 = make_spec(
        OperationPhase::Calls,
        "call.upsert",
        1,
        vec!["barrier-tables".to_string()],
        vec![],
    );
    let batch2 = journal.enqueue_batch(&run_a.run_id, spec2.clone()).expect("enqueue spec2");
    check_next!(Ok(serde_json::to_value(&batch2).unwrap()), "enqueue spec2");

    // 10. get_barrier
    let barrier_now = journal.get_barrier(&run_a.run_id, "barrier-tables").expect("get_barrier");
    check_next!(Ok(match &barrier_now {
            Some(b) => serde_json::to_value(b).unwrap(),
            None => Value::Null,
        }), "get_barrier");

    // 11. close_barrier → produced (1 produced, 0 drained)
    let closed = journal
        .close_barrier(&run_a.run_id, "barrier-tables")
        .expect("close_barrier");
    check_next!(Ok(serde_json::to_value(&closed).unwrap()), "close_barrier");

    // 12. claim spec1
    let claimed = journal
        .claim_batch(Some(&run_a.run_id), 60)
        .expect("claim 1")
        .expect("phải claim được spec1");
    assert_eq!(claimed.job_id, batch1.job_id);
    check_next!(Ok(serde_json::to_value(&claimed).unwrap()), "claim spec1");

    // 13. renew_lease
    let renewed = journal
        .renew_lease(&claimed.job_id, claimed.fencing_token.as_deref().expect("token"), 60)
        .expect("renew");
    check_next!(Ok(serde_json::to_value(&renewed).unwrap()), "renew_lease");

    // 14. ack_batch
    let acked = journal
        .ack_batch(&claimed.job_id, claimed.fencing_token.as_deref().expect("token"), Some(120))
        .expect("ack");
    assert_eq!(acked.status, BatchStatus::Done);
    check_next!(Ok(serde_json::to_value(&acked).unwrap()), "ack spec1");

    // 15. claim spec2 (barrier drained sau ack)
    let claimed2 = journal
        .claim_batch(Some(&run_a.run_id), 60)
        .expect("claim 2")
        .expect("phải claim được spec2");
    assert_eq!(claimed2.job_id, batch2.job_id);
    check_next!(Ok(serde_json::to_value(&claimed2).unwrap()), "claim spec2");

    // 16. ack spec2 (elapsed None)
    let acked2 = journal
        .ack_batch(&claimed2.job_id, claimed2.fencing_token.as_deref().expect("token"), None)
        .expect("ack 2");
    check_next!(Ok(serde_json::to_value(&acked2).unwrap()), "ack spec2");

    // 17. claim → None
    let none_claimed = journal.claim_batch(Some(&run_a.run_id), 60).expect("claim empty");
    check_next!(Ok(match none_claimed {
            Some(record) => serde_json::to_value(&record).unwrap(),
            None => Value::Null,
        }), "claim empty");

    // 18. complete_producers → 0 (không có producer riêng lẻ)
    let completed = journal.complete_producers(&run_a.run_id).expect("complete_producers");
    check_next!(Ok(Value::from(completed)), "complete_producers");

    // 19. enqueue sau complete → invalid_transition
    let rejected = journal.enqueue_batch(&run_a.run_id, spec1.clone());
    let error = rejected.expect_err("phải bị chặn sau producer completion");
    check_next!(Err(error), "enqueue after complete");

    // 20. renew với token sai → stale_fence
    let stale = journal.renew_lease(&batch1.job_id, &"0".repeat(32), 60);
    let error = stale.expect_err("phải stale fence");
    check_next!(Err(error), "stale fence");

    // ── Phase 02: manifest staging + reconciling + conservation ──
    let make_manifest_spec = |phase: OperationPhase,
                              operation_key: &str,
                              sequence: i64,
                              artifact: cortex_graph_core::models::ArtifactRef,
                              expected_count: i64,
                              operation: BTreeMap<String, Value>|
     -> BatchSpec {
        BatchSpec {
            phase,
            operation_key: operation_key.to_string(),
            sequence,
            artifact,
            expected_count,
            required_barriers: vec![],
            produced_barriers: vec![],
            max_attempts: 5,
            operation,
        }
    };

    // 22. open_run meta_c
    let meta_c: RunMetadata =
        serde_json::from_value(fixture.metadata_c.clone()).expect("metadata_c");
    let run_c = journal.open_run(&meta_c, 0, 0).expect("open_run meta_c");
    check_next!(Ok(serde_json::to_value(&run_c).unwrap()), "open_run meta_c");

    // 23. create_artifact run_b (rows dirty)
    let artifact_b = journal
        .create_artifact(&run_b.run_id, &fixture.artifact_rows_b)
        .expect("create_artifact b");
    check_next!(Ok(serde_json::to_value(&artifact_b).unwrap()), "create_artifact b");

    // 24. enqueue dirty (dup + conflict + rejected) → error INVALID_CONTRACT
    let dirty_spec = make_manifest_spec(
        OperationPhase::Nodes,
        "node.upsert",
        0,
        artifact_b.clone(),
        fixture.artifact_rows_b.len() as i64,
        BTreeMap::from([
            ("reconciliation".to_string(), Value::from("node_identity")),
            ("node_label".to_string(), Value::from("Function")),
            ("identity_property".to_string(), Value::from("id")),
            ("mutation_kind".to_string(), Value::from("merge")),
            ("producer_id".to_string(), Value::from("producer-dirty")),
        ]),
    );
    let dirty_error = journal
        .enqueue_batch(&run_b.run_id, dirty_spec)
        .expect_err("dirty manifest phải bị từ chối");
    check_next!(Err(dirty_error), "enqueue dirty manifest");

    // 25. complete_producers trên run blocked → invalid_transition
    let blocked_complete = journal.complete_producers(&run_b.run_id);
    let error = blocked_complete.expect_err("run blocked không complete được");
    check_next!(Err(error), "complete_producers blocked run");

    // 26. recover_run_leases_as_ambiguous run_b → 0
    let recovered = journal
        .recover_run_leases_as_ambiguous(&run_b.run_id)
        .expect("recover run_b");
    check_next!(Ok(Value::from(recovered)), "recover run_b");

    // 27-28. run_c: artifact + enqueue node_identity (có dup)
    let artifact_c1 = journal
        .create_artifact(&run_c.run_id, &fixture.artifact_rows_c1)
        .expect("create_artifact c1");
    check_next!(Ok(serde_json::to_value(&artifact_c1).unwrap()), "create_artifact c1");
    let spec_node = make_manifest_spec(
        OperationPhase::Nodes,
        "node.upsert",
        0,
        artifact_c1.clone(),
        fixture.artifact_rows_c1.len() as i64,
        BTreeMap::from([
            ("reconciliation".to_string(), Value::from("node_identity")),
            ("node_label".to_string(), Value::from("Function")),
            ("identity_property".to_string(), Value::from("id")),
            ("mutation_kind".to_string(), Value::from("merge")),
            ("producer_id".to_string(), Value::from("producer-clean")),
        ]),
    );
    let batch_c1 = journal
        .enqueue_batch(&run_c.run_id, spec_node)
        .expect("enqueue node clean");
    check_next!(Ok(serde_json::to_value(&batch_c1).unwrap()), "enqueue node clean");

    // 29-30. artifact + enqueue call_edge
    let artifact_c2 = journal
        .create_artifact(&run_c.run_id, &fixture.artifact_rows_c2)
        .expect("create_artifact c2");
    check_next!(Ok(serde_json::to_value(&artifact_c2).unwrap()), "create_artifact c2");
    let spec_edge = make_manifest_spec(
        OperationPhase::Calls,
        "call.upsert",
        1,
        artifact_c2.clone(),
        fixture.artifact_rows_c2.len() as i64,
        BTreeMap::from([
            ("reconciliation".to_string(), Value::from("call_edge")),
            ("producer_id".to_string(), Value::from("producer-clean")),
        ]),
    );
    let batch_c2 = journal
        .enqueue_batch(&run_c.run_id, spec_edge)
        .expect("enqueue edge clean");
    check_next!(Ok(serde_json::to_value(&batch_c2).unwrap()), "enqueue edge clean");

    // 31. conservation_summary (trước ack)
    let summary = journal.conservation_summary(&run_c.run_id).expect("conservation 1");
    check_next!(Ok(summary), "conservation_summary pre-ack");

    // 32. claim batch_c1
    let claimed_c = journal
        .claim_batch(Some(&run_c.run_id), 60)
        .expect("claim c1")
        .expect("phải claim được batch_c1");
    assert_eq!(claimed_c.job_id, batch_c1.job_id);
    check_next!(Ok(serde_json::to_value(&claimed_c).unwrap()), "claim c1");

    // 33. mark_reconciling
    let reconciling = journal
        .mark_reconciling(
            &batch_c1.job_id,
            claimed_c.fencing_token.as_deref().expect("token"),
            None,
        )
        .expect("mark_reconciling");
    check_next!(Ok(serde_json::to_value(&reconciling).unwrap()), "mark_reconciling");

    // 34. schedule_reconciliation_retry (retry_at = now theo clock cố định)
    let retried = journal
        .schedule_reconciliation_retry(
            &batch_c1.job_id,
            reconciling.fencing_token.as_deref().expect("token"),
            &cortex_graph_core::journal::iso_from_epoch(clock_epoch),
            TerminalErrorCode::InvalidContract,
        )
        .expect("schedule_reconciliation_retry");
    check_next!(Ok(serde_json::to_value(&retried).unwrap()), "schedule_reconciliation_retry");

    // 35. claim_reconciling → fence lại batch_c1
    let fenced = journal
        .claim_reconciling(Some(&run_c.run_id), 60)
        .expect("claim_reconciling")
        .expect("phải fence được batch reconcile");
    assert_eq!(fenced.job_id, batch_c1.job_id);
    check_next!(Ok(serde_json::to_value(&fenced).unwrap()), "claim_reconciling");

    // 36. ack batch_c1 với token reconcile
    let acked_c1 = journal
        .ack_batch(&batch_c1.job_id, fenced.fencing_token.as_deref().expect("token"), Some(100))
        .expect("ack c1");
    check_next!(Ok(serde_json::to_value(&acked_c1).unwrap()), "ack c1");

    // 37-38. claim + ack batch_c2
    let claimed_c2 = journal
        .claim_batch(Some(&run_c.run_id), 60)
        .expect("claim c2")
        .expect("phải claim được batch_c2");
    assert_eq!(claimed_c2.job_id, batch_c2.job_id);
    check_next!(Ok(serde_json::to_value(&claimed_c2).unwrap()), "claim c2");
    let acked_c2 = journal
        .ack_batch(&batch_c2.job_id, claimed_c2.fencing_token.as_deref().expect("token"), None)
        .expect("ack c2");
    check_next!(Ok(serde_json::to_value(&acked_c2).unwrap()), "ack c2");

    // 39. complete_producers run_c → 1
    let completed_c = journal.complete_producers(&run_c.run_id).expect("complete c");
    check_next!(Ok(Value::from(completed_c)), "complete_producers run_c");

    // 40. conservation_summary (sau ack) → conserved
    let summary_c = journal.conservation_summary(&run_c.run_id).expect("conservation 2");
    assert_eq!(
        summary_c["conserved"], Value::Bool(true),
        "run_c phải conserved: {summary_c}"
    );
    check_next!(Ok(summary_c), "conservation_summary post-ack");

    // 41. seal_endpoint_audit → "sealed"
    let sealed = journal
        .seal_endpoint_audit(&run_c.run_id, None, None, None)
        .expect("seal");
    check_next!(Ok(Value::from(sealed)), "seal_endpoint_audit");

    // 42. seal lại với digest khác → immutable error
    let immutable = journal.seal_endpoint_audit(&run_c.run_id, Some("deadbeef"), None, None);
    let error = immutable.expect_err("seal là immutable");
    check_next!(Err(error), "seal immutable");

    // 43. endpoint_audit_status → "sealed"
    let status = journal.endpoint_audit_status(&run_c.run_id).expect("audit status");
    check_next!(
        Ok(status.map(Value::from).unwrap_or(Value::Null)),
        "endpoint_audit_status"
    );

    // 44. recover_run_leases_as_ambiguous run_c → 0
    let recovered_c = journal
        .recover_run_leases_as_ambiguous(&run_c.run_id)
        .expect("recover run_c");
    check_next!(Ok(Value::from(recovered_c)), "recover run_c");

    // 45. inspect — mask journal_bytes
    let summary = inspect_journal(&db_path, fixture.now_epoch).expect("inspect");
    let summary_value = serde_json::to_value(&summary).unwrap();
    let expected_summary = &fixture.ops[op_index].result.as_ref().expect("inspect result");
    compare(
        &summary_value,
        &Value::Array(
            expected_summary
                .as_array()
                .expect("inspect là list")
                .iter()
                .map(|run| {
                    let mut run = run.clone();
                    if let Some(obj) = run.as_object_mut() {
                        obj.remove("journal_bytes");
                        // tuổi batch unfinished phụ thuộc clock thật lúc ghi
                        // fixture (Python inspect_journal không nhận clock) —
                        // mask như fencing_token.
                        obj.remove("oldest_unfinished_age_seconds");
                    }
                    run
                })
                .collect(),
        ),
        "inspect",
    );

    drop(journal);
    let _ = std::fs::remove_file(&db_path);
    let _ = std::fs::remove_dir_all(&artifact_root);
}
