# Phase 07 — swift analyzer parity (python vs rust)

- chạy: 2026-09-14 03:36:02
- testdata: `tests/fixtures/swift-analyzer` (class/struct/protocol/enum/extension/methods/fields/typealias/subscript/cross-file)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=61 rust=61
- edges: py=114 rust=114
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=61 rust=61
- edges: py=114 rust=114
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=swift files=2 functions=9 classes=8 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=2 functions=9 classes=8 vectors=0 vector_status=disabled`

- cleanup: py=(27, 0) rust=(27, 0)


### INC_RUN

- nodes: py=27 rust=27
- edges: py=34 rust=34
- diff_total: **0**


> [warn] skip stock: chỉ 0 file .swift dưới stock (ngoài skip dirs) < 3 — không có matching sources — stock scenario bỏ qua.


## Kết luận

- FAILURES: không có — PASS toàn bộ

