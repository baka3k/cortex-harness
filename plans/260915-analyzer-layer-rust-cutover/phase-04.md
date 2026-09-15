# Phase 04 — Port analyzer-topology (project_topology)

## Mục tiêu

Binary `analyzer-topology` thay `code-tiny/tools/project_topology/topology_analyzer.py` (1,835 LOC). Nửa writer đã port sẵn (`cortex-graph-writer/src/topology.rs`, export `cortex-graph-writer/src/lib.rs:10-11,29,34`) — phase này port nửa analyzer (đọc graph → tính topology → gọi writer).

## Scope code

| Thành phần | Ghi chú |
|---|---|
| Crate [[bin]] `analyzer-topology` (mặc định crate riêng theo pattern analyzer-*; quyết định khi implement) | Tái dùng `cortex-graph-writer::topology` cho write path |
| Graph read path | Query graph qua `cortex-graph-core`/`cortex-graph-driver` trait (đã chứng minh ở retrieval/MCP crates) — không thêm client riêng |
| `rust/crates/cortex-sync/src/registry.rs` | `project_topology_analyzer()` đi qua flip matrix (hook từ phase-01) |
| Embedding/input artifact | Không liên quan vector — nhưng dùng chung artifact conventions (0600, versioned) |

## Parity gate

1. `scripts/rust_parity/analyzer_parity_topology.py`: dual-run trên stock (topology chạy cuối sync, cần graph đầy đủ); graph diff 0 ngoài mask cho topology nodes/rels; `[SCAN_RESULT]` byte-identical.
2. Incremental leg: re-run sau thay đổi nhỏ — counts khớp.
3. **Orchestrator leg riêng** (red-team C6): topology qua `sync_orchestrator_parity` opt-in rust trước merge.

## Exit criteria

- Parity PASS, reports `phase04-topology-parity.md` + `phase04-orchestrator-leg.md`.
- Sync smoke opt-in: topology child là binary Rust.
- Sau phase này: opt-in `=rust` cho phép spawn path 100% binary (trừ embedding + message lanes còn pin).
