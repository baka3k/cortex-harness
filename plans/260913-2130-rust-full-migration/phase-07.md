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

- [x] cplus parity trên procsample (nodes/rels exact ngoài mask).
- [x] Parse-quality artifact JSON khớp trên corpus có file lỗi.
- [x] 8/8 analyzer trong batch pass parity riêng lẻ.
- [x] Dogfood: sync stock + procsample với toàn bộ analyzer đã port đến thời điểm này.

**Trạng thái 2026-09-14:** 8/8 PASS — cplus (85/85 nodes, 180/180 edges diff 0; clang plane
giữ Python subprocess theo key decision #8), rust (0.24.2), go (0.25.0), swift (0.7.3),
delphi (tree-sitter-pascal 0.10.2 chỉ cho parse_meta như Python), cobol (line-based
fixed-format + native grammar bundle dlopen qua libloading; golden stable_id),
vb×4 (line/regex parser + real Roslyn subprocess cho vbnet), jp1 (cp932 decode table
18,381 pairs sinh từ CPython). Reports: reports/phase07-{cplus,rust,go,swift,delphi,
cobol,vb,jp1}-parity.md. Ghi chú tham chiếu: go INCLUDES→ExternalModule bị writer reject
thiếu id-index (quarantine CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1 cả 2 bên);
vb bare `Const X` crash cả 2 backend (crash-parity được gate).
