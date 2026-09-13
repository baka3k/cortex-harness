# Phase 07 — cobol analyzer parity (python vs rust)

- chạy: 2026-09-14 04:35:40
- testdata: `tests/fixtures/cobol-application` (fixed/free format, copybook nested + cycle + missing, malformed syntax, EXEC SQL/CICS, PERFORM THRU/UNTIL/VARYING, GO TO DEPENDING, ALTER, CALL literal + dynamic, payroll.cbl: sections + COPY REPLACING + nhiều FD)
- grammar: không có tree-sitter-cobol grammar crate trên crates.io (crate 0.1.0 là stub 768 byte) ⇒ cả 2 phía dùng CÙNG native grammar bundle `code-tiny/tools/cobol/lib/cobol.cpython-310-darwin.so` (ABI 14): Python ctypes (`parser_runtime.py`) ↔ Rust libloading (`parser_runtime.rs`, `tree_sitter_language::LanguageFn::from_raw`). Error/missing node sets byte-identical (golden test `rust/crates/analyzer-cobol/tests/grammar_parity.rs`).
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- project-scope: `project_id` là thành phần của `stable_id` nên 2 phía chạy CÙNG `--project-id=parity_cobol` trên 2 graph khác nhau; cobol_analyzer.py truyền project_id tường minh vào relations + file rows ⇒ không cần journal-shadow env (khác go_analyzer.py).

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=cobol files=9 nodes=64 edges=122 diagnostics=31 graph=195 vectors=0 artifact=/Users/hieplq1.aip/AI/cortex-harness/tests/fixtures/cobol-application/.cortex/cobol/facts.json`
- rust: `[SCAN_RESULT] parser=cobol files=9 nodes=64 edges=122 diagnostics=31 graph=195 vectors=0 artifact=/Users/hieplq1.aip/AI/cortex-harness/tests/fixtures/cobol-application/.cortex/cobol/facts.json`


### TESTDATA_FULL

- nodes: py=64 rust=64
- edges: py=111 rust=111
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=cobol files=5 nodes=49 edges=104 diagnostics=24 graph=158 vectors=0 artifact=/private/var/folders/kb/nt6s9qdn22x56dbyyy0x02zr0000gn/T/p07_cobol_inc_96j14sgx/corpus/.cortex/cobol/facts.json`
- rust: `[SCAN_RESULT] parser=cobol files=5 nodes=49 edges=104 diagnostics=24 graph=158 vectors=0 artifact=/private/var/folders/kb/nt6s9qdn22x56dbyyy0x02zr0000gn/T/p07_cobol_inc_96j14sgx/corpus/.cortex/cobol/facts.json`


### INC_SEED

- nodes: py=49 rust=49
- edges: py=94 rust=94
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=cobol files=4 nodes=48 edges=110 diagnostics=25 graph=162 vectors=0 artifact=/private/var/folders/kb/nt6s9qdn22x56dbyyy0x02zr0000gn/T/p07_cobol_inc_96j14sgx/corpus/.cortex/cobol/facts.json`
- rust: `[SCAN_RESULT] parser=cobol files=4 nodes=48 edges=110 diagnostics=25 graph=162 vectors=0 artifact=/private/var/folders/kb/nt6s9qdn22x56dbyyy0x02zr0000gn/T/p07_cobol_inc_96j14sgx/corpus/.cortex/cobol/facts.json`


### INC_RUN

- nodes: py=52 rust=52
- edges: py=103 rust=103
- diff_total: **0**


> [warn] stock scenario bỏ qua — stock không chứa nguồn COBOL (.cbl/.cob/.cpy/.copy); lưu trong report.


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-cobol --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-cobol` | PASS (9 tests, gồm golden grammar parity) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup-after-write xoá node deleted file (qua graph diff) | PASS (diff=0) |
| stock | SKIP — stock không chứa nguồn COBOL |

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0` — Rust không embed Qdrant (key decision
   #3); Python chỉ sync khi `--qdrant-url` + `--qdrant-collection` được truyền
   (harness không truyền).
2. `--config /dev/null` (Rust nhận và bỏ qua); cobol_analyzer.py KHÔNG có cờ
   này.
3. `--device`, `--batch-size`, `--max-embed-chars`, `--cache-dir`,
   `--ignore-cache`, message-scan flags: nhận và bỏ qua (plane Python).
   Lưu ý: `--cache-dir`/`--ignore-cache` Python-side áp cho dependency cache;
   Rust CŨNG đọc/ghi dependency cache cùng format JSON (parity bằng được vì
   format giống nhau) nhưng parse-cache/batch flags là no-op.
4. Verbose log lines (ngoài `[SCAN_RESULT]`) không bắt buộc byte-identical.
5. `--dry-run`: CẢ 2 phía in dòng `Dry run: parser=cobol ...` (đã port).
6. `facts.json` artifact: Rust ghi cùng path với structure sorted-keys
   tương đương nhưng KHÔNG byte-identical (runtime info: provider/
   tree_sitter_version/library_path khác nhau theo bản chất) — nội dung
   artifact là debug-plane, không so parity.
7. Provider `neo4j`: Rust backend không hỗ trợ (fail khác chỗ so với Python
   trả None) — harness dùng falkordb.
8. Python fallback `tree_sitter_language_pack` khi không tìm thấy bundled
   grammar library: Rust fail `COBOL_RUNTIME_UNAVAILABLE` (pack là extension
   module Python). Harness truyền `--cobol-language-library` tường minh cho
   cả 2 phía nên không chạm nhánh này.

