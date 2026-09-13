# Phase A2: Rollout BM25 auto — flip ON, đo lường, CI

**status: planned** · **track: A (query path)** · **depends: A1**

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

- [ ] A/B report có kết luận + commit cùng phase file.
- [ ] Default ON, test suite cập nhật theo default mới.
- [ ] `make rust-check` xanh trong CI run.
- [ ] Help/wiki nói đúng behavior mới.

**Trạng thái:** planned
