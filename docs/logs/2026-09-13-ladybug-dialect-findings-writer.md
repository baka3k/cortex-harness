# LadybugDB 0.20.4 dialect findings khi port writer (probe Rust) — 2026-09-13

## Context

Phase 03 port writer plane sang Rust cần biết chính xác ladybug 0.20.4 xử lý
prepared params + map literals + `SET n += map` như thế nào qua Rust FFI
(crate `lbug`), vì writer Python vốn chạy FalkorDB-style queries
(`SET x += row`, list-of-dict params). Probe bằng example Rust chạy trực tiếp
trên store thật (đã xoá probe sau khi chốt).

## Change

Các finding (verify trên ladybug 0.20.4, macOS arm64):

1. **Prepared param LIST<STRUCT> bị binder từ chối** — "row has data type
   STRUCT(...)[] but (NODE,REL,STRUCT,ANY) was expected". Python driver chạy
   được vì backend pybind/C-API của wheel khác đường FFI Rust.
   → `LadybugStore` render **toàn bộ params thành Cypher literals inline**
   (`src/store/ladybug_store.rs::render_params`).
2. **Map literal = STRUCT, access key thiếu là binder error** ("Invalid struct
   field name") và `{}` không parse được → rows render **uniform-key** (union
   key của batch + mọi `row.<field>` mà query tham chiếu, key thiếu → NULL)
   (`SchemaNode::absorb/render`).
3. **NULL bare trong STRUCT literal type thành STRING** → `coalesce(row.exported,
   false)` fail kiểu. Fix: `CAST(NULL AS T)` với T infer từ default literal của
   `coalesce(row.<f>, default)` trong query (`coalesce_type_hints`).
4. **`SET n += <map>` / `SET r += <map>` không parse được** — typed relations,
   evidence sites, topology `SET x += row` đều fail trên ladybug **cả phía
   Python** (driver không rewrite) → các path này là falkordb-only trên cả 2
   implementation hiện tại.
5. **Zero-arg `datetime()`**: "function DATETIME does not exist" → rewrite
   `timestamp('<iso>')` (khớp `rewrite_datetime_call` Python, nhưng inline).
6. **FOREACH-based cleanup** (`WITH collect(n) AS nodes FOREACH(... DETACH
   DELETE ...)`) parse-fail trên ladybug — cleanup_paths/cleanup_project và
   incremental cleanup là falkordb-only.
7. **Auto-DDL works**: `Cannot find property X for Y` / `Table X does not
   exist` / `Cannot bind X as a relationship pattern label` classify đúng như
   Python; column type infer từ literal đã render (INT64/DOUBLE/BOOL/STRING/
   STRING[]/TIMESTAMP).

## Impact

- `LadybugStore` không dùng prepared statement — inline literals với escaping
  đầy đủ (`quote_cypher`) + single-pass token replacement (tránh double-render
  khi data chứa text `$param`). Risk: **med** (render inline là bề mặt mới) —
  đã có 6 integration test + parity harness che.
- Mọi ngôn ngữ/phase sau cần biết: writer trên ladybug local chỉ cover node
  upserts + calls + những query không `+=`/FOREACH; parity gate phase này chạy
  falkordb local.
- `ensure_schema` của LadybugStore = bootstrap CODE_GRAPH_SCHEMA (preflight
  index-poll không chạy được trên static schema); ART/FTS index create
  soft-skip khi column chưa có trong table (store mới) — khác Python (raise),
  chủ đích để local provider dùng được.

## Decision

Inline literals thay vì попытки fix param binding FFI: (a) binder từ chối ở
cấp engine không phải crate; (b) semantics `row.missing → null` tái tạo được
chính xác qua uniform-key + CAST hints; (c) giữ 1 code path duy nhất trong
store, writer phía trên không đổi. Đã cân nhắc rewrite `+=` → per-property
SET phía store nhưng từ chối: mỗi query có context map khác nhau
(row.props / row.properties / row), làm store phình to và dễ drift parity so với Python.

## References

- code: `rust/crates/cortex-graph-writer/src/store/ladybug_store.rs`
- log: `./docs/logs/2026-09-13-phase03-graph-writer-port.md`
- plan: `./plans/260913-2130-rust-full-migration/phase-03.md`
- commit: 9a395db
