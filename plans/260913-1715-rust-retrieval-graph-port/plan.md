---
title: "Rust port CortexHarness — retrieval brain trước, graph core sau (driver lbug)"
status: implemented-phases-01-07
created: 2026-09-13
target: "rust/ (workspace mới), code-tiny/tools/common + code-tiny/tools/graph (tham chiếu parity, không xoá)"
blockedBy: []
blocks: []
relatedPlans:
  - "260913-1538-ladybug-graph-provider"
predictionReport: "reports/prediction_report_20260913-1553.md"
---

# Rust port: retrieval brain + graph core

## Overview

Port phần **thuần thuật toán** của CortexHarness sang Rust theo chiến lược strangler-fig, xuất phát từ hi-predict CAUTION (full rewrite = STOP). Thứ tự port được chọn theo giá trị/rủi ro:

1. **Retrieval brain** (`tools/common/`): BM25 ranker, signal normalizer, query understanding, fusion engine — pure logic, golden-test được từng điểm số.
2. **Graph core** (`tools/graph/`): journal (SQLite), schema manifest, writers, operations.
3. **Driver LadybugDB** qua crate `lbug` chính thức — **cùng engine, cùng on-disk format** với Python side đang adopt theo plan `260913-1538-ladybug-graph-provider` → không có migration giữa 2 ngôn ngữ.

ML (jina-v3, bge-m3, GLiNER) **out of scope** — ở Python ML sidecar theo addendum `plans/prediction_addendum_20260913-graph-scope.md`.

## Non-goals (out of scope)

- Port ML models sang Rust (ONNX là plan riêng, sau khi hybrid ổn định).
- Xoá/thay Python modules — dual-run, Python là source of truth đến khi parity gate pass.
- Port MCP server layer (FastMCP + torch ở query services) — giữ Python.
- ETL data cũ falkordblite .rdb (thuộc full-cutover plan của ladybug).

## Phase map

| Phase | Scope | Key files |
|---|---|---|
| 01 | Cargo workspace `rust/` + golden-fixture pipeline (Python → JSON) | `rust/Cargo.toml`, `scripts/rust_parity/gen_*.py` |
| 02 | BM25 ranker port (BM25Okapi k1=1.5, b=0.75, ε=0.25) + golden parity | `rust/crates/cortex-retrieval/src/bm25.rs` |
| 03 | signal_normalizer + query_understanding + query_intent_classifier port | `rust/crates/cortex-retrieval/src/{normalize,query_understanding}.rs` |
| 04 | IntelligentRetrievalEngine fusion port (trait `CandidateSource`, weighted blend) | `rust/crates/cortex-retrieval/src/fusion.rs` |
| 05 | Graph core port: journal (rusqlite) + schema manifest | `rust/crates/cortex-graph-core/` |
| 06 | Spike driver `lbug` (Rust) + quyết định biên tích hợp (PyO3/subprocess/native) | `rust/crates/cortex-graph-driver/` |
| 07 | Journal write core (low-level loop) + PyO3 bindings retrieval brain | `rust/crates/cortex-retrieval-py/`, `journal.rs` |

## Key architectural decisions

1. **Parity-first:** mỗi module port phải có golden fixtures sinh từ code Python tham chiếu bằng `uv run --with <dep>` (không đụng venv dự án), tolerance 1e-9 cho scores, so cả thứ tự ranking.
2. **Replicate exact semantics của rank_bm25:** query tokens lặp được cộng điểm mỗi lần xuất hiện; term lạ ngoài vocab contribution 0; drop raw ≤ 0 sau normalize theo max.
3. **`BM25Ranker` đang silent-disable trong venv hiện tại** (`rank_bm25` không cài) — cần quyết định riêng: thêm `rank_bm25` vào deps dự án hay chấp nhận BM25-off. Rust port luôn có BM25 (không có fallback missing-dep).
4. **Traits cho boundary:** data sources (qdrant/graph) đứng sau trait để fusion engine test offline; DB access thật ở phase 05+.
5. **Mọi phát hiện lệch dialect/semantics** ghi vào phase file tương tự quy trình ladybug plan.
6. **No `unsafe`, `cargo clippy -D warnings`** là gate cho mọi phase.

## Risks & gates

| Risk | Gate |
|---|---|
| Fixture drift khi rank_bm25 đổi version | Pin `rank_bm25==0.2.2` trong lệnh sinh fixture; commit fixtures vào repo |
| Float determinism khác thứ tự cộng | Replicate đúng thứ tự loop của Python trong golden test; tolerance 1e-9 |
| Unicode tokenizer edge (`.lower()` Python vs Rust `to_lowercase`) | Fixture gồm identifier thực tế + case unicode; lệch → ghi nhận và quyết minh hoạ |
| ladybug (PyPI) vs lbug (crates.io) version skew | Phase 06 spike pin cả 2 version, so schema/store format trên cùng file |
| Đụng ch Ladybug plan (đang in-flight phase 01→02) | Phase 05/06 `phaseBlockedBy` ladybug plan; retrieval phases độc lập |

## Verification strategy

- `cargo test` (unit + golden fixtures) + `cargo clippy -- -D warnings` mỗi phase.
- Fixture generator script commit vào `scripts/rust_parity/`, chạy lại bằng `uv run --with rank_bm25==0.2.2`.
- Phase 04: golden ranking parity trên sample candidate set (qdrant hits + graph keyword hits + BM25) — kết quả cuối so kết quả Python engine.
- Phase 06: mở store file do Python ladybug driver ghi bằng Rust driver (ngược lại) — smoke read/write.

## Active-plan coordination

- `260913-1538-ladybug-graph-provider` (in-progress phase 01): sở hữu `tools/graph/driver/ladybug_driver.py`, `storage/*`. Plan này **không sửa** các file đó; phase 05/06 phụ thuộc output layout/schema của nó (`phaseBlockedBy`).
- Các plan retrieval-related khác (`simplify-search-full-removal`…) không sở hữu `tools/common/{bm25_ranker,intelligent_retrieval,signal_normalizer,query_understanding}.py` ở thời điểm scan.
