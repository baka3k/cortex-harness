# Phase 05 — js analyzer parity (python vs rust)

- chạy: 2026-09-14 02:35:25
- testdata: `tests/fixtures/js-analyzer` (js parity corpus, jsx-free)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=javascript files=4 functions=20 classes=0`
- rust: `[SCAN_RESULT] parser=javascript files=4 functions=20 classes=0`


### TESTDATA_FULL

- nodes: py=28 rust=28
- edges: py=44 rust=44
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=javascript files=4 functions=20 classes=0`
- rust: `[SCAN_RESULT] parser=javascript files=4 functions=20 classes=0`


### INC_SEED

- nodes: py=28 rust=28
- edges: py=44 rust=44
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=javascript files=3 functions=9 classes=0`
- rust: `[SCAN_RESULT] parser=javascript files=3 functions=9 classes=0`

- cleanup: py=(7, 0) rust=(7, 0)


### INC_RUN

- nodes: py=14 rust=14
- edges: py=17 rust=17
- diff_total: **0**


> [warn] skip stock: chỉ 0 file js dưới stock (ngoài node_modules/skip dirs) < 3 — stock scenario bỏ qua.


## Kết luận

- FAILURES: không có — PASS toàn bộ

