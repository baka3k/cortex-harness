# Verification Report — vb6-worker SLL pre-scan (plan 261002-1511)

Measured 2026-10-02, jar `vb6-antlr-worker.jar` built 15:16 (SLL pre-scan +
scanning lines). Corpora live on disk, never committed; đường dẫn và tên file
corpus được generic hóa (hygiene — xem ghi chú trong research-digest.md).

## M1 — single pathological file (form lớn nhất corpus, ~19.6k dòng)

| | Before plan | After plan |
|---|---|---|
| Wall | **timeout 120s**, allFailed | **2s** (JVM 1.7s + parse 747ms) |
| stderr | 0 lines | `scanning 1/1` → `parsing 1/1` → `parsed 1/1 ms=747` |
| Kết quả | timeout, ok=false | **ok=true**, sll_fallback=0 (SLL parse sạch) |

## M2 — legacy corpus chính (hàng trăm file / ~400k dòng)

| Cấu hình | Pre-pass | Parse loop | End-to-end | Kết quả |
|---|---|---|---|---|
| Trước plan (pre-pass LL) | **HANG** — 0 progress lines trong 540s | không tới | timeout 540s, allFailed | treo |
| Sau plan — adapter default (`VB6_WORKER_SLL=0`, loop LL) | ~vài giây, đủ dòng scanning | **HANG tại file thứ 40/263 (form ~6.9k dòng)** — jstack: `WorkerRunner.parseCode:186` (stage LL), 331 CPU-s/334s, 100% CPU | timeout 600s, **0 ok** | R4 xảy ra đúng dự đoán |
| Sau plan — jar-direct (SLL hai-giai-đoạn ON) | ~5s (SLL+bail) | hoàn tất, slowest 2.8s | **53s wall**, đủ rows, 23 ok / 240 fail (file thật sự hỏng: SQL*Loader `.ctl`, fragment), sll_fallback 88% | **PASS** |

Top slowest (SLL two-stage): 5 file trong khoảng 1.7–2.8s, tất cả hoàn tất —
không còn file nào vượt quá vài giây.

## Sample legacy corpus regression (228 file, corpus đo M6 của plan 261002-1410)

- Adapter default: wall 2.5s, parse_ms_total 1,475ms (old LL-only ≈ 994ms —
  cùng cỡ), slowest 152ms. Không timeout, không hang.
- Cảnh báo H1 (đã ghi nhận, KHÔNG xử lý theo chỉ đạo của user): 11 file
  fragment-fixture của parser (MSDN snippets, không phải module VB6 thật)
  flip ok=true→false vì SLL bail trên fragment (LL đếm 0 lỗi, SLL bail →
  `no procedures after parse (1 syntax errors)`). Đây chính là R1/H1 đã
  chấp nhận trong plan. Khuyến nghị: loại thư mục fixture của parser khỏi
  corpus qua ignore-folder.

## Timeout-path regression (checklist phase 02.6)

- Manifest lớn + `--workspace-timeout-ms 500`: **rc=0, wall <1s**,
  tất cả file allFailed, `batch_error: timeout after 500ms` — daemon
  JVM-exit fix của plan 261002-1410 giữ vững.

## Tests

- `tests/test_vb6_worker_progress.py` + `test_vb6_antlr_worker_contract.py`:
  **38 passed** (gồm test mới `test_scanning_line_per_file_precedes_parsing`,
  relay scanning không cần verbose).
- Full suite: `pytest -k vb6` → **147 passed**.
- stdout contract: `test_stdout_contract_untouched` — stderr không rò vào
  stdout JSON.

## `error_nodes` semantics (AD-05)

`parse_meta.error_nodes` giờ là 0/1 (SLL+bail verdict), không còn exact LL
count. `has_error` giữ nguyên ngữ nghĩa boolean. Re-grep consumer: không có
test/consumer Python nào đọc giá trị đếm (research §2, xác nhận lại trước
khi commit).

## Kết luận + follow-up

1. Pre-pass không còn treo: từ "hang vô hạn vô hình" → ~5s có log trên toàn
   corpus.
2. **R4 evidence**: với default hiện tại (`VB6_WORKER_SLL=0` do go/no-go của
   plan 261002-1410 — đo trên corpus mẫu nơi LL chạy tốt), corpus legacy
   chính vẫn treo trong **parse loop** (file form ~6.9k dòng, stage LL).
   Bằng chứng mới này cho thấy nên reconsider default sang SLL hai-giai-đoạn
   (đã harden bail-on-full-context, hoàn tất 53s) HOẶC set
   `VB6_WORKER_SLL=1` cho workspace này — follow-up 1 dòng adapter + flip
   test, chờ quyết định.
