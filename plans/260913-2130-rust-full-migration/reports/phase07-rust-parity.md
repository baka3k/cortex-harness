# Phase 07 — rust analyzer parity (python vs rust)

- chạy: 2026-09-14 03:13:49
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


## Gates (analyzer-rust)

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-rust --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-rust` | PASS (3/3 tests) |
| testdata FULL — graph diff ngoài mask | PASS — diff_total=0 (nodes 58/58, edges 92/92) |
| testdata FULL — `[SCAN_RESULT]` byte-identical | PASS — `parser=rust files=3 functions=27 classes=12 vectors=0 vector_status=disabled` |
| incremental seed — graph diff | PASS — diff_total=0 |
| incremental run — cleanup counts | PASS — py=(23, 0) rust=(23, 0) (deleted_nodes, deleted_unknown_functions) |
| incremental run — graph diff sau cleanup trên graph seed | PASS — diff_total=0 (nodes 23/23, edges 38/38; service.rs bị xoá thật khỏi seed) |
| stock corpus | SKIP — 0 file `.rs` dưới stock (ngoài skip dirs) — không có matching sources |

## Grammar pins

- Rust backend: `tree-sitter = "0.25"`, `tree-sitter-rust = "0.24"` (crates.io 0.24.2).
- Python reference (venv): `tree-sitter 0.26.0` + `tree-sitter-rust 0.24.2` — cùng dòng grammar
  0.24.2 ⇒ node kinds khớp từng chữ (đối chiếu qua graph diff = 0).

## Điểm port cần chú ý (byte-exact)

- `_SCAN_SKIP_DIRS` (23 entry) ∪ `COMMON_SCAN_EXCLUDE` ∪ `CORTEX_EXTRA_IGNORE_DIRS`; scan sort
  theo full path; không walk symlink dir (khớp `os.walk` followlinks=False).
- rust_analyzer KHÔNG có import-impact expansion: incremental chọn đúng manifest changed đã lọc
  `.rs`; empty manifest ⇒ full scan (khớp `if selected` của Python).
- `_add_type_use` charclass `r"[<&*\\[\\](),;]"` của Python parse thành class `{<,&,*,\,[}` + literal
  `(),;]` ⇒ gần no-op: candidate GIỮ nguyên dấu `<>` (vd external type `Vec<String>`); được port
  nguyên văn + unit test chốt hành vi.
- `types` có thể chứa TRÙNG symbol_id: `_ensure_external_type` (registry) và `_add_type_use`
  (external_types) là 2 registry riêng — cả 2 đều push vào `types` (khớp Python).
- `impl_item` không tạo node; child scope = `impl_owner_name.split("::")`, fallback fullmatch
  `[A-Za-z_][A-Za-z0-9_]*` tạo qualified tên owner.
- `_control_context` (branch/loop frames) không port: chỉ nằm trong call-row dicts mà writer
  không đọc — không thể vào graph.

## Accepted divergences (ngoài phạm vi graph)

- Qdrant/embedding lane không port: `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled`
  (reference chạy với env đã sanitize cũng ra disabled — byte-identical).
- `--config`, `--output/-o`, `--pretty`, `--qdrant-*` tuning: nhận và bỏ qua phía Rust. Lưu ý
  python rust_analyzer KHÔNG nhận `--config` (khác js) nên parity harness không truyền cờ này cho
  phía Python; backend Rust accept-and-ignore qua `AnalyzerArgs`.
- `[graph] written {counts}` (verbose) in format BTreeMap Rust thay vì dict repr Python — ngoài
  parity gates (chỉ so `[SCAN_RESULT]`, cleanup counts, graph dump).
- Dry-run in 1 dòng tóm tắt thay vì full JSON payload (không thuộc gate; gate FULL/incremental
  chạy qua đường write thật).

## CALLS project-scope contract

`write_calls` yêu cầu project_id trên call rows. rust_analyzer.py dựng rows không project_id nên
reference chạy qua journal env (`configure_journal_env`, mode `shadow`) để writer bơm
`metadata.project_id`; backend Rust supply `project_id` tường minh trên call rows (tiền lệ
analyzer-js). Graph diff = 0 xác nhận 2 đường cho cùng kết quả.

## Files

- `rust/crates/analyzer-rust/` (Cargo.toml, src/main.rs, src/rustparse.rs, src/pipeline.rs)
- `tests/fixtures/rust-analyzer/{geometry.rs,container.rs,service.rs}`
- `scripts/rust_parity/analyzer_parity_rust.py`
