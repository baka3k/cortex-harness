# Phase-03 report — Journal replay driver (Rust) + lane policy (2026-09-16)

Closes trigger delegation #2 (required journal lane). Replay = **legacy-drain-only** (phase-01 M2
frozen: Rust children không enqueue batch).

## Changes

1. **`cortex-sync/src/journal_replay.rs` (mới, lib)** — port `consumer.py`:
   - `ReplayConfig` + `replay_config_from_env()` — consumer `_main` env contract
     (MODE/PATH/METADATA; normalize qua journalenv; Err fail-closed khi mode enabled thiếu path/metadata).
   - `GraphWriteOperation` descriptor port (`operation.py`): from_dict + operation_key verify.
   - Trusted compilers (`executor.py` + `query_contract.py` + `reconcile.py`): node_identity /
     typed_relationship / evidence_edge / repository_file / call_edge / call_site / possible_call_site /
     file_cleanup v2 / orphan_unknown_cleanup v2 + readback + endpoint-audit compile + grouping
     (typed relations & evidence edges, schema identity-index validation).
   - Receipt transform (`guard.py::_receipt_query` port): `RETURN count(x) AS count` → WITH-count +
     `MERGE (receipt:GraphWriteReceipt …)` — reconcile readback đếm receipt; `_journal_operation_key`
     fence check parity.
   - Drain loop (`drain`): seal-endpoint-audit-if-ready → claim_reconciling → reconcile_one |
     claim_batch → load (artifact JSONL + sha256 + count) → execute (receipt-transformed) →
     ack / block INTEGRITY / mark_reconciling / schedule_retry (AMBIGUOUS/TRANSIENT).
   - `resume_journal`: preflight `config.required` + `get_run` + **schema fingerprint fail-closed**
     (`_ensure_recovery_schema` parity) + `recover_run_leases_as_ambiguous`.
   - Store target từ GraphContext (phase-02) — **ladybug replay chạy native** (deliberate fix;
     consumer.py `_main` không có branch ladybug — phase-01.2 probe).
   - `drain_scope_sweep`: parent-level required mode không có env per-child → drain mọi journal
     resumable (runs Open/Draining) trong `<cache>/graph-write-journal/<scope_id>/`.
   - `recover_only_main()`: entry cho `cortex-sync --journal-recover-only` (exit 0/70 parity `_main`).
2. **Orchestrator wiring** (:1208+): xoá **empty-mode-cplus required branch (M2)**; `REQUIRED_MODES`
   explicit → native replay (env-config drain, fallback scope sweep) thay `Err(DELEGATE_SENTINEL…)`;
   summary `journal = {mode, backend:"rust-native", recovered_batches, path|sweep}` (L2 stamp).
3. **Lane policy** (`journalenv.rs`): `journal_mode_for_lane` cplus default `shared-required` → `off`;
   3 lane call sites (primary/framework/topology) skip journal-env cho lane `off` → children
   direct-write như 37 parser. Non-cplus default `shared-shadow` KHÔNG đổi.
4. **journalx.rs (`cortex-dev`)**: `recover_required_lane` bỏ python spawn + PYTHONPATH prepend
   (diệt bug join `":"`) → exec `cortex-sync --journal-recover-only` (cùng lib với orchestrator).
5. **`cortex-graph-core`**: thêm `Journal::list_batches(run_id)` (consumer audit cần; mapper tái dùng).
6. **Parity fix (lộ qua required-mode smoke)**: `graphops::graph_target_cli_args` giờ truyền
   `--ladybug-path/--ladybug-graph` cho children như incremental_sync.py:1434-1440 (trước đây chỉ có
   falkordb-graph → children ladybug die khi gọi trực tiếp không có env kế thừa).

## Gates — kết quả

- ✅ Replay qua fixture thật (`tests/fixtures/sync-plane-golden/journal/` — sqlite + 22 artifact files,
  fingerprint `8613fc08894a26c2` == canonical):
  - **Byte-compat identity**: `run_id(metadata_json)` tính lại == stored run_id (unit test).
  - **Fail-closed parity**: fixture nguyên trạng (producers open, manifest unconserved) → drain refuse
    tại endpoint-audit boundary — `invalid_contract` "missing or ambiguous endpoints" trên scratch
    store rỗng (khớp thứ tự check của python consumer: audit chạy trước seal's conservation gate).
  - **Positive end-to-end**: synthetic journal dựng bằng producer primitives (open_run → create_artifact
    → enqueue_batch ×2) drain sạch trên scratch ladybug store: **2/2 batch acked, receipt nodes = 2,
    double-drain no-op (journal + store bất biến), unknown-identity → 0**.
    (`cargo test -p cortex-sync --test journal_replay_fixture`)
- ✅ Scratch `CORTEX_GRAPH_JOURNAL_MODE=required` (ladybug): **exit 0, 0 delegation, success/scanned,
  sweep chạy native** (`journal.backend = "rust-native"`).
- ✅ Regression: default run (không env) healthy như phase-02; `--journal-recover-only` mode-required
  thiếu metadata → exit 70 với message parity; mode off → exit 0.
- ✅ `cargo test -p cortex-sync -p cortex-graph-core`: **104 passed / 0 failed**; clippy `-D warnings`
  xanh (cortex-sync / cortex-graph-core / cortex-dev).

## Không làm / deliberate

- Không wire journal producer vào Rust language_writer (durability qua mark-dirty; replay =
  legacy-drain-only). `INCOMPATIBLE_SCHEMA` qua `resume_journal` thực tế unreachable từ metadata
  thay đổi (fingerprint thuộc run identity → identity moves → get_run None → 0, parity python);
  preflight vẫn đứng cho đường metadata-tại-chỗ.
- Fixture prep trong test (flip run status/barrier/producer bookkeeping) là mô phỏng resume hợp lệ —
  python consumer trên fixture nguyên trạng cũng refuse (byte-compat hành vi, không mở khoá vi phạm).
