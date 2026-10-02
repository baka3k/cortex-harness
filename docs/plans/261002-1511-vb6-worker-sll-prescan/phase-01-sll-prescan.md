# Phase 01 — Worker: SLL pre-scan + scanning progress

> Gate ra: M1 (single-file không còn treo), M3 (ngữ nghĩa payload giữ nguyên
> nơi định nghĩa), M4 (`pytest -k vb6` xanh)

## Tasks

### 1.1 Shared SLL recipe (WorkerRunner.java)

- Extract `static VisualBasic6Parser createSllParser(final String vbCode)`
  (package-private, WorkerRunner) từ thân `parseStageSll` hiện tại:
  lexer removeErrorListeners → CommonTokenStream → parser
  `setPredictionMode(SLL)` + `new BailErrorStrategy()` + removeErrorListeners.
- Thêm listener bail-on-full-context (AD-02) trong helper:
  `parser.addListener(new BaseErrorListener() { reportAttemptingFullContext
  → throw new ParseCancellationException(...) })` — đúng chỗ sinh vách đá
  `execATNWithFullContext`/`closure_` (jstack 2026-10-02). SLL thuần luôn có
  DFA bound; cắt full-context = trần thời gian hằng nhỏ.
- `parseStageSll` gọi lại helper (trả `ParseStage(ctx, tokens)` như cũ,
  tokens = `(CommonTokenStream) parser.getInputStream()`).
- `VB6_WORKER_SLL=0` vẫn tắt whole two-stage ở `parseCode` như cũ — helper
  không đọc env này (AD-06).

### 1.2 countSyntaxErrors → SLL (Vb6Worker.java:2080-2105)

- Body mới: `createSllParser(content)` + `parser.startRule()` trong try;
  catch `ParseCancellationException`/Throwable → trả 1 (giữ contract cũ:
  Throwable → 1); parse sạch → 0.
- Giữ method signature `private static int countSyntaxErrors(String)` —
  không đổi call site (:261).
- Ghi chú code 1 dòng: `error_nodes` là 0/1 từ nay (AD-05).

### 1.3 Scanning progress lines (Vb6Worker.java pre-pass :238-267)

- Trước `entry.syntaxErrors = countSyntaxErrors(...)`, nếu entry đọc OK:
  `System.err.println(WorkerRunner.PROGRESS_PREFIX + " scanning " + i + '/'
  + totalEntries + " file=" + entry.filePath)`.
- `totalEntries = entries.size()` (mẫu số manifest; loadError giữa chừng chỉ
  làm lệch cosmetic — Open question trong plan); `i` chỉ tăng cho entry đọc
  thành công (khớp population của countSyntaxErrors).
- KHÔNG in gì khác không có prefix (R1 plan 261002-1410).

### 1.4 Docs trong code

- Cập nhật header comment stderr protocol của Vb6Worker: 3 kinds
  `scanning`/`parsing`/`parsed`, prefix là filter key.
- Comment tại AD-05 slot (:672-673): `error_nodes` 0/1.

### 1.5 Tests (`tests/test_vb6_worker_progress.py`)

- `WorkerProgressTest`:
  - thêm `SCAN_LINE_RE = ^\[vb6\]\[worker\] scanning (\d+)/(\d+) file=(.*)$`;
  - assert mỗi file có đúng 1 scanning line, số thứ tự 1..N, scanning đầu
    xuất hiện TRƯỚC parsing/parsed đầu tiên;
  - fixture `malformed.bas` vẫn ok=false, file sạch vẫn ok=true (M3).
- `AdapterRelayTest`: thêm 1 dòng `scanning` vào `FAKE_STDERR`; assert
  (a) relay live không cần verbose, (b) `_PROGRESS_LINE_RE` không match →
  summary vẫn `2 files`, (c) scanning không vào top-5 slowest.
- Không test nào assert tổng số dòng stderr → không đổi chỗ khác (research §4).

### 1.6 Build + verify nhanh

- `mvn -q -f code-tiny/tools/vb/antlr_worker/worker/pom.xml -DskipTests package`
  (jar rebuild theo mtime stamp của adapter).
- `pytest tests/test_vb6_worker_progress.py tests/test_vb6_antlr_worker_contract.py -q`
  → green.
- `uv run pytest -k vb6 -q --ignore=tests/test_unified_contract_doc_paths.py`
  → green (M4; ignore file mcp 2.x hỏng sẵn).

## Test scope

- Mapped AC: M1 (chạy jar 1-file smoke local), M3 (fixture assertions mới),
  M4 (suite).
- Expected size: **M** — 1 file Java chính + 1 refactor nhỏ + 1 test file.

## Định nghĩa xong

- [x] countSyntaxErrors chạy SLL + bail (syntax error & full-context)
- [x] parseStageSll dùng helper chung, không còn recipe trùng
- [x] scanning lines ra stderr đúng prefix, trước parsing lines
- [x] Header protocol + comment error_nodes cập nhật
- [x] Tests mới + full `-k vb6` pass (147 passed, 2026-10-02)

> Ghi chú build: ANTLR trong shaded jar dùng signature
> `reportAttemptingFullContext(Parser, DFA, int, int, java.util.BitSet,
> ATNConfigSet)` (không phải SimulatorState/misc.BitSet như ANTLR mới).
