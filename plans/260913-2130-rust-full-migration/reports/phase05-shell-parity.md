# Phase 05 — shell analyzer parity (python vs rust)

- chạy: 2026-09-14 02:33:49
- testdata: `tests/fixtures/shell-application` (có program-mapping ledger)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=6 rust=6
- edges: py=5 rust=5
- diff_total: **0**


### scan_result testdata_no_ledger

- py: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`


### TESTDATA_NO_LEDGER

- nodes: py=6 rust=6
- edges: py=5 rust=5
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=shell files=2 functions=1 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=6 rust=6
- edges: py=5 rust=5
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=shell files=2 functions=2 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=shell files=2 functions=2 vectors=0 vector_status=disabled`

- cleanup: py=(0, 0) rust=(0, 0)


### INC_RUN

- nodes: py=8 rust=8
- edges: py=6 rust=6
- diff_total: **0**


### scan_result stock_full

- py: `[SCAN_RESULT] parser=shell files=24 functions=36 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=shell files=24 functions=36 vectors=0 vector_status=disabled`


### STOCK_FULL

- nodes: py=1346 rust=1346
- edges: py=1366 rust=1366
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

