# Phase 08 — plsql analyzer parity (python vs rust)

- chạy: 2026-09-14 06:27:15
- testdata: `tests/fixtures/sql-family/plsql` (package spec/body, triggers, dbms_scheduler job blocks, standalone routines, call/exec/generic/bare-call extraction, builtins dbms_/utl_)
- mask: `_graph_id/_edge_id/_src/_dst/updated_at/created_at/last_updated/summary_updated_at/_start_id/_end_id`
- accommodation: CALLS rows python không project_id → journal-shadow env cho py reference; rust supply project_id tường minh trên call rows (cùng graph, cùng giá trị).

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=plsql files=4 functions=10 classes=0`
- rust: `[SCAN_RESULT] parser=plsql files=4 functions=10 classes=0`


### TESTDATA_FULL

- nodes: py=18 rust=18
- edges: py=24 rust=24
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=plsql files=4 functions=10 classes=0`
- rust: `[SCAN_RESULT] parser=plsql files=4 functions=10 classes=0`


### INC_SEED

- nodes: py=23 rust=23
- edges: py=34 rust=34
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=plsql files=2 functions=6 classes=0`
- rust: `[SCAN_RESULT] parser=plsql files=2 functions=6 classes=0`

- cleanup: py=(12, 0) rust=(12, 0)


### INC_RUN

- nodes: py=20 rust=20
- edges: py=29 rust=29
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup counts (regex bắt buộc khớp) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |

## Grammar pins

- `sql_analyzer.py` parse bằng REGEX (không tree-sitter) cho routine scan —
  `_get_sql_parser`/tree-sitter path là dead code cho regex path, không port.
- MyBatis sql-semantic gate dùng chung grammar SQL: PyPI `tree-sitter-sql`
  0.3.11 (derekstride) — vendored trong `analyzer-sql-family/sql-grammar/`.

## Accepted divergences (không tác động graph-plane)

1. Qdrant/embedding KHÔNG port (key decision #3) — cờ `--qdrant-*`,
   `--embed-*`, `--device`, `--batch-size` nhận và bỏ qua.
2. Message scan là plane Python-side; `--enable/--disable-message-scan` nhận,
   skip có kiểm soát.
3. Parse cache / neo4j resume state không áp dụng cho backend này —
   `--disable-parse-cache`, `--ignore-cache`, `--neo4j-state`,
   `--disable-neo4j-resume`, `--keep-cache`, `--cache-dir` nhận và bỏ qua.
4. `--unresolved-calls-path` / `--call-stats-path` (output file conveniences
   ngoài graph-plane): nhận và bỏ qua phía Rust.
5. `--config` nhận và bỏ qua.
6. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.

