# Phase 06: macOS dogfood + parity gate + benchmark smoke

## Context

Phase track-2: user dogfood ladybug trên macOS (opt-in) để chạy daily flow thật (sync_processes, MCP servers, doc ingest). Phase này cũng là gate quyết định có mở plan "full cutover macOS/Linux default" hay không (hi-predict verdict: full cutover vẫn CAUTION cho đến khi phase này qua).

## Requirements

### Dogfood run (macOS arm64, máy dev)
1. `GRAPH_PROVIDER=ladybug LADYBUG_PATH=<dev-root>` ingest 1 repo thật (medium-size).
2. Chạy daily flow: `sync_processes` → MCP queries (`explore_graph`, `search_by_code`, `find_paths`...) → doc ingest.
3. Ghi nhận: lỗi dialect, auto-DDL trigger counts, latency cảm nhận.

### Parity harness
- Script `scripts/parity_ladybug.py` (hoặc pytest module): ingest cùng 1 sample repo vào 2 providers (falkordblite + ladybug, 2 storage roots) → so sánh:
  - node count per label, rel count per type (`driver.list_labels()` / `list_relationship_types()`).
  - Output của bộ query MCP đại diện (search/traverse/impact) — so normalized JSON.
- Chạy được chọn lọc: `python -m pytest tests/test_parity_ladybug.py -m ladybug`.

### Benchmark smoke (gate, không phải tối ưu)
- So **2 write path** trên dataset parity: (a) MERGE row-by-row qua driver, (b) bulk `COPY FROM DataFrame` (phase-04 fast path) — kỳ vọng (b) nhanh hơn đáng kể cho initial ingest.
- Read p50/p95 cho 5 câu query đại diện (traverse depth 2, fulltext, by-id) falkordblite vs ladybug.
- Kỳ vọng hi-predict: read flat→tốt hơn; nếu ingest ladybug chậm >3x → ghi nhận blocker cho full-cutover, không block phase này (win32 vẫn ship). Nếu COPY path nhanh ≥5x so với MERGE path → đánh dấu "bulk ingest recommended default" cho generation mới trong results file.

### CI
- `.github/workflows/lifecycle-macos.yml`: thêm job/step cài ladybug + `pytest -m ladybug` (opt-in suite) để không regress dialect.

### Decision record
- Ghi kết quả parity + benchmark vào `plans/260913-1538-ladybug-graph-provider/phase-06-results.md` → input cho plan full-cutover sau này (ETL `.rdb`, bundle v2, flip default POSIX).

## Implementation steps

1. Viết parity harness + sample repo fixture.
2. Chạy parity, sửa chênh lệch (thường nằm ở normalization `_normalize_ladybug_value` + ranking FTS).
3. Benchmark smoke script + kết quả vào results file.
4. CI wiring + docs cập nhật "macOS opt-in" vào ReadMe (Phase 05 docs).

## Acceptance

- Parity: node/rel counts khớp 100%; query output khớp trừ ranking FTS (đã document).
- Dogfood 1 ngày không có P0/P1 lỗi nào.
- Results file tồn tại với benchmark table + go/no-go recommendation cho full cutover.

## Out of scope (chuyển sang plan full-cutover tương lai)

- ETL `.rdb` → ladybug, `cortex migrate` command.
- Bundle `.cortexdb` v2 (ladybug directory) + importer dual-format.
- Flip default `GRAPH_PROVIDER` trên macOS/Linux.
