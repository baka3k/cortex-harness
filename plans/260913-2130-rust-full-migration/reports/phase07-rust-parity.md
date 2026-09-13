# Phase 07 — rust analyzer parity (python vs rust)

- chạy: 2026-09-14 03:35:58
- testdata: `tests/fixtures/rust-analyzer` (mod/impl/trait/generics/methods/alias/macro/cross-file use)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=rust files=3 functions=27 classes=12 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=rust files=3 functions=27 classes=12 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=58 rust=58
- edges: py=92 rust=92
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=rust files=3 functions=27 classes=12 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=rust files=3 functions=27 classes=12 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=58 rust=58
- edges: py=92 rust=92
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=rust files=2 functions=11 classes=5 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=rust files=2 functions=11 classes=5 vectors=0 vector_status=disabled`

- cleanup: py=(23, 0) rust=(23, 0)


### INC_RUN

- nodes: py=23 rust=23
- edges: py=38 rust=38
- diff_total: **0**


> [warn] skip stock: chỉ 0 file .rs dưới stock (ngoài skip dirs) < 3 — không có matching sources — stock scenario bỏ qua.


## Kết luận

- FAILURES: không có — PASS toàn bộ

