# Phase 05 — TypeScript analyzer parity (python vs rust)

- chạy: 2026-09-14 02:33:55
- testdata: `tests/fixtures/ts-analyzer`
- stock frontend: `/Users/hieplq1.aip/baka3k/stock/frontend`
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=typescript files=7 functions=18 classes=0`
- rust: `[SCAN_RESULT] parser=typescript files=7 functions=18 classes=0`


### TESTDATA_FULL

- nodes: py=38 rust=38
- edges: py=32 rust=32
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=typescript files=7 functions=18 classes=0`
- rust: `[SCAN_RESULT] parser=typescript files=7 functions=18 classes=0`


### INC_SEED

- nodes: py=38 rust=38
- edges: py=32 rust=32
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=typescript files=4 functions=12 classes=0`
- rust: `[SCAN_RESULT] parser=typescript files=4 functions=12 classes=0`


### INC_RUN

- nodes: py=25 rust=25
- edges: py=20 rust=20
- diff_total: **0**


### scan_result stock_frontend

- py: `[SCAN_RESULT] parser=typescript files=76 functions=188 classes=0`
- rust: `[SCAN_RESULT] parser=typescript files=76 functions=188 classes=0`


### STOCK_FRONTEND

- nodes: py=339 rust=339
- edges: py=395 rust=395
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

