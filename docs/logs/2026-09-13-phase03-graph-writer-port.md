# Phase 03 — graph writer + operations port sang Rust (parity gate PASS) — 2026-09-13

## Context

Phase 03 của chương trình Rust full migration
(`plans/260913-2130-rust-full-migration/plan.md`): port writer plane còn lại
của `code-tiny/tools/graph` sang Rust — query contract, language writer,
operations (9 module), topology writer, schema preflight, và driver abstraction
`GraphStore` với 2 backend. Đóng Wave W0 (graph core) của chương trình.

## Change

- Crate mới `rust/crates/cortex-graph-writer/`:
  - `src/query_contract.rs` — port `query_contract.py`, compiled query
    byte-exact với Python (golden test so từng chữ trong `tests/`).
  - `src/language_writer.rs` — ~50 upsert methods, `write_batches` (state
    resume **opt-in** — `resume_state: true`; Python mặc định `state=None`),
    typed relations với endpoint audit fail-closed + cardinality check +
    `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS`, call dedupe replay-safe,
    evidence edges, call evidence sites/observations, build configurations,
    semantic coverage, proc joins, `write_all` orchestration với label
    inference từ identity rows.
  - `src/operations.rs` — 9 op module (package/class/namespace/type/function/
    infra/document/flow/cross_edge) + `batch_write_nodes/edges` dialect
    ladybug (CREATE + per-property SET, không `SET n = map`).
  - `src/topology.rs` — `ProjectTopologyWriter` + `stable_fact_id`
    (sha256[:24], material `\x1f`-join, casefold scope).
  - `src/preflight.rs` — inspect → create missing required → poll ONLINE.
  - `src/store/` — trait `GraphStore`; `FalkorDbStore` (param header
    falkordb-py, `__falkordb_now` datetime rewrite, index create/inspect) +
    `LadybugStore` (bootstrap, auto-DDL, inline literal rendering).
  - `src/upserts.rs` — catalog query text constants + persisted compiler
    node_identity/file_cleanup/orphan_cleanup.
- `rust/crates/cortex-falkordb/src/client.rs`: `Param::Map` (stringify
  ``{`k`:v}`` khớp `helpers.py` của falkordb-py), float format `str(2.0)`
  → "2.0", accept reply 1-section (query không RETURN), tolerate fresh
  graph key khi pre-load schema (`FalkorDbClient::schema_load_empty_key_error`,
  lỗi server reply về dạng `ClientError::Redis`), `connection_mut` accessor.
- `rust/crates/cortex-falkordb/src/value.rs`: `FalkorValue::to_json_value`
  (shape khớp `_normalize_falkordb_value` của driver Python).
- Parity harness: `scripts/rust_parity/gen_writer_rows_fixture.py` (2 variant:
  stock subset + testdata), `scripts/rust_parity/dual_write_diff.py`
  (Python writer → `stock_pw`, Rust example `dual_write` → `stock_rw`, dump +
  diff với timestamp mask), `scripts/rust_parity/bench_writer_1k.py`.
- Journal runtime **không** wire vào writer Rust — plane phase-02 giữ phía
  Python/CLI đến orchestrator phase 09 (đã ghi trong `phase-03.md`).

## Impact

- Analyzers/sync Rust (phase 05+) có sẵn writer plane để ghi graph; Python
  giữ default đến cutover (strangler-fig). Risk thấp — không đổi hành vi
  Python hiện hành, chỉ thêm code mới + mở rộng client falkordb có test.
- Wave W0 (graph core) đóng — Wave D (analyzers) có thể bắt đầu.

## Decision

- Parity gate chạy trên FalkorDB local (queries byte-identical 2 bên) thay vì
  ladybug, vì các path `+=` (typed relations, evidence sites, topology) và
  FOREACH cleanup parse-fail trên ladybug 0.20.4 — **cả hai implementation
  như nhau** (xem log dialect riêng). Cần ≥2 backend chạy được đầy đủ → giải
  quyết ở phase 08/09 hoặc khi ladybug hỗ trợ `+=`.
- Journal coupling tách khỏi writer port để giữ phase độc lập; retry
  reconcile path của `write_batches` sẽ vào cùng lúc orchestrator Rust.

## References

- plan: `./plans/260913-2130-rust-full-migration/phase-03.md`
- reports: `./plans/260913-2130-rust-full-migration/reports/phase03-dual-write-diff.md`,
  `./plans/260913-2130-rust-full-migration/reports/phase03-benchmark.md`
- commit: 9a395db
- log liên quan: `./docs/logs/2026-09-13-phase02-journal-manifest-staging.md`,
  `./docs/logs/2026-09-13-phase01-falkordb-rust-client-go.md`
