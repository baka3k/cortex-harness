# Phase 08 — Struts overlay parity (python vs rust)

- chạy: 2026-09-14 06:00:35
- testdata: `tests/fixtures/java-spring-overlays`
- rust bin: `/tmp/p08_ws/target/release/analyzer-struts`
- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=struts facts=20 relationships=21 diagnostics=1 graph=41`
- rs: `[SCAN_RESULT] parser=struts facts=20 relationships=21 diagnostics=1 graph=41`

- testdata_full analysis JSON artifact: py=29414B rs=29414B =PASS=


### TESTDATA_FULL

- nodes: py=73 rust=73
- edges: py=101 rust=101
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=struts facts=20 relationships=21 diagnostics=1 graph=41`
- rs: `[SCAN_RESULT] parser=struts facts=20 relationships=21 diagnostics=1 graph=41`

- inc_seed analysis JSON artifact: py=29414B rs=29414B =PASS=


### INC_SEED

- nodes: py=73 rust=73
- edges: py=101 rust=101
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=struts facts=27 relationships=30 diagnostics=1 graph=57`
- rs: `[SCAN_RESULT] parser=struts facts=27 relationships=30 diagnostics=1 graph=57`

- inc_run analysis JSON artifact: py=40899B rs=40899B =PASS=


### INC_RUN

- nodes: py=80 rust=80
- edges: py=110 rust=110
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

