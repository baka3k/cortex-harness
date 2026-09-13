# Phase 07 — jp1 analyzer parity (python vs rust)

- chạy: 2026-09-14 04:47:54
- testdata: `tests/fixtures/jp1-analyzer` — 2 file sample khó: JC00NIGHT.txt (nested jobnet 2 tầng, ar= multi-scope, 1 arc không resolve, queue unit không exec target, mixed-case props Ty/TE) và JC00KANJI.txt (cp932/Shift_JIS với comment Nhật — exercise legacy decode path); kèm scripts/ để exec target resolve được.
- parser: line-based regex (`^unit=`, `^(ty|cm|te)=`, `ar=(f=..,t=..)`), KHÔNG dùng grammar ⇒ không có tree-sitter version skew. Decode legacy port byte-exact: BOM utf-16/utf-8-sig → NUL heuristic → utf-8 → cp932 (bảng full sinh từ CPython: 18381 pair + byte đơn) → cp1252 errors=replace (5 byte undefined ⇒ U+FFFD).
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- accommodation (không sửa rows): `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` cho CẢ 2 phía — jp1_analyzer.py emit `Jp1Unit-[:CALLS]->ShellScript` mà không writer nào tạo node ShellScript ⇒ endpoint preflight fail-loud (upstream). INCLUDES/NEXT giữa Jp1Unit vẫn ghi đầy đủ và so sánh.
- project-scope: `--project-id=parity_jp1` cho cả 2 phía (unit_id có file_path rel nên 2 phía dùng chung root corpus tạm cho từng run).

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=11 rust=11
- edges: py=15 rust=15
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=11 rust=11
- edges: py=15 rust=15
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=jp1 files=2 functions=0 vectors=0 vector_status=disabled`

- cleanup: py=(3, (11, 0)) rust=(3, (11, 0))


### INC_RUN

- nodes: py=11 rust=11
- edges: py=13 rust=13
- diff_total: **0**


> [warn] stock scenario bỏ qua — stock không chứa file .txt dạng JP1 jobnet export; lưu trong report.


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo clippy -p analyzer-jp1 --all-targets -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-jp1` | PASS (4 golden tests) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup-before-write counts (regex bắt buộc khớp) | PASS |
| inc_run: graph diff ngoài mask | PASS (diff=0) |
| stock | SKIP — stock không chứa nguồn JP1 |

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled` — Rust không embed
   Qdrant (key decision #3); Python chỉ sync khi `--qdrant-url` được truyền
   (harness không truyền).
2. `--config /dev/null` (Rust nhận và bỏ qua); jp1_analyzer.py KHÔNG có cờ này.
3. `--output/-o`, `--pretty`, `--dry-run` payload JSON, `--cache-dir`,
   `--ignore-cache`, message-scan flags: nhận và bỏ qua (plane Python/debug).
   Python in JSON payload khi `--dry-run`/`--pretty`; Rust không in (debug
   plane, không so parity).
4. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
5. CLI: positional `path` của jp1_analyzer.py không port làm positional độc
   lập (orchestrator luôn truyền `--root`); `--root` bắt buộc ở Rust (clap).
6. `cp932` decode: bảng sinh từ CPython nên strict-identical (kể cả các
   mapping 2 ký tự PUA của lead 0xA0..=0xDF/0xFD..0xFF). `cp1252` replace:
   5 byte undefined (81 8D 8F 90 9D) ⇒ U+FFFD như CPython (khác WHATWG).
7. Provider `neo4j`: Rust backend không hỗ trợ (fail khác chỗ so với Python
   trả None ⇒ RuntimeError) — harness dùng falkordb.

## Upstream findings (không sửa — ngoài scope crate này)

1. **jp1_analyzer.py + writer contract: CALLS→ShellScript fail-loud.**
   jp1_analyzer.py emit `Jp1Unit-[:CALLS]->ShellScript` (exec_target) nhưng
   không node ShellScript nào được tạo ⇒ endpoint preflight raise ở CẢ Python
   lẫn Rust writer khi graph chưa có sẵn target. Harness quarantine các rows
   này qua `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` cho cả 2 phía;
   INCLUDES/NEXT ghi đầy đủ.
2. `_clean` của jp1 strip `"` cả hai đầu sau khi rstrip `;` — hành vi giữ
   nguyên trong port (unit name/comment có thể mất quote bao ngoài).

