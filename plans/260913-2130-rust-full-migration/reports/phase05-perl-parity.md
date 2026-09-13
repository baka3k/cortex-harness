# Phase 05 — perl analyzer parity (python vs rust)

- chạy: 2026-09-14 03:15:44
- testdata: `tests/fixtures/perl-application` (dedicated fixture corpus: packages, use/require, our/my/local vars, method/qualified/direct/coderef/eval refs, 1 file broken để exercise error-node diagnostics)
- mask: `['_dst', '_edge_id', '_end_id', '_graph_id', '_src', '_start_id', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- python invocation: journal env `shared-shadow` như orchestrator (`configure_journal_env`) — writer `_require_call_project_scope` lấy project_id của call rows từ journal metadata.
- stock scenario: bỏ qua — stock corpus không có file .pl/.pm/.t.

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=perl files=5 functions=6 classes=6 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=perl files=5 functions=6 classes=6 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=23 rust=23
- edges: py=37 rust=37
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=perl files=5 functions=6 classes=6 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=perl files=5 functions=6 classes=6 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=23 rust=23
- edges: py=37 rust=37
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=perl files=4 functions=7 classes=5 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=perl files=4 functions=7 classes=5 vectors=0 vector_status=disabled`

- cleanup: py=(5, 0) rust=(5, 0)


### INC_SEED

- nodes: py=26 rust=26
- edges: py=45 rust=45
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

