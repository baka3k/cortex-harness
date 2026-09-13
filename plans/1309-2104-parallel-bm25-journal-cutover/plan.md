---
title: "Cutover song song: BM25 auto-corpus (query path) + Journal Rust shadow (ingest path)"
status: planned
created: 2026-09-13
target: "code-tiny/tools/common, code-tiny/mcp/services, code-tiny/tools/graph/journal, rust/crates, scripts/rust_parity"
blockedBy: []
blocks: []
relatedPlans:
  - "260913-1715-rust-retrieval-graph-port"
  - "260913-1538-ladybug-graph-provider"
---

# Cutover song song: BM25 query-path + Journal Rust shadow

## Overview

Hai track **độc lập hoàn toàn về file** chạy song song, nối tiếp kết quả của
rust_bridge (commit `1f49e61`) — Rust đã chạy trong query path nhưng BM25
signal vẫn chưa fire vì không có corpus, và journal write core đã port
(phase-05/07 DONE) nhưng chưa có bằng chứng trên ingest thật:

- **Track A — BM25 auto-corpus** (`phase-A1`, `phase-A2`): bật bm25 signal
  thật trong `IntelligentRetrievalEngine` bằng corpus build từ seed
  candidates, flag tắt được, đo lường trước/sau.
- **Track B — Journal Rust shadow** (`phase-B1`, `phase-B2`, `phase-B3`):
  capture op-stream của ingest Python, replay qua journal write core Rust,
  diff 2 store — bằng chứng cutover mà không đổi behavior.

Tôn trọng decision record phase-06 của plan cha: biên tích hợp là
**native Rust** (PyO3 defer) → Track B dùng replay CLI, không thêm FFI mới.

## Ma trận file (bằng chứng song song an toàn)

| Track | File đụng | Không đụng |
|---|---|---|
| A | `code-tiny/tools/common/intelligent_retrieval.py`, `code-tiny/mcp/services/explore_service.py`, `code-tiny/tests/`, `.github/workflows/`, wiki/help | `tools/graph/journal/*`, `rust/*`, `scripts/rust_parity/*` |
| B | `code-tiny/tools/graph/journal/*`, `rust/crates/cortex-graph-driver` (bin mới), `scripts/rust_parity/`, `Makefile` | `tools/common/*`, `mcp/services/*`, `.github/*` |

Điểm chạm duy nhất: cả 2 chạy gate `make rust-check` / `make rust-pyo3`.
Merge order: **A trước** (nhỏ, user-visible nhanh) → B rebase → merge.

## Git strategy

- 2 branch từ `feat/change-db`: `feat/bm25-auto-corpus` (A) và
  `feat/journal-rust-shadow` (B).
- 1 người vẫn làm song song được vì file-disjoint; nếu muốn 1 branch chung
  thì commit xen kẽ cũng an toàn (không file trùng).

## Non-goals (out of scope)

- Flip Rust thành primary journal writer (thuộc plan native-binary sau này —
  B3 chỉ ra decision record + benchmark đầu vào).
- Migration `.rdb` → `.lbug` cho instance cũ (thuộc ladybug full-cutover).
- Port ML sang ONNX.
- Đổi weight profile / behavior ranking ngoài việc bật bm25 weight 0.15
  đã thiết kế sẵn.

## Risks & gates

| Risk | Severity | Mitigation |
|---|---|---|
| Bật bm25 đổi kết quả search gây bất ngờ | Medium | Mặc định OFF (`CORTEX_BM25_AUTO=0`) ở A1; A/B ranking report trước khi flip ON; rollback = env |
| BM25 trên identifier text (`qualified_name`) khác hành vi prose | Medium | Tokenizer `[a-z0-9_]+` sinh cho identifier — golden test A1 chốt corpus text trước |
| Capture shadow làm chậm ingest | Low | JSONL append-only, env-gated, mặc định OFF |
| Op-stream format drift giữa Python và fixture Rust | Medium | Pin `schema_version` của journal op; replay test chạy fixture committed |
| Conflict Makefile/workflow khi 2 track merge | Low | Merge order A→B; B chỉ đụng Makefile |

## Verification strategy (chung cả 2 track)

- `make rust-check` (clippy -D warnings + cargo test) mỗi lần đụng `rust/`.
- `make rust-pyo3` — parity gate sau khi build lại extension.
- `PYTHONPATH=code-tiny pytest code-tiny/tests -q` — full suite xanh.
- Track A thêm: A/B ranking report trước/sau bật flag trên query set thật.
- Track B thêm: `check_journal_roundtrip.py` + diff store pass trên fixture
  VÀ trên capture ingest thật ≥ 1 project.
