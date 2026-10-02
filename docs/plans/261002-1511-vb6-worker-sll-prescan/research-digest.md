# Research Digest — worker pre-pass (session 2026-10-02)

Nguồn: session evidence (jstack + đo thật) + researcher pass (Explore agent,
báo cáo đầy đủ trong conversation). Phục vụ plan `261002-1511`.

## 1. Live evidence vách đá pre-pass (2026-10-02, legacy corpus)

> Ghi chú hygiene: đường dẫn và tên file định danh corpus được thay bằng
> placeholder (`<CORPUS_DIR>`, `<LARGEST_FILE>`) — chi tiết thật chỉ tồn
> tại cục bộ lúc chạy verify, không đưa vào git.

- Legacy corpus (sync thật của user): `<CORPUS_DIR>` — hàng trăm file
  VB6, tổng ~400k dòng; nhiều file >5k dòng; largest `<LARGEST_FILE>.frm`
  ~20k dòng; 10 file đầu chiếm ~28% tổng số dòng.
- jstack giữa run full-corpus: thread `vb6-analyze` bão hòa CPU (CPU-s ≈
  wall-s) trong `ParserATNSimulator.closure_`/`getEpsilonTarget`; đáy
  stack = `Vb6Worker.countSyntaxErrors(:2100)` ←
  `analyze(:261)` → `startRule` → `module` → `moduleOptions`.
- Full-corpus run vượt workspace budget → timeout, stderr **0 dòng** (pre-pass
  chạy trước dòng progress đầu tiên).
- Single-file `<LARGEST_FILE>.frm` + workspace 120s → timeout, 0 progress lines.
- Đối chiếu: corpus đối chiếu nhỏ 194 file / 18.6k dòng: pre-pass + parse
  end-to-end **3s wall** (adapter run) — kích thước tuyệt đối không phải
  vấn đề, vách đá nằm ở file lớn + LL prediction.

## 2. countSyntaxErrors + consumption (agent report)

- Definition `Vb6Worker.java:2080-2105`: full ANTLR parse **LL default**,
  counting BaseErrorListener, catch Throwable → 1. Chỉ 1 call site :261.
- `entry.syntaxErrors` (field :122) consumed:
  - :307 — retry-exclusion set khi batch failure (`>0` → excluded);
  - :315 — marker `-1` excluded-from-batch;
  - :375 — branch `== -1` → "excluded after batch failure (syntax errors)";
  - :381-383, :391-393 — message strings (`>0` chọn message variant);
  - :672-673 — `parse_meta.has_error` (`>0`) + `parse_meta.error_nodes`
    (`max(0, N)`).
- **Không** gate primary batch: batch gồm mọi entry đọc được (:271-284) →
  thay count exact bằng 0/1 không mất payload.
- Không test Java; không test Python nào assert error strings /
  `has_error` / `error_nodes` / `countSyntaxErrors` (grep toàn tests/ +
  code-tiny/tools/vb; consumer `has_error`/`error_nodes` khác chỉ ở engine
  cplus/perl và vb_common.py:1175-1176 là tree-sitter, không worker).

## 3. Stderr protocol hiện tại

- `WorkerRunner.PROGRESS_PREFIX = "[vb6][worker]"` (:44, package-private).
- `parsing i/N` (:113) trước parse; `parsed i/N ms=… [ sll_fallback]`
  (:140-148) sau; Vb6Worker chỉ in usage (stderr) + 1 JSON stdout (:202).
- Adapter relay: mọi dòng prefix `[vb6][worker]` in live KHÔNG cần verbose
  (`vb6_antlr_adapter.py:333-341`); `_PROGRESS_LINE_RE` (:45-47) chỉ match
  ` parsed ` → line `scanning` tự relay mà không đụng summary/top-5.
- Test analysis (agent report §4): không test nào break khi thêm line kind
  mới; `test_start_line_per_file_precedes_parsed` dùng min-index trên
  parsing/parsed riêng — scanning không ảnh hưởng.

## 4. SLL recipe + pre-pass cost inventory

- `WorkerRunner.parseStageSll` (:206-216): instance method nhưng KHÔNG dùng
  instance state → tách được thành static helper dùng chung (AD-04).
  Imports ANTLR cần đã có ở cả 2 file.
- Pre-pass per-entry: readTextLenient (I/O, :466-483) + utf8CopyForBatch
  (chỉ non-UTF-8) + second read originalContent (:251-254) +
  declaredModuleName (regex scan, :2047-2064) + **countSyntaxErrors (ANTLR
  LL — hang source)** + dedup map. Không có bước đắt khác giữa pre-pass và
  parse loop (agent report §8).

## 5. Trạng thái plan 261002-1410 (cross-plan)

- status: `complete-with-exclusions` — M6 NO-GO: SLL-bail 33.3% > 30% trên
  corpus đo; adapter giờ spawn worker với `VB6_WORKER_SLL=0` mặc định
  (`vb6_antlr_adapter.py:303-307`, test `AdapterSllDefaultTest`).
- **Corpus đo M6 = corpus mẫu 228 file** (`/tmp/vb6_sll_results.json`:
  parse_ms_total ≈ 0.8–1.0s cả hai mode) — KHÔNG PHẢI corpus legacy nơi
  vách đá nằm. Nghĩa là go/no-go cũ chưa từng thấy vách đá; flipping default
  KHÔNG thuộc plan này (AD-06) — VERIFY trên corpus legacy sẽ cho data
  follow-up.
- Timeout-path regression của plan cũ: JVM exit 0.56s khi timeout — giữ
  nguyên checklist (phase 02.6).
