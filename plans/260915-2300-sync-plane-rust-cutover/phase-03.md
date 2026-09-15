# Phase-03 — Journal replay driver (Rust) + lane policy — rev2

Mục tiêu: chết trigger delegation thứ hai (orchestrator.rs:1208-1228). Replay driver = port
`consumer.py::GraphWriteJournalConsumer.drain` trên `cortex_graph_core::journal`.

## Changes

1. **`cortex-sync/src/journal_replay.rs` (mới)** — extract thành lib function dùng chung
   (cortex-dev gọi lại, không 2 implementation):
   - Config env như consumer `_main` (consumer.py:349-374); store target từ GraphContext phase-02
     (sửa bug Python: fallback FALKORDB cho mọi non-neo4j, không có ladybug branch — phase-01.2 probe
     chốt hành vi thật; nếu ladybug required vốn không chạy được ở Python leg → document deliberate fix).
   - Preflight fail-closed: `config.required` + `schema_fingerprint == CODE_GRAPH_SCHEMA.fingerprint`
     (consumer.py:299-329).
   - Order: node batches (NODE_PHASE_BARRIER) → endpoint readback audit (ENDPOINT_AUDIT_BARRIER) → edge
     batches (:250-297); artifact JSONL + count/sha256 check (:68-95); retry/reconcile classes
     (claim_reconciling/schedule_retry/block_batch — journal.rs:1953-2166 có sẵn).
   - Exit: 0 clean, 70 failure (parity `_main`).
2. **Wire orchestrator**: thay `Err(DELEGATE_SENTINEL…)` :1222-1280 bằng native replay sau khi store
   sẵn sàng (ladybug hỗ trợ — cùng `Box<dyn GraphStore>`).
   **Xoá cả empty-mode-cplus required branch :1214-1221 (M2)** — cplus về direct-write giống 37 parser;
   giữ `CORTEX_GRAPH_JOURNAL_MODE=required/shared-required` = hợp lệ, chạy native replay (drain legacy).
   `journal_mode_for_lane` (journalenv.rs:49-61) default cplus `shared-required` → `off` đồng bộ.
3. **journalx.rs**: `recover_required_lane` (journalx.rs:306-343) pre-run → gọi lib replay (bỏ python
   spawn + PYTHONPATH prepend :315-332 — diệt luôn bug join `":"` separator, red-team verified-sound
   note); read/purge native :175-294 giữ nguyên.
4. **Backend stamp (L2)**: replay leg ghi `journal` section + `backend` stamp nhất quán.

## Gates

- Replay byte-compat trên journal fixture phase-01.2: conservation_summary (journal.rs:2503) trước/sau
  khớp; barrier order đúng; exit codes parity.
- Double-drain: lần 2 no-op (resumable-run), không double-write (đếm node trước/sau store).
- Scratch `CORTEX_GRAPH_JOURNAL_MODE=required` (ladybug): PASS Rust-only, không delegation.
- Regression: non-cplus parsers default mode — hành vi không đổi so phase-02; cplus = direct-write
  (assert summary không còn `journal` delegate note; đây là thay đổi có chủ đích, document runbook).
- `cargo test -p cortex-sync -p cortex-graph-core` xanh; unit tests retry/reconcile (mock Journal +
  fail-injection).

## Không làm

- Không wire journal producer vào Rust language_writer (durability qua mark-dirty; phase-01.2 đã xác
  nhận Rust children không enqueue — replay = legacy-drain contract).
