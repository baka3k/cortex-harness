# Phase 06 — Kotlin analyzer parity (python vs rust)

- chạy: 2026-09-14 03:09:06
- testdata: `tests/fixtures/kotlin-analyzer` (src/com/example/...)
- rust bin: `/Users/user/AI/cortex-harness/rust/target/release/analyzer-kotlin`
- grammar pin: **tree-sitter-kotlin-ng 1.1.0** (Rust, crates.io) == **tree-sitter-kotlin 1.1.0** (PyPI venv; `_get_kotlin_parser` fallback vì tree_sitter_languages.get_parser TypeError với tree_sitter 0.26) — parse trees byte-identical (diff sexp trên corpus enum/data-class/companion/delegation/lambda).
- tree-sitter pin: 0.25 (Rust) / 0.26.0 (PyPI venv) — FFI khác grammar không ảnh hưởng node kinds.
- stock corpus: **skip** — không có file .kt

## Ghi chú port

- Qdrant/embedding, message scan, parse cache / neo4j resume state: backend Rust nhận cờ và bỏ qua (khớp contract phase 04+; message scan là plane Python).
- Call rows: writer (`write_calls` → `_require_call_project_scope`, language_writer.py:137-147) bắt buộc project_id. Python analyzer gốc để rows KHÔNG project_id → harness set journal env như orchestrator (`configure_journal_env(..., mode="shared-shadow")`) cho phía py; phía rust rows mang project_id tường minh (khớp lane java).
- FunctionType/TAKES_FUNCTION: tree-sitter-kotlin v1.1.0 KHÔNG định nghĩa field `type` trên node `parameter` → nhánh này inert trên cả 2 phía (python `_extract_parameter_info` trả None). Port giữ nguyên logic cho fidelity.
- `enum class` parse thành `class_declaration` + modifier `enum` (kind="class") trên grammar 1.1.0 — `_class_kind` vẫn map `enum_class`/`enum_declaration` cho grammar cũ, khớp python.
- inc_run KHÔNG clean graph seed → cleanup đếm xoá thật (nếu clean trước, deleted_nodes luôn 0).

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`
- rust: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`


### TESTDATA_FULL

- nodes: py=37 rust=37
- edges: py=65 rust=65
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`
- rust: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`


### INC_SEED

- nodes: py=37 rust=37
- edges: py=65 rust=65
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`
- rust: `[SCAN_RESULT] parser=kotlin files=3 functions=21 classes=7`

- cleanup: py=(19, 0) rust=(19, 0)


### INC_RUN

- nodes: py=37 rust=37
- edges: py=65 rust=65
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ

