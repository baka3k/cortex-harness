# Phase 08 — express_js overlay parity (python vs rust)

- chạy: 2026-09-14 05:49:10
- fixture: `tests/fixtures/web-overlays/express_js`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-express-js`
- base parser (prerequisite): `js` (python, journal-shadow)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### testdata_full

- stdout py: `[overlay] framework=express_js endpoints=7 relationships=5 graph={'nodes': 7, 'relationships': 5, 'deleted': 0}`
- stdout rust: `[overlay] framework=express_js endpoints=7 relationships=5 graph={'nodes': 7, 'relationships': 5, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=16 rust=16
- edges: py=16 rust=16
- diff_total: **0**


### inc_run

- stdout py: `[overlay] framework=express_js endpoints=5 relationships=3 graph={'nodes': 5, 'relationships': 3, 'deleted': 0}`
- stdout rust: `[overlay] framework=express_js endpoints=5 relationships=3 graph={'nodes': 5, 'relationships': 3, 'deleted': 0}`
- byte-identical: **True**

- nodes: py=11 rust=11
- edges: py=10 rust=10
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

