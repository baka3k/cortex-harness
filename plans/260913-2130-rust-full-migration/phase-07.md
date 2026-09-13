# Phase 07 — WAVE D batch 3: systems & legacy analyzers (cplus lớn nhất + legacy group)

## Scope

| Analyzer | Python LOC | Ghi chú |
|---|---|---|
| `tools/cplus/` | **16.6k** | Lớn nhất: C/C++ Pro*C style, SqlStatement/SqlHostVariable/SqlCursor labels, semantic_worker (libclang pinned 18.1.1), parse-quality report/repair machinery (đã có flags --parse-quality trong orchestrator) |
| `tools/rust/` | 1.4k | parse Rust bằng tree-sitter-rust |
| `tools/go/` | 1.4k | |
| `tools/swift/` | 1.3k | |
| `tools/delphi/` | 2.7k | legacy pascal |
| `tools/cobol/` | 2.0k | CobolProgram/Section/Paragraph/Copybook/CicsCommand |
| `tools/vb/` | 3.1k | vb6 + vba + vbnet |
| `tools/jp1/` | 0.4k | JP1 job scheduler units |

## Điểm rủi ro riêng

- **cplus = 16.6k LOC + libclang**: `semantic_worker.py` là isolated worker dùng `libclang==18.1.1`
  bundle. Rust: `clang-sys`/`libclang-rs` cùng pin 18.1.1, giữ mô hình isolated worker
  (subprocess) y nguyên; parse-quality report/repair (max-files 500, wall-seconds 900,
  workers 4) port đúng semantics.
- Pro*C (.pc) files: SqlStatement/host variables extraction — testdata `procsample` chính là
  repository mẫu (đã sync thật trong instance này).
- Legacy (cobol/delphi/vb): parser.regex-based nhiều — port trực tiếp logic regex (Python `re`
  → Rust `regex`/`fancy-regex` nếu có lookbehind).

## Parity

- cplus: dual-run trên **procsample thật** (đã có trong instance, .pc files thật) + testdata.
- cobol/vb/delphi/jp1: testdata riêng của từng analyzer.
- Parse-quality: inject file hỏng cố định → report artifact JSON khớp Python.

## Gate

- [ ] cplus parity trên procsample (nodes/rels exact ngoài mask).
- [ ] Parse-quality artifact JSON khớp trên corpus có file lỗi.
- [ ] 8/8 analyzer trong batch pass parity riêng lẻ.
- [ ] Dogfood: sync stock + procsample với toàn bộ analyzer đã port đến thời điểm này.
