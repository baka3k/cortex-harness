# Phase 07 — go analyzer parity (python vs rust)

- chạy: 2026-09-14 03:25:05
- testdata: `tests/fixtures/go-analyzer` (generics, receivers, goroutines, defer, select, type switch, aliases)
- grammar pins: PyPI `tree-sitter-go` **0.25.0** ↔ crates.io `tree-sitter-go` **0.25** (cùng dòng grammar); `tree-sitter` PyPI 0.26.0 ↔ crates.io 0.25
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- accommodation (không sửa rows): Python reference chạy qua `run_go_reference.py` — journal-shadow env cho CALLS project_id fallback + skip identity-index validation cho ExternalModule INCLUDES (Rust writer vốn không validate theo manifest); `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` đặt cho CẢ 2 phía để quarantine giống hệt các rows không resolve được target (INCLUDES → ExternalModule, ALIASES → primitive như `float64`) — upstream go_analyzer.py emit các rows này và writer contract (Py lẫn Rust) không bao giờ materialize được.

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=go files=3 functions=14 classes=9 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=go files=3 functions=14 classes=9 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=42 rust=42
- edges: py=49 rust=49
- diff_total: **0**


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=go files=3 functions=14 classes=9 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=go files=3 functions=14 classes=9 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=42 rust=42
- edges: py=49 rust=49
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=go files=2 functions=8 classes=1 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=go files=2 functions=8 classes=1 vectors=0 vector_status=disabled`

- cleanup: py=(23, 0) rust=(23, 0)


### INC_RUN

- nodes: py=33 rust=33
- edges: py=41 rust=41
- diff_total: **0**


> [warn] skip stock: chỉ 0 file .go dưới stock (ngoài skip dirs) < 3 — stock scenario bỏ qua (stock không chứa Go sources).


## Kết luận

- FAILURES: không có — PASS toàn bộ


## Gates

| Gate | Kết quả |
|---|---|
| `cargo build/clippy -p analyzer-go -- -D warnings` | PASS (0 warning) |
| `cargo test -p analyzer-go` | PASS (6 unit tests) |
| testdata_full: [SCAN_RESULT] byte-identical | PASS |
| testdata_full: graph diff ngoài mask | PASS (diff=0) |
| inc_seed (FULL trên corpus copy): scan + diff | PASS (diff=0) |
| inc_run (incremental trên graph seed, clean=False): scan | PASS (byte-identical) |
| inc_run: cleanup counts (regex bắt buộc khớp) | PASS (py=(23,0) rust=(23,0)) |
| inc_run: graph diff ngoài mask | PASS (diff=0) |
| stock | SKIP — stock không chứa file .go |

## Grammar pins

- Rust: `tree-sitter = "0.25"`, `tree-sitter-go = "0.25"` (crates.io 0.25.0).
- Python venv tham chiếu: `tree-sitter-go` 0.25.0, `tree-sitter` 0.26.0 —
  cùng dòng grammar, node kinds khớp (đã probe: `type_alias`,
  `type_case`/`communication_case`/`default_case` tồn tại; `case_clause` và
  `expression_case` KHÔNG tồn tại ⇒ dead entries trong `_BRANCH_NODES` giữ
  nguyên hành vi).

## Accepted divergences (không tác động graph-plane)

1. `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled` — Rust không embed
   Qdrant (key decision #3); Python chỉ khác khi `--qdrant-url` được truyền.
2. `--config` (Rust nhận và bỏ qua); Python go_analyzer KHÔNG có cờ này.
3. `--output/-o`, `--pretty`, `--cache-dir`, `--ignore-cache`, message-scan
   flags: nhận và bỏ qua (plane Python).
4. `--dry-run`: Python dump payload JSON; Rust in số file tìm thấy.
5. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
6. CLI nhận `--root` (orchestrator contract); positional `path` của
   go_analyzer.py không port (single-file mode không dùng bởi orchestrator).

## Upstream findings (không sửa — ngoài scope crate này)

1. **go_analyzer.py + writer contract: ExternalModule INCLUDES fail-loud.**
   go_analyzer.py ghi `File-[:INCLUDES]->ExternalModule` với `target_label`
   tường minh, nhưng static schema manifest không có id index cho
   `ExternalModule` ⇒ Python writer raise
   `"target label 'ExternalModule' has no required id index"` ngay ở
   preprocessing của `write_all` (trước cả endpoint audit) ⇒ MỌI project Go có
   import thoát rc=3. Rust writer không validate group theo manifest
   (`RelationshipGroup::new_unchecked`) nên fail MUỘN hơn — tại endpoint
   audit. Đây là divergence Py/Rust writer-internal (không đụng trong task
   này) + upstream bug của go_analyzer.py.
2. **ALIASES tới primitive không bao giờ materialize được**: `type MyInt = int`
   sinh relation `alias::… ->(Type) "int"` mà node Type "int" không tồn tại ⇒
   endpoint preflight failure ở CẢ 2 writer.
3. Harness accommodation: `run_go_reference.py` bỏ identity-index validation
   (mirror hành vi Rust writer) + `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1`
   cho CẢ 2 phía ⇒ cùng rows vào, cùng chính sách quarantine, graph so được
   đầu-cuối. DECLARES/USES_TYPE/POINTER_TO/TEMPLATES/POSSIBLE_CALLS/CALLS vẫn
   được ghi đầy đủ và so sánh.

