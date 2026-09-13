# Phase 08 — mybatis overlay parity (python vs rust)

- chạy: 2026-09-14 06:26:34
- testdata: `tests/fixtures/sql-family/mybatis` (mapper interfaces + annotation mappers + mapper XML với dynamic SQL/include/resultMap + spring bridge + config)
- cấu trúc: overlay dual-run — cả 2 graph được SEED bằng PYTHON java analyzer trước (journal shared-shadow), rồi overlay python vs rust chạy trên cùng input → diff cô lập đúng phần overlay.
- mask: `['_dst', '_edge_id', '_end_id', '_graph_id', '_src', '_start_id', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### summary testdata_full

- py: `[mybatis] modules=1 artifacts=7 parser_capabilities=3 semantic_facts=179 relationships=132 diagnostics=9`
- rust: `[mybatis] modules=1 artifacts=7 parser_capabilities=3 semantic_facts=179 relationships=132 diagnostics=9`

- facts: py=(179, 179) rust=(179, 179); rels: py=(132, 132) rust=(132, 132)


### TESTDATA_FULL

- nodes: py=217 rust=217
- edges: py=184 rust=184
- diff_total: **0**


### summary inc_seed

- py: `[mybatis] modules=1 artifacts=7 parser_capabilities=3 semantic_facts=179 relationships=132 diagnostics=9`
- rust: `[mybatis] modules=1 artifacts=7 parser_capabilities=3 semantic_facts=179 relationships=132 diagnostics=9`

- facts: py=(179, 179) rust=(179, 179); rels: py=(132, 132) rust=(132, 132)


### INC_SEED

- nodes: py=217 rust=217
- edges: py=184 rust=184
- diff_total: **0**


### summary inc_run

- py: `[mybatis] modules=1 artifacts=6 parser_capabilities=3 semantic_facts=173 relationships=126 diagnostics=10`
- rust: `[mybatis] modules=1 artifacts=6 parser_capabilities=3 semantic_facts=173 relationships=126 diagnostics=10`

- facts: py=(173, 173) rust=(173, 173); rels: py=(126, 126) rust=(126, 126)

- cleanup: py=97 rust=97


### INC_RUN

- nodes: py=212 rust=212
- edges: py=180 rust=180
- diff_total: **0**


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-sql-family --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-sql-family` | PASS (8 unit tests) |
| testdata_full: [mybatis] summary byte-identical | PASS |
| testdata_full: mybatis_facts/relationships counts | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): summary + counts + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, java base reseed trước): summary + counts | PASS (byte-identical) |
| inc_run: cleanup counts (`[cleanup][falkordb] deleted_nodes=N deleted_unknown_functions=0`) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |

## Grammar pins

- Java: `tree-sitter-java` **0.23.5** (crates.io) == PyPI `tree_sitter_java`
  0.23.5 (fallback path của venv) — symbol-id maps (class_ids/method_ids +
  comment-adjusted start_line) khớp byte-identical với `parse_java_file`.
- XML: `tree-sitter-xml` **0.7** (crates.io, tree-sitter-grammars) == grammar
  `xml` của `tree_sitter_language_pack` (node kinds STag/EmptyElemTag/CDSect/
  CData/EntityRef khớp).
- SQL: PyPI `tree-sitter-sql` **0.3.11** (derekstride) — vendored nguồn SINH từ
  sdist cùng version vào `sql-grammar/` (git tag không commit `src/parser.c`).

## Parser notes

1. Toàn bộ parse logic được port: detector (module/evidence/confidence +
   android gate), mapper interface (annotations, params, overloads, default/
   static bindable gate), annotation mapper (SQL/provider/Results), mapper XML
   (statements/fragments/resultMaps/includes expand với cycle+depth guard/
   dynamic nodes/config), SQL semantic (placeholder normalize → crud/tables/
   columns/joins/parameters provenance), resolver (mọi relationship type).
2. Diagnostics COUNT ảnh hưởng dòng `[mybatis]` — port đủ các nhánh emit
   (missing_file, parse_error, duplicate_statement, include_cycle/depth/
   unresolved, empty_sql, overloaded_statement_id, crud_mismatch, resolver...).

## Accepted divergences (không tác động graph-plane)

1. Fact artifact JSON (`--mybatis-facts-output`) — Rust ghi summary stub;
   payload đầy đủ là plane Python (không parity, không vào graph).
2. `parser_capabilities` cố định 3/available (grammar pinned phía Rust) —
   chỉ ảnh hưởng số trong dòng summary (đã byte-parity) chứ không đụng graph.
3. Qdrant/embedding/message-scan: nhận cờ và bỏ qua (key decision #3).
4. Harness-only: incremental chạy SAU java-base reseed (giống production order
   — base analyzer chạy trước overlay) để SEMANTIC_OF→Function/Class resolve.

