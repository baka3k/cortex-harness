# Phase 02 — Journal manifest staging hoàn tất (Rust) — 2026-09-13

## Context

Phase 02 của `plans/260913-2130-rust-full-migration/plan.md`: port phần còn lại
của `code-tiny/tools/graph/journal/sqlite_store.py` mà phase-07 cũ chưa làm —
manifest staging, conservation, endpoint audit, reconciling. Trước change này,
Rust journal từ chối rõ ràng mọi `BatchSpec` có `operation` khác rỗng.

## Change

- `rust/crates/cortex-graph-core/src/journal_manifest.rs` (mới): port
  `_typed_identity` + `_manifest_candidates` (6 reconciliation mode:
  node_identity/typed_relationship/evidence_edge/repository_file/call_edge/
  call_site/possible_call_site + skip cho file_cleanup/orphan_unknown_cleanup,
  Type-external dedupe theo semantic row bỏ `file_path`) và
  `_stage_manifests_locked` (dedupe STAGED_UNIQUE/DECLARED_DUPLICATE/CONFLICT,
  endpoints, counters `producer_completion`).
- `rust/crates/cortex-graph-core/src/journal.rs`: wire staging vào
  `enqueue_batch` (bỏ chặn operation rỗng; manifest failure → batch BLOCKED +
  run BLOCKED sau commit, event `batch_manifest_rejected`); thêm
  `conservation_summary` (digest canonical exact với Python),
  `seal_endpoint_audit` + boundary validation (immutable),
  `claim_reconciling(_job)`, `schedule_reconciliation_retry`,
  `recover_run_leases_as_ambiguous`, `quarantine_legacy_targets`.
- Scenario fixture mở rộng 21 → **45 ops**
  (`scripts/rust_parity/gen_journal_scenario.py`): run dirty (dup + conflict +
  rejected trong 1 batch → BLOCKED), run clean (node dups + call_edge dups →
  mark_reconciling → schedule_reconciliation_retry → claim_reconciling → ack →
  complete_producers → seal → immutable-check). Rust replay khớp Python từng op.
- 12 unit test per reconciliation mode trong
  `rust/crates/cortex-graph-core/tests/journal_manifest_unit.rs` (gồm test
  quarantine 2 run với physical_target cũ/mới).

## Impact

- Rust journal giờ phủ đủ chu kỳ production/consumer + manifest conservation —
  điều kiện để Phase 03 (graph writer) và Phase 09 (orchestrator) dùng journal
  Rust thật thay vì chỉ low-level path. Risk: low (parity replay pass, clippy
  `-D warnings` sạch toàn workspace).
- Gate còn mở: dogfood `CORTEX_JOURNAL_BACKEND=rust` trên stock — cần cầu PyO3
  cho `Journal` (mẫu `cortex-retrieval-py`) + adapter trong
  `GraphWriteJournalRuntime`; đã ghi pending trong phase-02.md. Không chặn
  Phase 03.
- Parity note: `inspect_journal` phía Python dùng clock thật (không nhận clock
  param) nên `oldest_unfinished_age_seconds` bị mask trong replay — field này
  vốn wall-clock, không phải parity target.

## Decision

- Đặt staging logic trong module riêng `journal_manifest.rs` thay vì phình
  `journal.rs` (đã 2.2k+ dòng): giữ ranh giới review được.
- Node REJECTED vẫn giữ `scope` — sửa chính xác thứ tự Python (`candidate.update
  (scope=...)` chạy **trước** try-identity); lần đọc đầu sai làm test bắt được
  (`NOT NULL constraint failed: node_manifest.scope`).
- `JournalError` Rust không có map `details` như Python — mã lỗi (thứ harness
  so) giữ nguyên, chi tiết nhét vào message; chấp nhận vì không có consumer
  nào đọc `details` máy được.

## References

- plan: ./plans/260913-2130-rust-full-migration/phase-02.md
- commit: aeef97e
- log liên quan: ./docs/logs/2026-09-13-phase01-falkordb-rust-client-go.md
