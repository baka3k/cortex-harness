# Phase 07 — VB analyzer family parity (python vs rust)

- chạy: 2026-09-14 04:35:19
- variants: vbnet, vb6, vba, vbscript (crate `analyzer-vb`, 4 binary)
- testdata: `tests/fixtures/vb-analyzer` (1-2 file/variant, classifier + regex quirks)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- parser: `vb_common.parse_vb_file` là line/regex thuần (tree-sitter chỉ feed `has_error` vào parse_meta — không vào graph). Rust port full heuristic kể cả quirk line-number (`source.find(line, start-len(line))`) và dead regex `_ARRAY_DECL_RE`.
- vbnet Roslyn (key decision #8): plane C# giữ nguyên subprocess — Rust invoke cùng `RoslynVbWorker` (build + manifest + JSON payloads, fallback regex từng file). Gate set DOTNET_ROLL_FORWARD=LatestMajor để worker net9 chạy trên runtime .NET 10 — vbnet gate exercise đúng plane Roslyn (scan_result functions=7 = roslyn counts, khác regex counts=11); nếu worker vẫn fail, CẢ HAI phía fallback regex (parity đúng cả hai world).
- accommodation (không sửa rows): Python reference chạy với `configure_journal_env(mode="shadow")` cho CALLS project_id fallback; Rust supply `project_id` tường minh trên call rows.

### scan_result net/testdata_full

- py: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`
- rust: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`


### NET/TESTDATA_FULL

- nodes: py=28 rust=28
- edges: py=28 rust=28
- diff_total: **0**


### scan_result net/inc_seed

- py: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`
- rust: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`


### NET/INC_SEED

- nodes: py=28 rust=28
- edges: py=28 rust=28
- diff_total: **0**


### scan_result net/inc_run

- py: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`
- rust: `[SCAN_RESULT] parser=vbnet files=2 functions=7 classes=5`

- cleanup: py=(27, 0) rust=(27, 0)


### NET/INC_RUN

- nodes: py=28 rust=28
- edges: py=27 rust=27
- diff_total: **0**


### scan_result 6/testdata_full

- py: `[SCAN_RESULT] parser=vb6 files=3 functions=6 classes=0`
- rust: `[SCAN_RESULT] parser=vb6 files=3 functions=6 classes=0`


### 6/TESTDATA_FULL

- nodes: py=18 rust=18
- edges: py=22 rust=22
- diff_total: **0**


### scan_result 6/inc_seed

- py: `[SCAN_RESULT] parser=vb6 files=3 functions=6 classes=0`
- rust: `[SCAN_RESULT] parser=vb6 files=3 functions=6 classes=0`


### 6/INC_SEED

- nodes: py=18 rust=18
- edges: py=22 rust=22
- diff_total: **0**


### scan_result 6/inc_run

- py: `[SCAN_RESULT] parser=vb6 files=2 functions=5 classes=0`
- rust: `[SCAN_RESULT] parser=vb6 files=2 functions=5 classes=0`

- cleanup: py=(11, 0) rust=(11, 0)


### 6/INC_RUN

- nodes: py=18 rust=18
- edges: py=22 rust=22
- diff_total: **0**


### scan_result vba/testdata_full

- py: `[SCAN_RESULT] parser=vba files=2 functions=3 classes=0`
- rust: `[SCAN_RESULT] parser=vba files=2 functions=3 classes=0`


### VBA/TESTDATA_FULL

- nodes: py=12 rust=12
- edges: py=12 rust=12
- diff_total: **0**


### scan_result vba/inc_seed

- py: `[SCAN_RESULT] parser=vba files=2 functions=3 classes=0`
- rust: `[SCAN_RESULT] parser=vba files=2 functions=3 classes=0`


### VBA/INC_SEED

- nodes: py=12 rust=12
- edges: py=12 rust=12
- diff_total: **0**


### scan_result vba/inc_run

- py: `[SCAN_RESULT] parser=vba files=2 functions=4 classes=0`
- rust: `[SCAN_RESULT] parser=vba files=2 functions=4 classes=0`

- cleanup: py=(11, 0) rust=(11, 0)


### VBA/INC_RUN

- nodes: py=12 rust=12
- edges: py=13 rust=13
- diff_total: **0**


### scan_result vbscript/testdata_full

- py: `[SCAN_RESULT] parser=vbscript files=2 functions=3 classes=0`
- rust: `[SCAN_RESULT] parser=vbscript files=2 functions=3 classes=0`


### VBSCRIPT/TESTDATA_FULL

- nodes: py=10 rust=10
- edges: py=10 rust=10
- diff_total: **0**


### scan_result vbscript/inc_seed

- py: `[SCAN_RESULT] parser=vbscript files=2 functions=3 classes=0`
- rust: `[SCAN_RESULT] parser=vbscript files=2 functions=3 classes=0`


### VBSCRIPT/INC_SEED

- nodes: py=10 rust=10
- edges: py=10 rust=10
- diff_total: **0**


### scan_result vbscript/inc_run

- py: `[SCAN_RESULT] parser=vbscript files=2 functions=5 classes=0`
- rust: `[SCAN_RESULT] parser=vbscript files=2 functions=5 classes=0`

- cleanup: py=(9, 0) rust=(9, 0)


### VBSCRIPT/INC_RUN

- nodes: py=11 rust=11
- edges: py=14 rust=14
- diff_total: **0**


### ambiguous_const (upstream quirk, crash-parity)

- py rc=1, rust rc=1 (cùng `cannot infer target_label` từ write_all preprocessing)


> [warn] skip stock: chỉ 0 file VB-family dưới stock < 3 — stock scenario bỏ qua (stock không chứa VB sources).


## Kết luận

- FAILURES: không có — PASS toàn bộ

## Gates

| Gate | net (vbnet) | 6 (vb6) | vba | vbscript |
|---|---|---|---|---|
| testdata_full: [SCAN_RESULT] byte-identical | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |
| testdata_full: graph diff rỗng ngoài mask | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |
| inc_seed: [SCAN_RESULT] byte-identical | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |
| inc_seed: graph diff rỗng ngoài mask | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |
| inc_run: [SCAN_RESULT] byte-identical | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |
| inc_run: cleanup line xuất hiện ở cả 2 log | inc_run=PASS | inc_run=PASS | inc_run=PASS | inc_run=PASS |
| inc_run: cleanup counts khớp | inc_run=PASS | inc_run=PASS | inc_run=PASS | inc_run=PASS |
| inc_run: graph diff rỗng ngoài mask | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS | testdata_full=PASS, inc_seed=PASS, inc_run=PASS |


## Accepted divergences (không tác động graph-plane)

1. Parse cache (`--cache-dir`, `--disable-parse-cache`, `--ignore-cache`) là
   plane Python — Rust nhận và bỏ qua (verbose `cache=off` vs `cache=on`).
2. Qdrant/embedding KHÔNG port (key decision #3); `--qdrant-*`, `--embed-*`,
   `--device`, `--batch-size` nhận và bỏ qua.
3. Message scan là plane Python; default BẬT như Python nhưng gate chạy
   `--disable-message-scan` cho cả 2 phía.
4. `--config` Rust nhận và bỏ qua; Python pre-parse harness config
   (`load_harness_config`) với `/dev/null` = no-op.
5. `parse_meta` (has_error/error_nodes từ tree-sitter, worker_elapsed_ms,
   fallback_reason) chỉ sống trong parse cache Python — không vào graph; Rust
   set has_error=false.
6. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
7. Rust default Roslyn worker project resolve theo CWD layout repo
   (`code-tiny/tools/vb/roslyn_worker/...`) thay vì `__file__` Python;
   flag/env override giống nhau.
8. Harness accommodations (không đụng rows, áp cho CẢ HAI phía):
   `--parallel-workers 1` (Python default 4 thu payloads theo thread-completion
   order — nondeterministic ⇒ write order đổi ⇒ internal FalkorDB ids
   `_start_id`/`_end_id` khác dù graph content giống hệt), `--ignore-cache`
   (parse cache Python làm engine vbnet "dính" theo lần parse đầu giữa các
   run), `DOTNET_ROLL_FORWARD=LatestMajor` (chạy worker net9 trên runtime
   .NET 10 để exercise plane Roslyn), `--disable-message-scan` (message scan
   là plane Python-side).

## Suspected upstream bugs (không sửa — ghi nhận)

1. **Bare `Const X = ...` crash cả 2 backend**: `_VAR_DECL_RE` match scope
   `Const` ⇒ sinh thêm Variable row tên `X` trùng id với Constant row ⇒
   `write_all` preprocessing (Py lẫn Rust writer) raise
   `cannot infer target_label ... candidates=["Constant","Variable"]` ⇒
   analyzer crash trên mọi source có bare Const (rc=1). Đã gate crash-parity
   (`scenario_ambiguous_const`): CẢ HAI fail cùng error.
2. **Enum members mất phần tử đầu**: vòng extract member dùng `i > line_num`
   với `line_num` 1-based làm start index 0-based ⇒ member dòng đầu sau dòng
   Enum bị skip (port giữ nguyên, test `interface_enum_dual_rows`).
3. **Heuristic line-number** của Property/Event/Interface/Enum/Constant/
   Variable trả dòng trống ĐẦU TIÊN của file (hoặc 1) vì điều kiện
   `source.find(line, start - len(line)) == start - len(line)` chỉ thoả với
   `line=""` — các row này mang start_line/end_line sai từ upstream; port
   replica đầy đủ (py_find semantics).
4. `.ctl`/`.pag` nằm trong `_SOURCE_EXTS` vb6 nhưng classifier trả None ⇒
   dead extension (port giữ nguyên hành vi).

