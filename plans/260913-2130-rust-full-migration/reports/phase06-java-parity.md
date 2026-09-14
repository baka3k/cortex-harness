# Phase 06 — Java analyzer parity (python vs rust)

- chạy: 2026-09-14 16:23:37
- testdata: `tests/fixtures/java-analyzer` (src/com/example/...)
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-java`
- grammar pin: tree-sitter-java 0.23.5 (Rust) == tree-sitter-java 0.23.5 (PyPI venv)
- stock corpus: **skip** — không có file .java
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=java files=4 functions=27 classes=11`
- rust: `[SCAN_RESULT] parser=java files=4 functions=27 classes=11`


### TESTDATA_FULL

- nodes: py=52 rust=52
- edges: py=99 rust=99
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=java files=4 functions=27 classes=11`
- rust: `[SCAN_RESULT] parser=java files=4 functions=27 classes=11`


### INC_SEED

- nodes: py=52 rust=52
- edges: py=99 rust=99
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=java files=4 functions=28 classes=11`
- rust: `[SCAN_RESULT] parser=java files=4 functions=28 classes=11`

- cleanup: py=(0, 0) rust=(0, 0)


### INC_RUN

- nodes: py=53 rust=53
- edges: py=103 rust=103
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 06)

- **Grammar pin**: Rust `tree-sitter = "0.25"` + `tree-sitter-java = "0.23.5"`.
  Venv Python chạy fallback `tree_sitter_java` 0.23.5 (`tree_sitter_languages.get_parser`
  raise TypeError với tree_sitter 0.26 — verify bằng cách import thử). Cùng
  grammar version ⇒ parse tree byte-identical, không drift.
- **Semantic engine**: `java_analyzer.py` KHÔNG dùng `SemanticInferenceEngine`
  (grep chứng minh) — không cần port stage semantic, không đổi framework crate.
- **Scan**: java có skip-list riêng `_SCAN_SKIP_DIRS` (fnmatch POSIX
  case-sensitive) union `COMMON_SCAN_EXCLUDE` — port trong crate, không dùng
  `framework::scan::scan_files` (list python-specific).
- **Incremental**: selection = changed_existing ∪ import-impact BFS
  (`_collect_java_import_graph` + `_expand_impacted_files_by_imports`);
  function/class index build từ TOÀN BỘ file (index_payloads) — port đúng
  ảnh hưởng tới `resolve_callee_id`.
- **Journal env (Python reference)**: `java_analyzer.py` build call rows
  KHÔNG có `project_id` (java_analyzer.py:2162-2165) — `_require_call_project_scope`
  (tools/graph/writer/language_writer.py:137-147) lấy project_id từ journal
  metadata; chạy standalone không qua orchestrator sẽ fail
  `ValueError: call row requires project_id`. Harness mô phỏng orchestrator
  bằng `configure_journal_env(..., mode="shared-shadow")` trước khi spawn.
  Rust bù `project_id` tường minh vào call row (giống analyzer-python phase 04).
- **Mask bổ sung**: `_start_id`/`_end_id` là row-id nội bộ FalkorDB cấp theo
  lịch sử ghi từng graph (cùng bản chất `_graph_id`/`_edge_id` đã có trong
  `MASKED_PROPS` của dual_write_diff) — harness strip chúng trước diff thay vì
  sửa shared script.
- **Cleanup counter**: cả 2 backend in `deleted_nodes=0` cho mutation scenario
  (đặc thù đánh giá query trên FalkorDB) nhưng stale nodes của file xoá thực
  sự bị DETACH DELETE trên CẢ HAI graph (verify `Util` nodes: seed=2 → run=0).
- **Qdrant/message/parse-cache**: không port (plane Python / trong suốt với
  graph); CLI nhận và bỏ qua toàn bộ cờ tương ứng.
- **Stock corpus**: skip — không có file .java trong stock.

## Files

- `rust/crates/analyzer-java/` — crate mới (bin `analyzer-java`): `javaparse.rs`
  (parse), `javascan.rs` (scan + import graph + impacted BFS), `resolve.rs`
  (function/class index + `resolve_callee_id` + rows), `java_analyzer.rs`
  (pipeline), `main.rs` (CLI flatten `AnalyzerArgs` + cờ java-specific).
- `tests/fixtures/java-analyzer/` — corpus: interface + generics
  (`Comparable<Shape>`), class extends/implements + override + inner class +
  lambda + method reference + `this()`/`super()`, anonymous class, enum,
  record implements, functional-interface params (TAKES_FUNCTION), external
  type edges (`Object`, `Cloneable`).
- `scripts/rust_parity/analyzer_parity_java.py` — harness dual-graph
  `p06_java_<tag>_py`/`_rs`.

