//! Replay JSONL shadow capture (Track B, phase 1309-2104) qua journal core
//! Rust (phase-05/07 port) thành một SQLite shadow store.
//!
//! Native binary — đúng decision record phase-06 của plan
//! `260913-1715-rust-retrieval-graph-port` (không thêm FFI/PyO3 mới).
//!
//! Input: file JSONL do capture B1 (`code-tiny/tools/graph/journal/shadow.py`)
//! hoặc `gen_journal_scenario.py --emit-dir` sinh ra. Dòng đầu là header
//! (`_header` + `schema_version`, được verify khớp crate); các key prefix
//! `_` (debug) bị bỏ qua.
//!
//! Usage:
//!   replay_journal --input <capture.jsonl> --output <store.sqlite3> \
//!     [--artifact-root <dir>] [--bench] [--skip-unsupported]
//!
//! `--bench`: đo ops/s và MB-ghi/s trên chính luồng replay (phase B3).
//!
//! Lưu ý thiết kế (khớp side Python `scripts/rust_parity/replay_journal_python.py`):
//! - `fencing_token` do store sinh ra tại thời điểm claim; replay dùng token
//!   của store đang replay (track theo job_id) cho các op phụ thuộc — token
//!   trong capture chỉ là fallback. Diff tool chuẩn hoá cột này.
//! - Payload `operation` của `BatchSpec` bị strip (manifest staging chưa
//!   được port sang Rust — phase-07); CẢ HAI replay side làm như nhau nên
//!   DB-state vẫn so được (giới hạn ghi nhận ở phase-B3).
//! - Op ghi lỗi trong capture: replay verify Rust lỗi ĐÚNG code đã ghi.
//! - Op chưa hỗ trợ replay (`claim_reconciling_job`,
//!   `schedule_reconciliation_retry`) → lỗi, trừ khi `--skip-unsupported`.

use cortex_graph_core::journal::{Journal, JOURNAL_SCHEMA_VERSION};
use cortex_graph_core::models::{
    BatchRecord, BatchSpec, JournalLimits, RetryClass, RunMetadata, TerminalErrorCode,
};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Instant;

const USAGE: &str = "usage: replay_journal --input <capture.jsonl> --output <store.sqlite3> [--artifact-root <dir>] [--bench] [--skip-unsupported]";

struct Args {
    input: PathBuf,
    output: PathBuf,
    artifact_root: Option<PathBuf>,
    bench: bool,
    skip_unsupported: bool,
}

fn parse_args() -> Option<Args> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut input = None;
    let mut output = None;
    let mut artifact_root = None;
    let mut bench = false;
    let mut skip_unsupported = false;
    let mut index = 0;
    while index < argv.len() {
        match argv[index].as_str() {
            "--input" => {
                index += 1;
                input = Some(PathBuf::from(argv.get(index)?));
            }
            "--output" => {
                index += 1;
                output = Some(PathBuf::from(argv.get(index)?));
            }
            "--artifact-root" => {
                index += 1;
                artifact_root = Some(PathBuf::from(argv.get(index)?));
            }
            "--bench" => bench = true,
            "--skip-unsupported" => skip_unsupported = true,
            "--help" | "-h" => return None,
            other => {
                eprintln!("unknown flag: {other}");
                return None;
            }
        }
        index += 1;
    }
    Some(Args {
        input: input?,
        output: output?,
        artifact_root,
        bench,
        skip_unsupported,
    })
}

/// Lỗi replay: giữ `code` (TerminalErrorCode) để so khớp với error mà capture
/// đã ghi, kèm message ngữ cảnh.
struct ReplayError {
    code: Option<String>,
    message: String,
}

impl ReplayError {
    fn journal(error: cortex_graph_core::models::JournalError) -> Self {
        Self {
            code: Some(error.code),
            message: error.message,
        }
    }

    fn msg(message: impl Into<String>) -> Self {
        Self {
            code: None,
            message: message.into(),
        }
    }
}

impl From<ReplayError> for String {
    fn from(value: ReplayError) -> Self {
        value.message
    }
}

/// Trạng thái replay: clock điều khiển được + token/run map để op phụ thuộc
/// dùng giá trị của store đang replay (xem docstring đầu file).
struct ReplayState {
    clock: Arc<Mutex<f64>>,
    tokens: HashMap<String, String>,
    run_by_job: HashMap<String, String>,
    last_run: Option<String>,
}

/// Bộ đếm op bị bỏ qua (chỉ-đọc / chưa hỗ trợ).
#[derive(Default)]
struct SkipCounters {
    readonly: usize,
    unsupported: usize,
}

struct ReplayStats {
    ops: usize,
    skipped_readonly: usize,
    skipped_unsupported: usize,
}

fn main() {
    let Some(args) = parse_args() else {
        eprintln!("{USAGE}");
        std::process::exit(2);
    };
    if let Err(error) = run(&args) {
        eprintln!("replay_journal: {error}");
        std::process::exit(1);
    }
}

fn run(args: &Args) -> Result<(), String> {
    let raw = std::fs::read_to_string(&args.input)
        .map_err(|e| format!("cannot read input {}: {e}", args.input.display()))?;
    let input_bytes = raw.len() as u64;

    let mut lines = raw.lines().filter(|line| !line.trim().is_empty());
    let header_line = lines
        .next()
        .ok_or_else(|| "capture rỗng — thiếu header".to_string())?;
    let header: Value =
        serde_json::from_str(header_line).map_err(|e| ReplayError::msg(format!("header không hợp lệ: {e}")))?;
    if header.get("_header") != Some(&Value::Bool(true)) {
        return Err("dòng đầu file phải là header (\"_header\": true)".to_string());
    }
    let schema_version = header
        .get("schema_version")
        .and_then(Value::as_i64)
        .ok_or_else(|| "header thiếu schema_version".to_string())?;
    if schema_version != JOURNAL_SCHEMA_VERSION {
        return Err(format!(
            "schema_version {schema_version} không khớp crate ({JOURNAL_SCHEMA_VERSION}) — format drift"
        ));
    }

    // Store output là sản phẩm của tool này: xoá sạch trước khi replay.
    for stale in [
        args.output.clone(),
        PathBuf::from(format!("{}-wal", args.output.display())),
        PathBuf::from(format!("{}-shm", args.output.display())),
    ] {
        let _ = std::fs::remove_file(&stale);
    }
    let (artifact_root, defaulted_root) = match &args.artifact_root {
        Some(dir) => (dir.clone(), false),
        None => (
            PathBuf::from(format!("{}.artifacts", args.output.display())),
            true,
        ),
    };
    if defaulted_root {
        let _ = std::fs::remove_dir_all(&artifact_root);
    }

    // Clock: dùng `_captured_at_epoch` của từng op (fallback now_epoch header
    // hoặc đồng hồ thật) → timestamp store replay bám sát capture.
    let initial_epoch = header.get("now_epoch").and_then(Value::as_f64).unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_secs_f64())
            .unwrap_or(0.0)
    });
    let mut state = ReplayState {
        clock: Arc::new(Mutex::new(initial_epoch)),
        tokens: HashMap::new(),
        run_by_job: HashMap::new(),
        last_run: None,
    };
    let journal = Journal::open(
        &args.output,
        &artifact_root,
        limits_from_header(&header),
        {
            let clock = state.clock.clone();
            Box::new(move || *clock.lock().unwrap())
        },
    )
    .map_err(ReplayError::journal)?;

    let started = Instant::now();
    let mut counters = SkipCounters::default();
    let mut stats = ReplayStats {
        ops: 0,
        skipped_readonly: 0,
        skipped_unsupported: 0,
    };
    for line in lines {
        let value: Value = serde_json::from_str(line)
            .map_err(|e| ReplayError::msg(format!("dòng op không hợp lệ: {e}")))?;
        if value.get("_header") == Some(&Value::Bool(true)) {
            continue; // header lặp (append nhiều runtime) — bỏ qua
        }
        if let Some(epoch) = value.get("_captured_at_epoch").and_then(Value::as_f64) {
            *state.clock.lock().unwrap() = epoch;
        }
        let op = value
            .get("op")
            .and_then(Value::as_str)
            .ok_or_else(|| "dòng op thiếu trường \"op\"".to_string())?;
        let empty = serde_json::Map::new();
        let op_args = value.get("args").and_then(Value::as_object).unwrap_or(&empty);
        let expected_error = value
            .get("error")
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .map(str::to_string);

        let outcome = dispatch(&journal, op, op_args, &mut state, &mut counters, args);
        verify_outcome(op, outcome, expected_error.as_deref())?;
        stats.ops += 1;
    }
    stats.skipped_readonly = counters.readonly;
    stats.skipped_unsupported = counters.unsupported;

    if args.bench {
        let elapsed = started.elapsed().as_secs_f64();
        let ops_per_s = if elapsed > 0.0 {
            stats.ops as f64 / elapsed
        } else {
            0.0
        };
        let mib = input_bytes as f64 / (1024.0 * 1024.0);
        let mib_per_s = if elapsed > 0.0 { mib / elapsed } else { 0.0 };
        println!(
            "bench: ops={} elapsed_s={elapsed:.3} ops_per_s={ops_per_s:.0} input_bytes={input_bytes} mib_per_s={mib_per_s:.2} skipped_readonly={} skipped_unsupported={}",
            stats.ops, stats.skipped_readonly, stats.skipped_unsupported
        );
    } else {
        eprintln!(
            "replayed {} ops (skipped readonly {}, unsupported {}) → {}",
            stats.ops,
            stats.skipped_readonly,
            stats.skipped_unsupported,
            args.output.display()
        );
    }
    Ok(())
}

fn limits_from_header(header: &Value) -> JournalLimits {
    let mut limits = JournalLimits::default();
    let Some(limits_json) = header
        .get("journal_config")
        .and_then(|config| config.get("limits"))
        .and_then(Value::as_object)
    else {
        return limits;
    };
    macro_rules! take {
        ($field:ident) => {
            if let Some(v) = limits_json.get(stringify!($field)).and_then(Value::as_i64) {
                limits.$field = v;
            }
        };
    }
    take!(max_batches_per_run);
    take!(max_payload_bytes_per_run);
    take!(max_artifact_bytes);
    take!(max_journal_bytes);
    take!(min_free_bytes);
    take!(retention_seconds);
    take!(busy_timeout_ms);
    take!(wal_autocheckpoint_pages);
    limits
}

fn arg_str<'a>(
    op_args: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a str, ReplayError> {
    op_args
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| ReplayError::msg(format!("args thiếu string \"{key}\"")))
}

fn arg_i64(op_args: &serde_json::Map<String, Value>, key: &str, default: i64) -> i64 {
    op_args.get(key).and_then(Value::as_i64).unwrap_or(default)
}

/// Token dùng cho op phụ thuộc: ưu tiên token của store đang replay.
fn token_for(state: &ReplayState, job_id: &str, fallback: Option<&str>) -> String {
    state
        .tokens
        .get(job_id)
        .cloned()
        .or_else(|| fallback.map(str::to_string))
        .unwrap_or_default()
}

/// Cập nhật token/run map từ kết quả thật của store đang replay.
fn track_batch(state: &mut ReplayState, record: &BatchRecord) {
    if let Some(token) = &record.fencing_token {
        state.tokens.insert(record.job_id.clone(), token.clone());
    } else {
        state.tokens.remove(&record.job_id);
    }
    state
        .run_by_job
        .insert(record.job_id.clone(), record.run_id.clone());
    state.last_run = Some(record.run_id.clone());
}

fn enum_from_value<T, F>(value: Option<&str>, parse: F, what: &str) -> Result<T, ReplayError>
where
    F: FnOnce(&str) -> Option<T>,
{
    value
        .and_then(parse)
        .ok_or_else(|| ReplayError::msg(format!("{what} không hợp lệ: {value:?}")))
}

fn dispatch(
    journal: &Journal,
    op: &str,
    op_args: &serde_json::Map<String, Value>,
    state: &mut ReplayState,
    counters: &mut SkipCounters,
    args: &Args,
) -> Result<(), ReplayError> {
    match op {
        // ── ops ghi thật đã port (phase-05/07) ──────────────────────────
        "open_run" => {
            let metadata: RunMetadata =
                serde_json::from_value(op_args.get("metadata").cloned().unwrap_or(Value::Null))
                    .map_err(|e| ReplayError::msg(format!("open_run.metadata: {e}")))?;
            let record = journal
                .open_run(
                    &metadata,
                    arg_i64(op_args, "expected_items", 0),
                    arg_i64(op_args, "expected_bytes", 0),
                )
                .map_err(ReplayError::journal)?;
            state.last_run = Some(record.run_id);
            Ok(())
        }
        "create_artifact" => {
            let run_id = arg_str(op_args, "run_id")?;
            let rows: Vec<Value> = op_args
                .get("rows")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            journal
                .create_artifact(run_id, &rows)
                .map_err(ReplayError::journal)?;
            Ok(())
        }
        "enqueue_batch" => {
            let run_id = arg_str(op_args, "run_id")?;
            let mut spec: BatchSpec =
                serde_json::from_value(op_args.get("spec").cloned().unwrap_or(Value::Null))
                    .map_err(|e| ReplayError::msg(format!("enqueue_batch.spec: {e}")))?;
            // Manifest staging chưa ported — strip payload; side Python replay
            // cũng làm tương tự (xem docstring đầu file).
            spec.operation = BTreeMap::new();
            let record = journal
                .enqueue_batch(run_id, spec)
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "claim_job" => {
            let job_id = arg_str(op_args, "job_id")?;
            let lease = arg_i64(op_args, "lease_seconds", 300);
            let run = state
                .run_by_job
                .get(job_id)
                .cloned()
                .or_else(|| state.last_run.clone());
            let claimed = journal
                .claim_batch(run.as_deref(), lease)
                .map_err(ReplayError::journal)?;
            if let Some(record) = claimed {
                if record.job_id != job_id {
                    eprintln!(
                        "warn: capture claim_job({job_id}) nhưng replay claim được {} — thứ tự stream lệch",
                        record.job_id
                    );
                }
                track_batch(state, &record);
            }
            Ok(())
        }
        "claim_batch" => {
            // Low-level path (fixture scenario): claim theo run, thứ tự sequence.
            let run_id = op_args.get("run_id").and_then(Value::as_str);
            let lease = arg_i64(op_args, "lease_seconds", 60);
            let claimed = journal
                .claim_batch(run_id, lease)
                .map_err(ReplayError::journal)?;
            if let Some(record) = claimed {
                track_batch(state, &record);
            }
            Ok(())
        }
        "ack_batch" => {
            let job_id = arg_str(op_args, "job_id")?;
            let token = token_for(state, job_id, op_args.get("fencing_token").and_then(Value::as_str));
            let record = journal
                .ack_batch(job_id, &token, op_args.get("elapsed_ms").and_then(Value::as_i64))
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "renew_lease" => {
            let job_id = arg_str(op_args, "job_id")?;
            let token = token_for(state, job_id, op_args.get("fencing_token").and_then(Value::as_str));
            let record = journal
                .renew_lease(job_id, &token, arg_i64(op_args, "lease_seconds", 60))
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "mark_reconciling" => {
            let job_id = arg_str(op_args, "job_id")?;
            let token = token_for(state, job_id, op_args.get("fencing_token").and_then(Value::as_str));
            let error_code = enum_from_value(
                op_args.get("error_code").and_then(Value::as_str),
                TerminalErrorCode::from_value,
                "error_code",
            )?;
            let record = journal
                .mark_reconciling(job_id, &token, Some(error_code))
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "schedule_retry" => {
            let job_id = arg_str(op_args, "job_id")?;
            let token = token_for(state, job_id, op_args.get("fencing_token").and_then(Value::as_str));
            let retry_at_epoch = op_args
                .get("retry_at_epoch")
                .and_then(Value::as_f64)
                .ok_or_else(|| ReplayError::msg("schedule_retry thiếu retry_at_epoch"))?;
            let retry_class = enum_from_value(
                op_args.get("retry_class").and_then(Value::as_str),
                RetryClass::from_value,
                "retry_class",
            )?;
            let error_code = enum_from_value(
                op_args.get("error_code").and_then(Value::as_str),
                TerminalErrorCode::from_value,
                "error_code",
            )?;
            let record = journal
                .schedule_retry(job_id, &token, retry_at_epoch, retry_class, error_code)
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "block_batch" => {
            let job_id = arg_str(op_args, "job_id")?;
            let token = token_for(state, job_id, op_args.get("fencing_token").and_then(Value::as_str));
            let retry_class = enum_from_value(
                op_args.get("retry_class").and_then(Value::as_str),
                RetryClass::from_value,
                "retry_class",
            )?;
            let error_code = enum_from_value(
                op_args.get("error_code").and_then(Value::as_str),
                TerminalErrorCode::from_value,
                "error_code",
            )?;
            let record = journal
                .block_batch(job_id, &token, retry_class, error_code)
                .map_err(ReplayError::journal)?;
            track_batch(state, &record);
            Ok(())
        }
        "open_barrier" | "close_barrier" => {
            let run_id = arg_str(op_args, "run_id")?;
            let name = arg_str(op_args, "name")?;
            let outcome = if op == "open_barrier" {
                journal.open_barrier(run_id, name)
            } else {
                journal.close_barrier(run_id, name)
            };
            outcome.map_err(ReplayError::journal)?;
            Ok(())
        }
        "complete_producers" => {
            let run_id = arg_str(op_args, "run_id")?;
            journal
                .complete_producers(run_id)
                .map_err(ReplayError::journal)?;
            Ok(())
        }
        // ── ops chỉ-đọc / no-op trên store replay mới ───────────────────
        "get_run" | "list_runs" | "find_resumable_run" | "get_batch" | "get_barrier"
        | "list_open_producers" | "list_batches" | "status_counts" | "status_summary"
        | "list_events" | "inspect" | "conservation_summary" | "endpoint_audit_status"
        | "recover_expired_leases" | "recover_run_leases_as_ambiguous" | "close" => {
            counters.readonly += 1;
            Ok(())
        }
        // ── chưa port / ngoài scope replay ──────────────────────────────
        other => {
            if args.skip_unsupported {
                eprintln!("warn: bỏ qua op chưa hỗ trợ replay: {other}");
                counters.unsupported += 1;
                Ok(())
            } else {
                Err(ReplayError::msg(format!(
                    "op \"{other}\" chưa hỗ trợ replay — dùng --skip-unsupported để bỏ qua"
                )))
            }
        }
    }
}

/// So kết quả replay với error mà capture đã ghi — lệch code là format/parity drift.
fn verify_outcome(
    op: &str,
    outcome: Result<(), ReplayError>,
    expected_error: Option<&str>,
) -> Result<(), String> {
    match (outcome, expected_error) {
        (Ok(()), None) => Ok(()),
        (Err(failure), None) => Err(format!("op {op}: {}", failure.message)),
        (Ok(()), Some(code)) => Err(format!(
            "op {op}: capture ghi error `{code}` nhưng replay thành công"
        )),
        (Err(failure), Some(code)) => match failure.code.as_deref() {
            Some(actual) if actual == code => Ok(()), // lỗi đúng như capture
            Some(actual) => Err(format!(
                "op {op}: capture error `{code}` nhưng replay lỗi `{actual}` ({})",
                failure.message
            )),
            None => Err(format!(
                "op {op}: capture error `{code}` nhưng replay lỗi không có code: {}",
                failure.message
            )),
        },
    }
}
