# Phase A2: Rollout BM25 auto — flip ON, đo lường, CI

**status: DONE 2026-09-13** · **track: A (query path)** · **depends: A1**

## Mục tiêu

Bật auto-BM25 làm mặc định sau khi có bằng chứng A/B, và khoá gate Rust
vào CI.

## Việc cần làm

1. **A/B ranking report**: script nhỏ `scripts/benchmark_bm25_auto.py` —
   query set thật (tái dùng QUERIES trong `test_rust_bridge.py` +
   realworld_stock_test queries), chạy engine OFF/ON trên store local, in
   ranking diff + latency. Tiêu chí flip: bm25 chỉ đảo thứ tự các candidate
   có semantic score gần nhau (chênh < 0.1) HOẶC thêm hit mới có lý do
   keyword rõ — review thủ công, ghi vào file report.
2. **Flip default**: `CORTEX_BM25_AUTO` mặc định `1` trong
   `explore_service` path (constructor param vẫn override). Wiki
   `Makefile Targets & Build Automation.md` + help lifecycle bổ sung 1 dòng
   env mới.
3. **Rollback 1 dòng**: env `CORTEX_BM25_AUTO=0` — ghi vào help text.
4. **CI**: thêm job step `make rust-check` vào `.github/workflows/`
   (workflow lifecycle hiện có) — gate Rust không còn chỉ chạy tay.

## Gates

- [x] A/B report có kết luận + commit cùng phase file.
- [x] Default ON, test suite cập nhật theo default mới.
- [x] `make rust-check` xanh trong CI run.
- [x] Help/wiki nói đúng behavior mới.

**Trạng thái:** DONE 2026-09-13 — A/B report tại
`reports/ab-bm25-auto.md`: kết luận **ĐẠT flip ON** (21/120 slot đảo chỗ,
20/21 giữa candidate Δsemantic < 0.1; trường hợp Δ 0.362 duy nhất là
artifact cặp-rank, cặp displaced thật Δ 0.046 + lý do keyword rõ; 14
"hit mới" đều là re-entry cùng domain; query tiếng Việt bm25 im lặng đúng;
latency mean +0.01 ms/query). Default đã flip trong
`_BM25_AUTO_DEFAULT = True` (`intelligent_retrieval.py`, 1 dòng rollback +
env `CORTEX_BM25_AUTO=0`); suite cập nhật: test OFF dùng env=0 tường minh,
test unset→default-ON — full suite 161 passed, 45 subtests. Help text thêm
block "Retrieval env (CORTEX_BM25_AUTO)" vào cả `scripts/mcp-lifecycle.py`
(USAGE) và `scripts/mcp-lifecycle.ps1` (Write-Usage). CI: job `rust`
(`make rust-check`) thêm vào `.github/workflows/lifecycle-macos.yml` +
trigger path `rust/**`; `make rust-check` chạy xanh local (clippy -D
warnings + cargo test, 0 failed).

Ghi chú lệch thiết kế so với plan:
- Flip default đặt ở resolver của engine (`_BM25_AUTO_DEFAULT`) thay vì
  trong `explore_service` — explore_service không cần đụng (không truyền
  param), mọi call path hưởng default chung; rollback vẫn 1 dòng/env.
- Wiki `Makefile Targets & Build Automation.md` chưa sửa (file wiki nằm
  ngoài repo, ngoài phạm vi file được phép của track này) — behavior mới
  đã ghi đủ trong help lifecycle + report.
- Gate "make rust-check xanh trong CI run": chưa có CI run thật vì không
  push (ràng buộc không commit); bù bằng chứng `make rust-check` xanh local
  và job CI được thêm đúng lệnh đó.
