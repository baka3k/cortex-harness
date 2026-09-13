# Phase 08 — laravel overlay parity (python vs rust)

- chạy: 2026-09-14 05:49:24
- fixture: `tests/fixtures/web-overlays/laravel`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-laravel`
- base parser (prerequisite): `php` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[overlay] framework=laravel endpoints=3 relationships=2 graph={'nodes': 3, 'relationships': 2, 'deleted': 0}`
- stdout rust: `[overlay] framework=laravel endpoints=3 relationships=2 graph={'nodes': 3, 'relationships': 2, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=16 rust=16
- edges: py=18 rust=18
- diff_total: **0**


### inc_run

- stdout py: `[overlay] framework=laravel endpoints=4 relationships=2 graph={'nodes': 4, 'relationships': 2, 'deleted': 0}`
- stdout rust: `[overlay] framework=laravel endpoints=4 relationships=2 graph={'nodes': 4, 'relationships': 2, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=14 rust=14
- edges: py=14 rust=14
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

