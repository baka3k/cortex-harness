# Phase 07 — Delphi analyzer parity (python vs rust)

- chạy: 2026-09-14 04:16:30
- testdata: `tests/fixtures/delphi-analyzer` (Runner.dpr + geom/Shapes.pas + logic/AppLogic.pas + report/Report.pas)
- rust bin: `/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-delphi`
- grammar pin: tree-sitter-pascal 0.10.2 (Rust, Isopod) == grammar `pascal` của tree_sitter_language_pack (PyPI venv, cùng dòng Isopod; node kinds `interface`/`implementation` bare ⇒ section ranges rỗng trên cả 2 — verify golden test)
- stock corpus: **skip** — không có file .pas/.dpr
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=delphi files=4 functions=34 classes=0`
- rust: `[SCAN_RESULT] parser=delphi files=4 functions=34 classes=0`


### TESTDATA_FULL

- nodes: py=78 rust=78
- edges: py=194 rust=194
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=delphi files=4 functions=34 classes=0`
- rust: `[SCAN_RESULT] parser=delphi files=4 functions=34 classes=0`


### INC_SEED

- nodes: py=78 rust=78
- edges: py=194 rust=194
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=delphi files=3 functions=17 classes=0`
- rust: `[SCAN_RESULT] parser=delphi files=3 functions=17 classes=0`

- cleanup: py=(0, 0) rust=(0, 0)


### INC_RUN

- nodes: py=46 rust=46
- edges: py=112 rust=112
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 07)

- **Grammar**: `delphi_analyzer.py` dùng tree-sitter CHỈ cho `parse_meta`
  (has_error/ERROR nodes) và section ranges; toàn bộ trích xuất
  types/functions/fields/calls là REGEX thuần. Với grammar `pascal` Isopod
  (tree_sitter_language_pack của venv), node kinds là `interface`/
  `implementation` BARE — KHÔNG thoả bộ lọc "section"/"part" của
  `_extract_section_line_ranges_from_tree` ⇒ ranges luôn RỖNG ⇒ extraction
  KHÔNG lọc dòng. Rust pin `tree-sitter-pascal = "0.10.2"` (cùng dòng Isopod,
  LANGUAGE_VERSION 14) — golden test khẳng định ranges rỗng + has_error/
  error_nodes khớp (Runner.dpr: has_error=true, 2 ERROR nodes từ
  `uses X in 'path'`).
- **Semantic engine**: `delphi_analyzer.py` KHÔNG dùng `SemanticInferenceEngine`
  (grep chứng minh) — không cần port stage semantic.
- **Scan**: Delphi có ignore-list riêng (`_should_ignore_directory`: __history__,
  __recovery__, dcu/dcp, *.dcu...) + file filter .pas/.dpr/.inc — port trong
  crate, không dùng `framework::scan::scan_files` (python-specific).
- **Incremental**: selection = changed_existing ∪ **uses-impact BFS**
  (`_collect_unit_and_uses_index` + `_resolve_uses_by_file` resolve unit-name →
  file theo unit header rồi basename stem + `_expand_impacted_files_by_uses`);
  cleanup targets = changed ∪ deleted (sorted). Port đúng cả
  `uses_closure` có cache + cycle-guard (giữ semantics partial-cache).
- **Call resolution**: `_resolve_calls` scoring (file +15, uses-closure +7,
  same-scope +10; precedence raw-dotted/file+arity/scope-chain/name+arity/name;
  tie-break (score, qualified_name) max-đầu-tiên) — port từng chữ; golden test
  khớp toàn bộ callee_id từ Python.
- **CALLS edges**: delphi dùng `write_calls_with_site` (site_id =
  `parse_run_id:uuid5(caller->callee)`, props parse_run_id + commit_sha).
  Harness pin `--parse-run-id parity-run --commit-sha-after parity-sha` trên
  CẢ HAI backend để site_id/props deterministic. Rust bù `project_id` tường
  minh vào call row (writer contract — giống analyzer-java); Python nhận cùng
  giá trị qua journal-shadow env (`configure_journal_env`, mode
  `shared-shadow`) mô phỏng orchestrator.
- **Parse cache / Qdrant / embedding / message-scan**: không port (plane
  Python / trong suốt với graph); CLI nhận và bỏ qua toàn bộ cờ tương ứng.
  `--neo4j-state` nhận và REFUSE như Python (resume disabled, exit 2).
- **`_stable_point_id`**: `uuid.uuid5(NAMESPACE_URL, ...)` — Rust `uuid` crate
  v5, golden test khớp.
- **Cleanup counter**: cả 2 backend in `deleted_nodes=0` cho mutation scenario
  (đặc thù đánh giá query trên FalkorDB — giống phase 06) nhưng stale nodes
  của file xoá thực sự bị DETACH DELETE trên CẢ HAI graph (verify
  `report/*` nodes: seed=19 → run=0 trên cả hai graph).
- **Divergence chấp nhận**: dry-run print và các verbose print không phải
  [SCAN_RESULT] có thể khác nhẹ; parse-error/call-stats/unresolved JSON dùng
  `serde_json::to_string_pretty` (Python `json.dump` indent=2) — file phụ,
  không gate; `splitlines()` Python tách thêm \v/\f/\x85/\u2028... (port
  cover \n/\r\n/\r — thực tế của source Delphi).
- **Stock corpus**: skip — không có file .pas/.dpr trong stock.

## Files

- `rust/crates/analyzer-delphi/` — crate mới (bin `analyzer-delphi`):
  `dparse.rs` (dataclasses + helpers regex + `parse_delphi_file` + scan),
  `resolve.rs` (uses index/resolve/impact BFS/closure + `_resolve_calls`),
  `danalyzer.rs` (pipeline + rows + reports + `[SCAN_RESULT]`), `main.rs`
  (CLI flatten `AnalyzerArgs` + cờ delphi-specific), `tests.rs` +
  `golden_parse.json` (golden sinh từ Python reference).
- `tests/fixtures/delphi-analyzer/` — corpus: unit interface/implementation
  split, class inheritance + virtual/abstract/override, constructor/destructor,
  COM interface, record, pointer type `^T`, class method static, overload,
  forward declaration `TEngine = class;`, .dpr với `uses X in 'path'` (ERROR
  nodes cố ý), string/comment chứa `;` `)` `{`.
- `scripts/rust_parity/analyzer_parity_delphi.py` — harness dual-graph
  `p07_delphi_<tag>_py`/`_rs`.

