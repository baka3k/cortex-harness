# Phase 08 — fastapi_django overlay parity (python vs rust)

- chạy: 2026-09-14 05:48:51
- fixture: `tests/fixtures/web-overlays/fastapi_django`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-fastapi-django`
- base parser (prerequisite): `python` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[overlay] framework=fastapi_django endpoints=12 relationships=12 graph={'nodes': 12, 'relationships': 12, 'deleted': 0}`
- stdout rust: `[overlay] framework=fastapi_django endpoints=12 relationships=12 graph={'nodes': 12, 'relationships': 12, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=36 rust=36
- edges: py=43 rust=43
- diff_total: **0**


### inc_run

- stdout py: `[overlay] framework=fastapi_django endpoints=5 relationships=5 graph={'nodes': 5, 'relationships': 5, 'deleted': 0}`
- stdout rust: `[overlay] framework=fastapi_django endpoints=5 relationships=5 graph={'nodes': 5, 'relationships': 5, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=26 rust=26
- edges: py=27 rust=27
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

