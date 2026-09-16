# Phase 05 — PHP analyzer parity (python vs rust)

- chạy: 2026-09-14 02:34:23
- testdata: `tests/fixtures/php-analyzer`
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-php`
- python ref: `code-tiny/tools/php/php_analyzer.py` (--config /dev/null)
- grammar pins: tree-sitter = 0.25, tree-sitter-php = 0.24 (crates.io không có 0.25 cho php; PyPI ref dùng tree-sitter-php 0.24.1 vì tree-sitter-languages 1.10.2 broken với tree_sitter 0.26)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + internal edge ids `['_start_id', '_end_id']`
- stock scenario: chạy khi stock có PHP scannable, tự skip nếu không

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=php files=3 functions=25 classes=12`
- rust: `[SCAN_RESULT] parser=php files=3 functions=25 classes=12`


### TESTDATA_FULL

- nodes: py=46 rust=46
- edges: py=70 rust=70
- diff_total: **0**


### scan_result inc

- py: `[SCAN_RESULT] parser=php files=3 functions=25 classes=12`
- rust: `[SCAN_RESULT] parser=php files=3 functions=25 classes=12`


### INC

- nodes: py=46 rust=46
- edges: py=70 rust=70
- diff_total: **0**


### scan_result inc

- py: `[SCAN_RESULT] parser=php files=2 functions=11 classes=6`
- rust: `[SCAN_RESULT] parser=php files=2 functions=11 classes=6`

- cleanup: py=(30, 0) rust=(30, 0)


### INC

- nodes: py=36 rust=36
- edges: py=50 rust=50
- diff_total: **0**


### scan_result stock_full

- py: `[SCAN_RESULT] parser=php files=0 functions=0 classes=0`
- rust: `[SCAN_RESULT] parser=php files=0 functions=0 classes=0`


### STOCK_FULL

- nodes: py=1 rust=1
- edges: py=0 rust=0
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Ghi chú port (grammar pins, divergences đã chấp nhận)

### Grammar pins

- Rust: `tree-sitter = "0.25"` + `tree-sitter-php = "0.24"` (crates.io dừng ở 0.24.2 — không có 0.25 cho php crate; 0.24.2 phụ thuộc tree-sitter ^0.25 nên ghép được với core 0.25).
- Python reference (venv): PyPI `tree-sitter-php 0.24.1` — `tree-sitter-languages 1.10.2` BỊ VỠ với `tree_sitter 0.26.0` (`get_parser('php')` raise TypeError → `php_analyzer.py` fallback sang tree_sitter_php), nên grammar tham chiếu thực tế là 0.24.1; Rust 0.24.2 cùng dòng grammar 0.24 — không thấy parse drift trên toàn bộ corpus (fixture + stock).
- Node-kind quirk đã verify cả 2 phía: grammar 0.24 đổi tên `method_call_expression` → `member_call_expression`; `_iter_calls` của reference không liệt kê kind mới nên member calls (`$this->m()`) KHÔNG được capture — port giữ nguyên đúng hành vi.

### Divergences đã chấp nhận (có chủ đích)

1. Call rows gửi thêm `project_id` (explicit scope) — bắt buộc theo contract writer `_require_call_project_scope`; trùng hành vi `python_analyzer.py` + template analyzer-python. Reference PHP chạy với shadow-journal env để metadata cung cấp cùng giá trị.
2. Qdrant/embedding/message-scan/parse-cache: flags nhận và bỏ qua (vector plane là Python-side theo key decision #3).
3. `--config /dev/null`: nhận và bỏ qua (Python pre-parse `load_harness_config` là no-op trên /dev/null).
4. Edge props `_start_id`/`_end_id` (internal node row ids của engine) được mask thêm ngoài MASKED_PROPS — cùng bản chất với `_src`/`_dst`; endpoint thật đã so qua edge key.

### Suspected shared/reference bug (KHÔNG sửa — ngoài lane)

- `code-tiny/tools/php/php_analyzer.py:1427-1430` (`all_calls.append` trong `build_call_graph`): call rows thiếu `project_id`, trong khi `code-tiny/tools/graph/writer/language_writer.py:130-149 (`_require_call_project_scope`) từ chối row thiếu project_id khi không có journal metadata → journal-less run của reference CRASH (`ValueError: call row requires project_id (row 0)`). `tools/python/python_analyzer.py:1612-1621` đã được cập nhật với cùng contract (comment "keeps journal-less runs valid") — php analyzer chưa được cập nhật theo. Rust port đi theo contract mới.

