# Phase 04 — analyzer parity (python vs rust)

- chạy: 2026-09-14 01:31:20
- stock: `/Users/hieplq1.aip/baka3k/stock`
- testdata: `tests/fixtures/python-analyzer`
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata

- py: `[SCAN_RESULT] parser=python files=2 functions=17 classes=6`
- rust: `[SCAN_RESULT] parser=python files=2 functions=17 classes=6`
- duration: python=2.7s rust=0.1s


### FULL testdata

- nodes: py=26 rust=26
- edges: py=38 rust=38
- diff_total (ngoài mask ['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']): **0**


### summary schema

- keys: ['calls', 'classes', 'commit_sha', 'commit_sha_before', 'duration_seconds', 'files', 'functions', 'incremental', 'language', 'parser', 'project_id', 'project_name', 'relations', 'repo', 'written']
- files=2 functions=17


### scan_result stock

- py: `[SCAN_RESULT] parser=python files=114 functions=874 classes=166`
- rust: `[SCAN_RESULT] parser=python files=114 functions=874 classes=166`
- duration: python=4.7s rust=1.5s


### FULL stock

- nodes: py=1149 rust=1149
- edges: py=2990 rust=2990
- diff_total (ngoài mask ['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']): **0**


### incremental (corpus 30 files từ stock)

- cleanup deleted_nodes: py=3 rust=3
- cleanup deleted_unknown: py=0 rust=0


### INCREMENTAL

- nodes: py=517 rust=517
- edges: py=1382 rust=1382
- diff_total (ngoài mask ['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']): **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

