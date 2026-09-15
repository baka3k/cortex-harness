# Phase-06 — Marker swap + DELETE sync closure + docs + cross-plan — rev2 (C1 re-scope)

Mục tiêu: xoá `incremental_sync.py` + **true sync closure** sau khi dogfood sign-off. 1 commit xoá
(revert-able), marker swap CÙNG commit, tag trước commit.

## Precondition gates (H3, M3b — mới rev2)

- **Dogfood sign-off**: umbrella §2 (≥7 ngày stock) HOẶC waiver user ghi trong
  `reports/phase05-drill.md`. Không sign-off → phase-06 KHÔNG chạy.
- **Windows plan coordination**: `plans/260821-2115-dev-sync-code-windows` đã đóng hoặc re-scope
  (targets của nó đè deletion manifest — phase-01.6 kết luận).
- Tag `pre-syncplane-delete` trước DELETE commit (L4 — forensics/revert anchor, mirror
  `pre-phase08-cutover`).

## Changes

1. **Marker swap (same commit)**: `registry.rs:13-26` marker `code-tiny/tools/sync/incremental_sync.py`
   → `cortex_harness/dev.py` (sentinel A6 sống vĩnh viễn; `cortex-dev/src/util.rs` đã dùng dev.py —
   nhất quán). Unit test repo-root detection không set `CORTEX_REPO_ROOT`, binary chạy từ sub-dir.
2. **DELETE — true closure (C1)**:
   - `code-tiny/tools/sync/` toàn bộ (5 .py + __pycache__)
   - `tests/test_incremental_sync_*.py` theo **glob** (L1 — 15 file, không hardcode count) +
     `tests/test_sync_processes.py`; port assertion gap sang Rust integration test
   - Rust: `delegate_to_python` + hatch check + `DELEGATE_SENTINEL` const; `journalx.rs` python spawn
     + PYTHONPATH block; `procinfo.rs:109` script list entry
   - `cortex_harness/dev.py` dead sync-command bodies :3400-3560 (phase-01.3 đã xác nhận dead; dev.py
     file giữ nguyên làm sentinel)
   - **KHÔNG xoá**: `tools/graph/**` (drivers/cli/core/schema/operations/writer/journal) — live
     importers doc-tiny + MCP python rollback (C1); `tools/common/incremental_sync_state.py`;
     `cortex_harness/storage/**`
3. **Retire flag**: `CORTEX_SYNC_BACKEND` → retired-error (loud, mirror phase-08 runbook §5).
4. **Docs**: runbook chốt end-state; ReadMe/INSTALLER_GUIDE sync-plane Rust-only; wiki DOC-STALE note
   (tổng thể wiki thuộc python-legacy-cleanup phase-05).
5. **Cross-plan**: python-legacy-cleanup plan.md + disposition.md (A1 → dead-by-this-plan; nhánh B
   superseded; `tools/graph` di sản + dev.py sync refs → disposition mới cho phase-02 của nó);
   rust-full-migration plan.md (wave sync-plane closed).

## Gates

- **MCP python backend still boots**: `CORTEX_MCP_BACKEND=python dev mcp start` (scratch instance) —
  import chain qua `tools.graph.core.provider_contract/shared_runtime` còn nguyên (C1).
- **`dev sync doc` smoke**: doc lane chạy được (import `falkordb_driver` từ doc-tiny/graph_store.py
  còn nguyên — C1).
- Grep gates với **allowlist đếm đủ (M4)**: `grep -rn "incremental_sync" --include="*.py"
  --include="*.rs"` → chỉ còn: `plans/**`, `docs/logs/**`, `scripts/rust_parity/**` (golden refs),
  `cortex_harness/dev.py` (parity reference + summary paths), `cortex_harness/sync_processes.py:77,84`,
  `code-tiny/tools/common/incremental_sync_state.py`, `code-tiny/tools/common/scan_ignore.py:190`,
  wiki DOC-STALE note. `grep -rn "delegate_to_python\|DELEGATE_SENTINEL" rust/crates/` → 0 hit.
- `cargo test` workspace + `cargo clippy -D warnings` xanh.
- Smoke sau xoá: scratch ladybug sync PASS; repo-root detect đúng không set `CORTEX_REPO_ROOT`.
- `git status tests/fixtures/` sạch.
