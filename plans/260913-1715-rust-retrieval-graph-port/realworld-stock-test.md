# Real-world test: repo /Users/hieplq1.aip/baka3k/stock (2026-09-13)

Script: `scripts/rust_parity/realworld_stock_test.py` — chạy từ repo root:
`.venv/bin/python scripts/rust_parity/realworld_stock_test.py`

## Scope

Sample bounded từ repo thật (loại venv/pyc/build): **116 files .py, 263 symbols**
(packages `stock_backend`, `stock_bot`, `stock_mcp`).

## Kết quả: 9/9 PASS

| Check | Ý nghĩa |
|---|---|
| journal: 1 run ghi từ dữ liệu stock thật | `SQLiteJournal` Python ghi run/batches/barrier bằng artifacts từ file thật |
| journal: Rust inspect khớp Python | Rust `inspect_journal` đọc cùng DB, field-by-field khớp (mask age/bytes) |
| journal: batches DONE 2/2 | state machine hoàn tất production→consumption trên dữ liệu thật |
| retrieval: parity 6 queries thật | BM25 + intent + query-understanding: Python ↔ PyO3 khớp tuyệt đối trên text có dấu tiếng Việt + identifier thật |
| retrieval: sanity relevance 3/3 | top-hit đúng domain (portfolio/snapshots/analyze_stock) |
| ladybug: Rust đọc 101 File node thật | store do Python `ladybug` tạo từ inventory thật |
| ladybug: node Rust ghi đọc lại được từ Python | roundtrip ghi 2 chiều |

## Findings từ real-world test

1. **ladybug Python `close()` không nhả OS flock** — chỉ nhả sau GC (`del` + `gc.collect()`); reopen cùng process có thể "thành công" nhờ handle nội bộ và dễ tưởng nhầm đã nhả lock. Cross-language thì bắt buộc chờ process thật sự release.
2. **WAL readonly:** DB WAL-mode mở readonly cần `-shm` creatable — trước khi đưa DB cho Rust inspect, checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`) rồi copy file.
3. **BM25 tokenizer thật:** `[a-z0-9_]+` gộp CamelCase (`PortfolioService` → 1 token) và underscore identifiers (`weighted_scorer` → 1 token) — query match thực tế đến từ module-path tokens; lưu ý khi thiết kế relevance expectation.
