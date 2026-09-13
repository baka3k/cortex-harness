# Phase 03: Graph writer + operations port (Wave W0 đóng)

## Scope

- `tools/graph/writer/language_writer.py` (3.0k): node/rel upsert pipelines, batch write
  Contract, query_contract (319) — replicate `SET n = map` rewrite rules và dialect notes
  của từng provider (falkordb/ladybug) đã ghi trong `ladybug_driver.py`.
- `tools/graph/writer/project_topology_writer.py` (523) + `tools/project_topology/` (3.3k).
- `tools/graph/operations/` (class_ops, infra_ops, ... ~1.7k).
- `tools/graph/schema/preflight.py` + bootstrap/auto-DDL (đối chiếu ladybug plan phase-04).
- Driver abstraction: trait `GraphStore` với 2 backend — `FalkorDbRemote` (Phase 01) và
  `Ladybug` (crate `lbug`); provider selection theo env như `GraphDriverFactory`.

## Parity (mẫu chuẩn cho mọi phase sau)

**Dual-write graph diff**: script chạy writer Python và writer Rust trên 2 graph riêng
(`stock_pw` / `stock_rw`) với cùng input rows (lấy từ sync thật của stock), sau đó dump
cả 2 graph (nodes + rels + properties) và so exact — khác biệt duy nhất cho phép:
`updated_at`/timestamp fields (khai báo trong mask list).

## Gate

- [x] Dual-write diff = rỗng (ngoài mask) trên ≥2 bộ input thật (stock files subset + testdata).
- [x] Auto-DDL: property lạ được ALTER đúng như Python (verify bằng introspection 2 graph).
- [x] clippy sạch; benchmark write 1k nodes Rust vs Python (ghi số liệu, không đặt gate tuyệt đối).

**Trạng thái 2026-09-13:** crate `rust/crates/cortex-graph-writer` port xong toàn bộ
scope: `query_contract` (byte-exact, có golden test so từng chữ), `language_writer`
(50+ upsert methods, write_batches với state resume opt-in, typed relations +
endpoint audit fail-closed + `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS`,
build configurations, semantic coverage, proc joins, write_all orchestration với
label inference), `operations` (9 module: package/class/namespace/type/function/
infra/document/flow/cross_edge + batch_write_nodes/edges dialect ladybug),
`topology` (ProjectTopologyWriter + stable_fact_id + canonical-json `_graph_row`),
`preflight` (inspect → create missing required → poll ONLINE), store trait
`GraphStore` + 2 backend:
- **FalkorDbStore** — param header falkordb-py (thêm `Param::Map` stringify
  ``{`k`:v}`` + float format `str(2.0)="2.0"`), datetime rewrite `$__falkordb_now`,
  create/inspect indexes (range/fulltext, 1 record/property), fix client nhận
  reply 1-section (query không RETURN) và tolerate graph key chưa tồn tại lúc
  pre-load schema (khớp hành vi falkordb-py: query đầu tự tạo key).
- **LadybugStore** — bootstrap CODE_GRAPH_SCHEMA (node tables base columns +
  rel tables), auto-DDL fail-closed (`Cannot find property X for Y` → ALTER ADD
  với type infer từ literal render; `Table does not exist` / `Cannot bind ... as
  relationship pattern label` → CREATE NODE/REL TABLE từ manifest hoặc query),
  datetime rewrite `timestamp('<iso>')`, params render inline uniform-key (union
  key của cả batch + các field query tham chiếu; `CAST(NULL AS T)` theo hint
  coalesce) vì binder từ chối prepared param LIST<STRUCT (probe 2026-09-13).
  Ghi chú dialect (khớp Python cùng fail): `SET n += map` và `FOREACH` cleanup
  parse-fail trên ladybug 0.20.4 → các path `+=` (typed relations, evidence
  sites, topology `SET x += row`) và cleanup FOREACH là falkordb-only trên cả
  2 implementation; ART/FTS index create soft-skip khi column chưa có (store
  mới) để local provider dùng được — khác Python (raise), đã ghi nhận.
  
**Parity kết quả** (`reports/phase03-dual-write-diff.md`): 
- input 1 (stock subset): nodes 57/57, edges 37/37, diff=0
- input 2 (testdata variant): nodes 52/52, edges 37/37, diff=0
Gate parity chạy trên FalkorDB local (127.0.0.1:6379) — queries byte-identical
giữa 2 bên. Benchmark `reports/phase03-benchmark.md`: 1k nodes functions_full —
Python median 0.039s vs Rust 0.051s (gồm process spawn + connect; bản thân write
~5ms) — không đặt gate tuyệt đối.

Khác biệt scope có chủ đích (đã ghi trong lib.rs): journal runtime không wire
vào writer Rust (plane phase-02 giữ phía Python/CLI đến orchestrator Rust phase
09); `enqueue_deferred_relations` / `close_barrier_and_drain` /
`close_node_production_and_drain_edges` (journal-coupled) chưa port.
