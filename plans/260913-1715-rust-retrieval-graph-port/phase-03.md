# Phase 03: signal_normalizer + query_understanding + query_intent_classifier port

## Context

Port 3 module pure-logic còn lại của retrieval brain (~900 LOC Python):
- `tools/common/signal_normalizer.py` (209 LOC) — normalize score [0,1].
- `tools/common/query_understanding.py` (513 LOC) — enrich `embedding_text` bằng domain keyword expansion (stdlib only, zero ML/HTTP).
- `tools/common/query_intent_classifier.py` — classify intent → weight profile.

## Requirements

- Port từng module sang `rust/crates/cortex-retrieval/src/{signal_normalize,query_understanding,intent}.rs`, giữ public API shape tương đương.
- Golden fixtures cho từng module sinh bằng `uv run` (không cần extra dep — stdlib only).
- Domain keyword tables (dict constants) port nguyên văn — sinh fixtures từ chính constants Python để tránh copy tay lệch.

## Gates

- [x] Golden tests xanh cho cả 3 module (score |diff| ≤ 1e-9, string output so exact).
- [x] `cargo clippy -- -D warnings` sạch.

## Phát hiện trong lúc port (2026-09-13)

1. **Dict-key collision `_TERM_TO_SIGNAL`:** "đơn hàng" nằm ở cả bảng payment lẫn order VI-terms — Python dict ghi đè value (order thắng vì khai báo sau), Vec naive của Rust giữ cả hai → thừa signal "payment". Đã replicate dict semantics (replace value, giữ vị trí đầu). Golden harness bắt được divergence này.
2. **Docstring `signal_normalizer.min_max_normalize` sai:** single value thực trả `[0.0]` (không phải `[1.0]` như docstring), ví dụ `[0.1,0.5,0.9,1.2]` thực trả `[0.0, 0.3636, 0.7273, 1.0]` (không phải `[0.0,0.4,0.8,1.0]`).
3. **Docstring `query_understanding.from_text` stale:** ví dụ `actions` ghi có `'login'` nhưng pattern `login` chỉ match "đăng nhập" (VI) — code thật trả `['process','payment']`.
4. `freshness_from_dirty` với ISO naive (không offset) Python **crash TypeError** (chỉ catch ValueError) — Rust trả 0.3 (documented divergence).
