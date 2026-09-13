# Phase 01: Cargo workspace + golden-fixture pipeline

## Context

Dựng nền cho toàn bộ port: workspace Rust tại `rust/` và cơ chế sinh golden fixtures từ code Python tham chiếu **không đụng venv dự án** (dùng `uv run --with <dep>` để inject dependency ephemerally).

## Requirements

- `rust/Cargo.toml`: workspace, resolver 2, members = `crates/*`.
- `rust/crates/cortex-retrieval/`: crate đầu tiên (retrieval brain), edition 2024, `#![deny(unsafe_code)]`, dev-deps: `serde`, `serde_json`.
- Fixture generator tại `scripts/rust_parity/gen_bm25_fixtures.py`:
  - Tự thêm `code-tiny` vào `sys.path`, import `tools.common.bm25_ranker` (tham chiếu thật, không copy code).
  - Chạy bằng: `uv run --with rank_bm25==0.2.2 python scripts/rust_parity/gen_bm25_fixtures.py` từ repo root.
  - Input: các sample doc set (identifier thực tế, có unicode case, doc rỗng, doc trùng term) + query set (term khớp, term lạ, query rỗng, term lặp).
  - Output JSON: `{"documents": [...], "text_field": ..., "id_field": ..., "cases": [{"query": ..., "expected": {symbol_id: score}}]}` vào `rust/crates/cortex-retrieval/tests/fixtures/bm25_golden.json`.
  - Fixtures commit vào repo (drift detection = diff).
- `cargo test` xanh với 1 placeholder test đọc được fixture (sanity parse).

**Trạng thái: DONE 2026-09-13.**

## Gates

- [x] Workspace compile, `cargo clippy -- -D warnings` sạch.
- [x] Fixture JSON generated + committed, parse được từ Rust.
- [x] Generator script chạy reproducible (chạy 2 lần → cùng output).
