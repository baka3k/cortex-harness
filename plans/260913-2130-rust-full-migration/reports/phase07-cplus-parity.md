# Phase 07 — C/C++ (cplus) analyzer parity (python vs rust)

- chạy: 2026-09-14 16:23:27
- testdata: `tests/fixtures/cplus-analyzer` (include/alice + src/alice + res)
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-cplus`
- grammar pin: tree-sitter-c **0.24.2** + tree-sitter-cpp **0.23.4** (Rust) == tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4 (PyPI venv; `tree_sitter_languages.get_parser` raise TypeError với tree_sitter 0.26 nên Python dùng fallback binding trực tiếp — verify bằng import)
- mode: tree-sitter fallback (không compile_commands.json, không bootstrap clang) trên CẢ HAI bên
- stock corpus: **skip**
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=cplus files=6 functions=36 classes=21 resources=4`
- rust: `[SCAN_RESULT] parser=cplus files=6 functions=36 classes=21 resources=4`


### TESTDATA_FULL

- nodes: py=85 rust=85
- edges: py=180 rust=180
- diff_total: **0**


### scan_result inc

- py: `[SCAN_RESULT] parser=cplus files=6 functions=36 classes=21 resources=4`
- rust: `[SCAN_RESULT] parser=cplus files=6 functions=36 classes=21 resources=4`


### INC

- nodes: py=85 rust=85
- edges: py=180 rust=180
- diff_total: **0**


### scan_result inc

- py: `[SCAN_RESULT] parser=cplus files=2 functions=17 classes=8 resources=0`
- rust: `[SCAN_RESULT] parser=cplus files=2 functions=17 classes=8 resources=0`

- cleanup: py=(16, 1) rust=(16, 1)


### INC

- nodes: py=87 rust=87
- edges: py=162 rust=162
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú parity (phase 07)

- **Grammar pin**: `tree-sitter = "0.25"` + `tree-sitter-c = "=0.24.2"` +
  `tree-sitter-cpp = "=0.23.4"`. Venv Python: tree-sitter 0.26.0 +
  tree-sitter-c 0.24.2 + tree-sitter-cpp 0.23.4;
  `tree_sitter_languages.get_parser("cpp"/"c")` raise
  `TypeError __init__() takes exactly 1 argument (2 given)` →
  `cplus_analyzer.py` rơi vào fallback import `tree_sitter_cpp`/`tree_sitter_c`
  trực tiếp. Cùng grammar version ⇒ parse tree byte-identical (verify golden
  test `tests/golden_parse.rs` khớp probe Python).
- **Fallback mode**: harness KHÔNG bootstrap clang — `--parse-quality` default
  `report` tự set `disable_compile_db_bootstrap` ở cả 2 bên; fixture không có
  compile_commands.json nên cả hai chạy extension/content heuristics
  (`_is_cpp_file`: sibling check + cpp-hint regex).
- **Journal env (Python reference)**: call rows của cplus_analyzer.py không có
  `project_id` tường minh — `_require_call_project_scope` lấy từ journal
  metadata. Harness `configure_journal_env(..., mode="shared-shadow")` như
  orchestrator; lane production của cplus là `shared-required` (plane
  Project/Repository setup đã gate ở phase-03 writer parity, không lặp lại
  trong diff này). Rust bù `project_id` tường minh vào call/evidence rows.
- **Quirks được tái tạo chính xác** (khớp hành vi Python, có golden test):
  `_walk_tree` copy using-state per-child ⇒ `using_namespaces`/`using_imports`
  của payload luôn rỗng; `_extract_function_name` dùng `_first_identifier`
  (identifier ĐẦU tiên trong declarator, gồm cả phần scope của qualified name
  — out-of-class `int Derived::run(){}` cho name `Derived`); alias name/target
  lấy identifier đầu/từ `typedef` text.
- **Header alternate-grammar retry**: port đủ (`.h` có ERROR → reparse với
  grammar flipped, chọn theo `candidate_is_strictly_better`).
- **Subprocess boundary (key decision #8)**: clang semantic-evidence plane
  (clang_worker.py/libclang 18.1.1, parse_recovery, semantic_worker) GIỮ
  Python. Rust nhận `--parse-quality` (off/report/repair) — `repair` chỉ có
  ý nghĩa khi recovery plane Python chạy; backend Rust không invoke subprocess
  trong parity này (fallback mode + report). `evidence_merge.py` là consumer
  MCP-side (`mcp/cplus/cplus_mcp.py`) — không nằm trong analyzer path, skip.
- **Không port** (documented): `clang_parser.py` (dead code, không ai import),
  `semantic_context/semantic_shadow/semantic_worker` (plane MCP/semantic, không
  ảnh hưởng graph write chuẩn), `pilot_rollout/proc_manifest` (ops tooling).
  `rc_parser.py` + `windows_resource_parser.py` PORT đầy đủ (fixture có
  res/app.rc: DIALOGEX + STRINGTABLE + ICON + UIControl relations).
- **Accepted divergence**: Pro*C plane (.pc/.pcc — proc_analyzer +
  proc_source_map masking + guarded_publication) chưa port; .pc vẫn được scan
  và parse structure bằng grammar C nhưng không sinh proc_nodes/SQL relations.
  Fixture parity không chứa .pc nên gate không bị ảnh hưởng. `--parse-quality
  repair` artifact + parse-quality JSON đầy đủ (parser_version strings của
  Python runtime) — artifact-only fields, không ảnh hưởng graph.
- **Parse cache / resume**: Python-only, accept-and-ignore ở Rust
  (transparent với graph).
- **Mask bổ sung**: `_start_id`/`_end_id` (row-id nội bộ FalkorDB) — strip ở
  harness như phase 06.

## Files

- `rust/crates/analyzer-cplus/` — crate mới (bin `analyzer-cplus`): `cparse.rs`
  (tree-sitter walk + parse_c_family_file), `cscan.rs` (scan + cpp heuristics +
  encodings + include graph), `identity.rs` (function_identity), `quality.rs`
  (parse_quality), `rcparse.rs` (rc_parser), `validate.rs`
  (payload_validation), `analyzer.rs` (pipeline + writer planes), `position.rs`
  (os.path helpers), lib+bin split.
- `tests/fixtures/cplus-analyzer/` — corpus: namespaces lồng, class hierarchy
  + override + pure-virtual, templates, enum, typedef/using, out-of-class
  definitions, free functions, extern "C", function-pointer fields, macros,
  include references chéo file, control-flow calls (if/for/ternary), .rc
  resource script + resource.h.
- `scripts/rust_parity/analyzer_parity_cplus.py` — harness dual-graph
  `p07_cplus_<tag>_py`/`_rs`.

