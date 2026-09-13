# Phase 02: Journal hoàn tất — manifest staging + conservation + endpoint audit

## Scope (phần còn lại của `sqlite_store.py` chưa port ở phase-07)

- `_manifest_candidates` (~150 LOC): suy ra node/edge canonical identities từ artifact rows
  theo `reconciliation` mode (`node_identity`, `typed_relationship`, `evidence_edge`,
  `repository_file`, `call_edge`/`call_site`/`possible_call_site`, `file_cleanup`,
  `orphan_unknown_cleanup`); xử lý `Type` external dedupe theo `file_path` provenance;
  `_typed_identity` (scalar/bool/invalid rules).
- `_stage_manifests_locked` (~143 LOC): upsert producer_completion, per-candidate
  STAGED_UNIQUE/DECLARED_DUPLICATE/CONFLICT/REJECTED dispositions, endpoint pairs, counters.
- Bỏ khoá "operation rỗng" trong `enqueue_batch` (phase-07) khi staging port xong.
- `conservation_summary` (~110 LOC), `seal_endpoint_audit` + `_validate_endpoint_audit_boundary_locked`.
- `claim_reconciling`, `claim_reconciling_job`, `schedule_reconciliation_retry`,
  `recover_run_leases_as_ambiguous`, `quarantine_legacy_targets`, `mark_reconciling` completion.

## Parity

- Mở rộng `gen_journal_scenario.py` với **kịch bản manifest đầy đủ**: batch có `operation`
  (node_identity + call_edge), duplicate rows, conflict rows, rejected rows → conservation
  summary + endpoint audit seal. Python ghi kịch bản → Rust replay so từng op (mask token).
- Unit: mỗi reconciliation mode 1 case, gồm invalid identity → REJECTED.

## Gate

- [x] Scenario manifest replay pass (từng op + conservation numbers exact).
- [ ] Python sync thật chạy với `CORTEX_JOURNAL_BACKEND=rust` (env flag mới) trên stock
      mà journal state không khác lần chạy Python (so inspect + events).
      **Pending** — cần cầu PyO3 cho `Journal` (mẫu `cortex-retrieval-py`) + adapter
      Python trong `GraphWriteJournalRuntime` trước khi flag chạy được; là việc kế tiếp
      của phase này trước Phase 09 (orchestrator sync Rust không cần bridge).
- [x] clippy `-D warnings` sạch.

**Trạng thái 2026-09-13:** port xong toàn bộ logic — `journal_manifest.rs`
(`_typed_identity`/`_manifest_candidates`/`_stage_manifests_locked`), wire vào
`Journal::enqueue_batch` (bỏ chặn operation rỗng), `conservation_summary`,
`seal_endpoint_audit` + boundary validation, `claim_reconciling(_job)`,
`schedule_reconciliation_retry`, `recover_run_leases_as_ambiguous`,
`quarantine_legacy_targets`. Kịch bản fixture mở rộng **45 ops** (run dirty:
dup+conflict+rejected → batch/run BLOCKED; run clean: node dups + call_edge dups →
mark_reconciling → schedule_reconciliation_retry → claim_reconciling → ack →
complete_producers → seal → immutable-check). 11 unit test per-mode trong
`tests/journal_manifest_unit.rs`. Lưu ý parity: `inspect_journal` Python dùng clock
thật (không nhận clock param) → `oldest_unfinished_age_seconds` được mask trong replay.
