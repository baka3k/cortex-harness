# DOC-STALE — sync-plane (2026-09-16)

Tài liệu wiki mô tả sync plane Python (`incremental_sync.py`, `tools/sync/**`,
delegate/pipeline python) đã **STALE** kể từ sync-plane Rust cutover
(`plans/260915-2300-sync-plane-rust-cutover`, tag `pre-syncplane-delete`):

- `dev sync code/doc` chạy orchestrator Rust (`cortex-sync`) — python plane đã xoá.
- `CORTEX_SYNC_BACKEND` retired-error.
- Embedded FalkorDB fail-closed: `FALKORDB_URI` (giữ data) hoặc `GRAPH_PROVIDER=ladybug`
  (rebuild bằng full re-sync — không có data migration).
- Journal required lane = native replay (legacy-drain-only).
- Tổng thể wiki thuộc python-legacy-cleanup phase-05 (rewrite hàng loạt).
