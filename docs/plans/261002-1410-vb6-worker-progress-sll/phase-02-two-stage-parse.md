# Phase 02 — Two-stage SLL→LL trong WorkerRunner

> Gate ra: M4 (parity payload LL-only vs SLL-first), M5 (full vb6 suite pass)

## Tasks

### 2.1 Two-stage trong `WorkerRunner.parseCode` (override, không sửa vendor)

- Override `protected void parseCode(String vbCode, String moduleName,
  boolean isClazzModule, boolean isStandardModule, Program program,
  VbParserParams params)`:
  - **Stage SLL**: lexer + `CommonTokenStream` + parser (như vendor
    VbParserRunnerImpl.java:201-213), rồi:
    - `parser.getInterpreter().setPredictionMode(PredictionMode.SLL)`;
    - `parser.setErrorHandler(new BailErrorStrategy())`;
    - `parser.removeErrorListeners()` (cả lexer) + listener đếm lỗi riêng
      (không ConsoleErrorListener — giữ stderr sạch, R1);
    - `StartRuleContext ctx = parser.startRule()` trong try;
  - `catch (ParseCancellationException)` → **Stage LL**: dựng lại lexer/tokens/
    parser nguyên bản (DefaultErrorStrategy + listener theo
    `params.getIgnoreSyntaxErrors()` — y hệt vendor :203-219), parse lại từ đầu;
    đánh dấu `sllFallback=true`.
  - **Tail giữ nguyên ngữ nghĩa vendor** (:224-238):
    `analyzeDeclaredModuleName(ctx)` → effective module name →
    `new VbModuleVisitorImpl(...).visit(ctx)` — dùng `ctx`/`tokens` của stage
    chiến thắng. Phần duplicate ~30 dòng ghi chú rõ "mirror of vendor
    <pinned-commit>, keep in sync".
- Đọc cấu hình 1 lần: `boolean sllEnabled = !"0".equals(System.getenv("VB6_WORKER_SLL"))`
  (static init của WorkerRunner — escape hatch, R5).
- Counter: `sllFallbackFiles` (AtomicLong) + per-file stderr marker
  `[vb6][worker] parsed i/N file=… ms=… sll_fallback` (bổ sung suffix vào
  format Phase 01 — parser regex adapter chấp nhận suffix tùy ý).
- worker_meta thêm `sll_fallback_files` (AD-05).
- Params `ignoreSyntaxErrors` vẫn được tôn trọng ở stage LL; stage SLL bail
  không in lỗi ra console (listener riêng).

### 2.2 Parity test (M4)

- Test mới trong `tests/test_vb6_worker_progress.py` (hoặc file riêng
  `test_vb6_worker_two_stage.py`):
  - Fixture: `tests/fixtures/vb6-application` (dùng sẵn bởi contract test) +
    1 file synthetic chứa construct nhập nhằng (If/Case lồng + line continuation)
    đặt `tests/fixtures/vb6-two-stage/stress.cls`.
  - Cách so: gọi jar trực tiếp 2 lần trên CÙNG manifest temp (không đi qua
    adapter để dễ truyền env): lần 1 mặc định (two-stage), lần 2 với env
    `VB6_WORKER_SLL=0` → so sánh JSON stdout **byte-equal** (sort keys) cho
    toàn bộ file.
  - Assert `worker_meta.sll_fallback_files` có mặt (giá trị ≥ 0).
- Edge cases test:
  - file cú pháp lỗi thật (fixture có sẵn malformed case ở contract test :88-92)
    → vẫn ok=false y như trước, không crash ở stage SLL.

### 2.3 Build + suite

- `mvn -q -f code-tiny/tools/vb/antlr_worker/pom.xml -DskipTests package`.
- `pytest -k vb6 --ignore=tests/test_unified_contract_doc_paths.py` full
  (điểm neo ≥ 130 test, M5).
- Kiểm tra stderr không còn nhiễu `line x:y` từ ConsoleErrorListener ở stage
  SLL (đã remove listener) và stage LL vẫn hành xử như cũ với
  ignoreSyntaxErrors=true.

## Định nghĩa xong

- [ ] WorkerRunner.parseCode two-stage SLL→LL, tail mirror vendor pinned
- [ ] `VB6_WORKER_SLL=0` tắt được two-stage (escape hatch, R5)
- [ ] `sll_fallback_files` trong worker_meta; marker stderr per-file
- [ ] Parity test byte-equal pass trên fixture + stress fixture
- [ ] Full `-k vb6` pass; jar rebuilt
