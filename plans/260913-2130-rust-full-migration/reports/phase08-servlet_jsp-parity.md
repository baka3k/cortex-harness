# Phase 08 — Servlet/JSP overlay parity (python vs rust)

- chạy: 2026-09-14 06:22:21
- testdata: `tests/fixtures/java-spring-overlays`
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-servlet-jsp`
- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### summary testdata_full

- py: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/testdata_full_py/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/testdata_full_rs/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`

- testdata_full preview JSON artifact: py=159872B rs=159872B =PASS=


### TESTDATA_FULL

- nodes: py=117 rust=117
- edges: py=158 rust=158
- diff_total: **0**


### summary inc_seed

- py: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/inc_seed_py/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/inc_seed_rs/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`

- inc_seed preview JSON artifact: py=159872B rs=159872B =PASS=


### INC_SEED

- nodes: py=117 rust=117
- edges: py=158 rust=158
- diff_total: **0**


### summary inc_run

- py: `{"analyzer":"servlet_jsp","applied":160,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":160,"deleted":0,"diagnostics":2,"facts":69,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/inc_run_py/servlet_jsp_preview.json","relationships":91,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":160,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":160,"deleted":0,"diagnostics":2,"facts":69,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-062221/inc_run_rs/servlet_jsp_preview.json","relationships":91,"stage":"complete","truncation_count":0,"updated":0}`

- inc_run preview JSON artifact: py=180613B rs=180613B =PASS=


### INC_RUN

- nodes: py=124 rust=124
- edges: py=173 rust=173
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

