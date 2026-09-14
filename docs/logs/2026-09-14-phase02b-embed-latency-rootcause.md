# Phase-02b: root-cause + fix regression latency onnx backend — 2026-09-14

## Context

Plan `260914-1706-onnx-embedding-spike` phase-02 chặn flip `CORTEX_EMBED_BACKEND`
bởi 2 thứ: (a) drift score 1e-7 chờ P04 re-baseline (deferred, decision #7 vì
dogfood) và (b) regression latency +25-31ms/tool-call của onnx backend trong MCP
server thật, khi đó "chưa giải thích được". Session này đóng (b) — việc duy nhất
không bị chặn bởi window dogfood.

## Change

- Root-cause (đo từng bước, loại trừ spin/threads/deterministic/mutex/raw-TCP/
  tokio-blocking): stall ~45ms nằm ở **GET `/collections`** — request nhỏ trên
  connection keep-alive được reuse sau idle ~20-50ms dính delayed-ACK/Nagle qua
  ssh port-forward colima (qdrant chạy Docker trong VM). Onnx embed nhanh (~19ms)
  tạo đúng gap dính stall; worker Python (~42ms) vô tình né được → "regression"
  thực chất là tương tác thời gian, không phải ORT.
- Fix: `cortex-mcp/src/mind/qdrant.rs:194` `collection_names_cached()` — cache TTL
  30s (env `CORTEX_MCP_QDRANT_LIST_TTL_MS`, 0=off) cho availability list của
  search path; tool-facing `list_qdrant_collections` không cache.
- Instrumentation env-gated `CORTEX_EMBED_TRACE`: stage timing trong
  `cortex-embed/src/onnx.rs:250` (tokenize/run/pool), `mind/embed.rs` (lock/embed),
  `mind/qdrant.rs` (send/read), `mind/tools.rs` (embed/qdrant/body).
- Knob `CORTEX_EMBED_ORT_SPIN` + `SessionConfig.spin`
  (`cortex-embed/src/onnx.rs:29`) — giữ làm benchmark knob sau khi đo chứng minh
  spin không phải nguyên nhân.
- Harness mới: `scripts/rust_parity/bench_mind_server.py` (bench end-to-end ma
  trận env, guard port zombie), `cortex-mcp/examples/latency_repro.rs` (repro
  V1-V6).

## Impact

- Onnx backend giờ **nhanh nhất end-to-end**: p50/p95 25.1/26.1ms vs python ref
  50.3/51.6ms và worker 47.0/48.0ms (trước fix: 71.7/79.3ms).
- Gates: `embed_golden` 3/3 (spin on lẫn off — số học không đổi);
  `compare_mind.py` backend python 38/38 (rollback byte-equivalent); backend onnx
  24/38 với `latency P95` PASS và 14 fail còn lại đúng là drift 1e-7 chờ P04.
- Risk thấp-movie: cache TTL có thể làm search với scope mới bỏ qua collection
  vừa tạo trong ≤30s (fixture static nên parity không đổi); default mọi backend
  vẫn `python` — flip vẫn chờ P04 theo decision #7.

## Decision

Cache TTL thay vì `Connection: close` hay đảo thứ tự request: bỏ hẳn 1 round-trip
mỗi search (thắng cả ở RTT thật), fixture static nên byte-identical, và biến
staleness chỉ nằm ở metadata availability — không nằm ở kết quả search. Đã cân
nhắc fix tại transport (nodelay trên socket ta — vô ích vì Nagle nằm ở ssh mux
của colima) và cache toàn cục cho cả tool-facing list (loại vì đổi hành vi tool).

## References

- plan: ./plans/260914-1706-onnx-embedding-spike/plan.md (bảng trạng thái P02)
- report: ./plans/260914-1706-onnx-embedding-spike/reports/phase02b-latency-rootcause.md
- commit: 3dacc26
