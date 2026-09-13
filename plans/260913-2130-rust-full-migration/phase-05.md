# Phase 05 — WAVE D batch 1: dynamic/scripting analyzers (js, ts, php, perl, shell)

## Scope — port theo đúng khung Phase 04, mỗi analyzer 1 crate con

| Analyzer | Python LOC | Ghi chú |
|---|---|---|
| `tools/ts/` | 8.7k | **Khó nhất batch**: ts_backend_analyzer + ts_frontend (detector fullstack/frontend), 4k+ ts + 5k js files trong stock thật; tree-sitter typescript/tsx grammar |
| `tools/js/` | 2.0k | express_js detection để dành cho Phase 08 |
| `tools/php/` | 1.9k | tree-sitter-php |
| `tools/perl/` | 2.0k | regex-based nhiều hơn tree-sitter (Python bảnPerl dùng gì port y vậy) |
| `tools/shell/` | 0.9k | ShellFunction/ShellScript/ShellInvocation — đã thấy chạy trên stock |

## Thứ tự trong batch

shell (nhỏ, nóng via stock) → js → php → perl → ts (để cuối vì detector phức tạp nhất).

## Parity per-analyzer (bắt buộc trước khi sang analyzer tiếp theo)

- Harness Phase 04 chạy dual trên: stock thật (có js/ts/php/shell thật) + testdata riêng
  của analyzer đó (`code-tiny/testdata`, tests/ của từng analyzer).
- Node/rel diff = rỗng (mask chuẩn); incremental changed-manifest path test 1 lượt.

## Gate

- [ ] 5/5 analyzer pass parity trên stock + testdata riêng.
- [ ] `dev sync code all --parsers <từng ngôn ngữ>` chạy được với backend Rust qua
      `CORTEX_RUST_ANALYZER=rust` (orchestrator Python vẫn điều phối —Phase 09 mới port orchestrator).
- [ ] Dogfood: 1 kỳ sync thật trên stock với 5 analyzer Rust, summary JSON khớp Python run trước.
