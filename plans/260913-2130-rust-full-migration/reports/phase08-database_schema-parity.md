# Phase 08 — database_schema overlay parity (python vs rust)

- chạy: 2026-09-14 06:27:01
- testdata: `tests/fixtures/sql-family/database_schema` (ddl: tables/views/procs + masking; plsql: package spec/body + trigger)
- registered keys: database_sql (`--dialect sql`) + database_plsql (`--dialect plsql`), order 90/91 — mirror FRAMEWORK_ANALYZERS.
- mask: `_graph_id/_edge_id/_src/_dst/updated_at/created_at/last_updated/summary_updated_at/_start_id/_end_id`
- accommodation: manifest format `paths` (khác `files` của language analyzers) — port đúng `_manifest`.

### testdata_full [sql]

- py: `[overlay] database=sql objects=11 relationships=21 graph={'nodes': 11, 'relationships': 21, 'deleted': 0}`
- rust: `[overlay] database=sql objects=11 relationships=21 graph={'nodes': 11, 'relationships': 21, 'deleted': 0}`


### TESTDATA_FULL [sql]

- nodes: py=11 rust=11
- edges: py=21 rust=21
- diff_total: **0**


### testdata_full [plsql]

- py: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`
- rust: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`


### TESTDATA_FULL [plsql]

- nodes: py=5 rust=5
- edges: py=6 rust=6
- diff_total: **0**


### inc_seed [sql]

- py: `[overlay] database=sql objects=11 relationships=21 graph={'nodes': 11, 'relationships': 21, 'deleted': 0}`
- rust: `[overlay] database=sql objects=11 relationships=21 graph={'nodes': 11, 'relationships': 21, 'deleted': 0}`


### INC_SEED [sql]

- nodes: py=11 rust=11
- edges: py=21 rust=21
- diff_total: **0**


### inc_run [sql]

- py: `[overlay] database=sql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 6}`
- rust: `[overlay] database=sql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 6}`

- cleanup: py=None rust=None


### INC_RUN [sql]

- nodes: py=5 rust=5
- edges: py=6 rust=6
- diff_total: **0**


### inc_seed [plsql]

- py: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`
- rust: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`


### INC_SEED [plsql]

- nodes: py=5 rust=5
- edges: py=6 rust=6
- diff_total: **0**


### inc_run [plsql]

- py: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`
- rust: `[overlay] database=plsql objects=5 relationships=6 graph={'nodes': 5, 'relationships': 6, 'deleted': 0}`

- cleanup: py=None rust=None


### INC_RUN [plsql]

- nodes: py=5 rust=5
- edges: py=6 rust=6
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS |
| testdata_full [sql]: [overlay] + graph diff | PASS |
| testdata_full [plsql]: [overlay] + graph diff | PASS |
| inc_seed [sql/plsql]: FULL trên corpus copy | PASS |
| inc_run: delete_paths + graph diff trên graph seed | PASS |

## Accepted divergences (không tác động graph-plane)

1. `--ignore-cache`, `--disable-message-scan`, neo4j flags: nhận và bỏ qua
   (plane Python).
2. Python `Path(args.root).resolve()` ↔ Rust `fs::canonicalize` — cùng semantics
   symlink-resolve.

