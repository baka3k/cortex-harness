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

- [x] 5/5 analyzer pass parity trên stock + testdata riêng.
- [x] `dev sync code all --parsers <từng ngôn ngữ>` chạy được với backend Rust qua
      `CORTEX_RUST_ANALYZER=rust` (orchestrator Python vẫn điều phối —Phase 09 mới port orchestrator).
- [x] Dogfood: sync thật qua orchestrator với analyzer Rust, SCAN_RESULT + graph khớp Python run trước.

**Trạng thái 2026-09-14:** PASS toàn bộ gate.

- 5/5 analyzers: shell (`analyzer_parity_shell.py` — stock 24 .sh + testdata + incremental
  + program-mapping ledger, cleanup 3=3), ts (`analyzer_parity_ts.py` — testdata + stock
  frontend 76 ts/tsx + incremental với impacted-by-imports BFS), js, php, perl — mỗi
  analyzer: FULL diff 0 ngoài mask, incremental cleanup counts khớp, [SCAN_RESULT]
  byte-identical. Báo cáo trong reports/phase05-*.md.
- shlex port byte-exact (state machine read_token CPython, golden probes); perl phát hiện
  parser là tree-sitter-perl 1.2.1 (vendor C sources, không phải regex thuần).
- Orchestrator swap: `_build_analyzer_cmd` nhận `CORTEX_RUST_ANALYZER=rust` → resolve
  binary `rust/target/release/analyzer-<parser>` (map parser đã port), fail-safe fallback
  Python; `--falkordb-path` embedded fail-closed phía Rust (dùng remote URI cho dogfood).
- Dogfood end-to-end qua orchestrator: mixed corpus py+shell+ts — SCAN_RESULT byte-identical
  giữa 2 backend; dogfood graph diff = 0 (mask chuẩn).
- Grammar pins trong rust/grammar-versions.toml (js 0.25, ts 0.23, php 0.24, java 0.23.5,
  kotlin-ng 1.1.0).
