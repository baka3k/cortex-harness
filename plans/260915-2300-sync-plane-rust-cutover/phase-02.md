# Phase-02 — Embedded target resolution + polymorphic store + hatch — rev2

Mục tiêu: chết trigger delegation thứ nhất (graphops.rs:85-88 ladybug, :104-107 embedded falkordb).

## Changes

1. **Ladybug branch** (graphops.rs:85-88):
   - Path precedence: `--ladybug-path` > `LADYBUG_PATH` env > `cortex_storage::resolve_storage` (anchor
     theo phase-01.4; deliberate deviation ghi doc-comment).
   - **Graph name precedence — H1, mirror cli.py:249-256 CHÍNH XÁC**: `--ladybug-graph` arg >
     **`project_id`** > `LADYBUG_GRAPH` env > `"hyper_graph"`. **Registry KHÔNG tham dự ladybug_graph**
     (registry chỉ fill falkordb_graph/qdrant_collection — cli.py:137-191). Không được thêm bước registry
     như rev1.
   - `GraphContext` mở rộng: `provider=ladybug` + `ladybug_path` + resolved graph name; bỏ Err path.
   - Port `apply_project_registry_defaults` thật (thay stub warn graphops.rs:48-60) cho
     falkordb_graph/qdrant_collection như Python.
2. **`open_store` provider-polymorphic** (graphops.rs:130-144): dùng sẵn
   `cortex_graph_writer::store::open_store_from_env() -> Box<dyn GraphStore>` (red-team verified-sound:
   đã có ladybug branch + `LadybugStore::open(path, graph)`); `GraphContext` tham dự selection. Đổi cả
   call sites: main flow, **message-scan lane provider gate `orchestrator.rs:3064-3069` (M1 — rev1 bỏ
   sót: gate `provider == "falkordb"` đang skip graph-half cho provider khác)**, ensure_schema/preflight.
3. **Embedded falkordb theo spike phase-01.5 (H2)**:
   - Spike WIN: wire embedded context qua `falkor_boot` reuse (spawn/readiness/shutdown sạch) →
     `FalkorDbStore`; default `dev init` giữ nguyên falkordb (init.rs:230 không đổi); data user giữ nguyên.
   - Spike FAIL: fail-closed với error **trung thực**: "set `FALKORDB_URI` (remote, giữ data) hoặc
     `GRAPH_PROVIDER=ladybug` (store mới, graph được rebuild bằng full re-sync — không có data migration
     falkordb→ladybug)". Không delegate. Ghi decision + lý do vào report.
4. **Backend stamp (L2)**: `summary["backend"] = "rust-native"`; delegation path stamp
   `summary["backend"] = "python"` (trước khi exec) — dogfood/drill verify đọc field này thay vì đoán.
5. **Rollback hatch** `CORTEX_SYNC_BACKEND=python`: check sớm `run_incremental` (orchestrator.rs:250)
   → `delegate_to_python("explicit CORTEX_SYNC_BACKEND=python")`. Runbook §flags (chỉ đụng runbook sau
   khi vector-lane edits đã commit — M3a).

## Gates

- Scratch ladybug sync Rust-only: 0 dòng `python-plane delegation`; exit 0; summary khớp golden
  (masked, **graph-name KHÔNG mask — assert đúng project_id-derived graph**); `summary.backend == "rust-native"`.
- Message-scan trên ladybug leg: `native_message_scan.total_graph_upserted > 0` (M1).
- Embedded falkordb theo nhánh Q2: WIN → sync embedded PASS Rust-only; FAIL → error actionable, exit ≠ 0,
  không delegate, message nêu đủ 2 lựa chọn + rebuild caveat.
- `CORTEX_SYNC_BACKEND=python` → delegation fires + `summary.backend == "python"` (hatch regression guard).
- `cargo test -p cortex-sync` + `cargo clippy -D warnings` xanh; parity suite hiện có không red.
