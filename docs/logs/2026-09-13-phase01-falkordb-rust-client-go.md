# Phase 01 spike GO — FalkorDB Rust client — 2026-09-13

## Context

Phase 01 của `plans/260913-2130-rust-full-migration/plan.md` là gate go/no-go cho
toàn bộ graph path Rust của chương trình full migration (~160k LOC Python → Rust,
14 phases). Nếu Rust không nói chuyện được với graph server qua Redis protocol thì
Phase 03–08 phải đổi hướng sang PyO3 bridge.

## Change

- Crate mới `rust/crates/cortex-falkordb/` (redis-rs 1.7, RESP2):
  - `src/value.rs` — parser result-set compact: value = `[type, payload]` theo
    bảng `ResultSetScalarTypes` của falkordb-py (1=NULL, 2=STRING, 3=INTEGER,
    4=BOOLEAN payload-chuỗi, 5=DOUBLE payload-chuỗi, 6=ARRAY, 7=EDGE, 8=NODE,
    9=PATH, 10=MAP); node `[id,[label_ids],[prop_triples]]`, edge
    `[id,rel_type_id,src,dest,props]`, property `[prop_id,type,value]`.
  - `src/schema.rs` + `src/client.rs` — cache bảng tên label/rel-type/property
    (fetch qua `DB.LABELS`/`DB.RELATIONSHIPTYPES`/`db.propertyKeys`), refresh khi
    gặp id lạ; params header `` CYPHER `k`=v `` khớp `stringify_param_value`.
  - `src/normalize.rs` — JSON khớp byte-level `_normalize_falkordb_value`
    (`_graph_id`, `_label` sorted-first, `_type`/`_start_id`/`_end_id`).
- Harness `scripts/rust_parity/falkordb_spike_parity.py` +
  `gen_falkordb_spike_fixtures.py` (fixture tự sinh lại mỗi lần chạy vì bảng tên
  schema của graph là append-only).

## Impact

- **VERDICT: GO** — Phase 03 dựng trait `GraphStore` với backend
  `FalkorDbRemote` + `Ladybug` như kế hoạch, không cần PyO3 bridge cho graph path.
- Read parity 11/11 query trên `stock` thật (2714 nodes); write MERGE
  cross-verify 2 chiều (graph sạch sau cleanup); latency Rust 1.848 ms/query vs
  Python 1.981 ms (~7% nhanh hơn) trên cùng tunnel. Risk: low.
- Phát hiện ghi lại cho chương trình: lệnh read thật là `GRAPH.RO_QUERY` +
  `--compact` (**`GRAPH.ROQUERY` như ghi trong phase-01.md không tồn tại**);
  server là Redis 8.6.3 query engine (không phải FalkorDB module) — legacy
  fulltext procedure trả result set rỗng không lỗi; `DB.PROPERTYKEYS` yield
  `propertyKey` chứ không phải `key`.

## Decision

- Dùng `GRAPH.RO_QUERY`/`GRAPH.QUERY` + `--compact` thay vì `GRAPH.ROQUERY`:
  bám sát wire contract thật của falkordb-py mà driver Python đang dùng, đã xác
  nhận trực tiếp trên server (`MODULE LIST`, thử lệnh).
- Refresh toàn bộ schema sau mỗi lệnh ghi: an toàn cho spike nhưng là nợ perf
  cho writer Phase 03 — đã ghi review note trong decision record (refresh theo
  yêu cầu hoặc TTL).
- Write-test trên `stock` dùng probe node rồi DETACH DELETE: chứng minh write
  path thật (Python đọc lại được) mà không để dữ liệu residual; label
  `SpikeProbe` vẫn nằm trong bảng tên schema (append-only) — chấp nhận được trên
  graph dev, đã ghi trong decision record.

## References

- plan: ./plans/260913-2130-rust-full-migration/phase-01.md
- decision record: ./plans/260913-2130-rust-full-migration/reports/phase01-decision.md
- commit: aeef97e
