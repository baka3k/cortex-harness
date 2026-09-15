# Phase 05 — Port message-scan native (red-team Critical #2)

## Vấn đề

Message-scan plane (graph `MessageEndpoint` merges + message qdrant vectors, logic tại `code-tiny/tools/common/message_scan.py` ~540+ LOC) **chỉ tồn tại trong Python children**. Rust children accept-and-ignore `--enable-message-scan`/`--message-*` (`cortex-analyzer-framework/src/cli.rs:98-99`, analyzer-java `main.rs:9`). 17 parsers message-enabled (`registry.rs:295-300`); `--sync-mode` default `both` → đây là default path. Không port = capability chết im lặng khi flip, và phase-08 xoá script khiến mọi force-Python policy vô hiệu.

## Quyết định (Validation Log #2)

Port native trong plan này — flip chỉ xảy ra khi parity pass.

## Thiết kế

Message-scan là **cross-parser plane** (nhận manifests từ mọi parser) → port ở tầng orchestrator/shared, không phải per-analyzer:

| Thành phần | Ghi chú |
|---|---|
| Message-scan module trong `cortex-sync` (hoặc util crate dùng chung) | Port logic `message_scan.py`: thu message records từ parser artifacts → merge `MessageEndpoint` graph nodes/rels (qua `cortex-graph-writer`) → message vectors |
| Message vectors | Phase này vẫn có thể ghi qua **Python embed sidecar tạm** (pin cũ) HOẶC chờ phase-06 cortex-embed — quyết định khi implement: nếu phase-06 pass trước thì message vectors dùng luôn cortex-embed; nếu phase-05 đi trước, message-vector lane pin Python sidecar và chuyển ở phase-06 (ownership GHI RÕ, red-team A7) |
| Parser side | Rust analyzers emit message records vào artifact (thay vì tự scan) — contract qua `cortex-analyzer-framework`; message-scan flags của children thành no-op có log |
| `--message-qdrant-collection` plumbing | Orchestrator giữ flag, dùng cho lane mới |

## Parity gate

1. Corpus có message endpoints thật (java/csharp/js/ts… theo message_enabled_parsers): dual-run children Python (baseline: graph + message vectors) vs rust children + native message-scan; graph diff 0 ngoài mask (gồm MessageEndpoint nodes/rels); message vector counts + cosine theo ngưỡng phase-06 (nếu dùng cortex-embed) hoặc byte-parity (nếu sidecar).
2. Incremental leg: file đổi → message endpoints stale được cleanup đúng (conservation).
3. Report `phase05-message-scan-parity.md`.

## Exit criteria

- Parity PASS; message-scan flags với rust children còn nghĩa (không còn accept-and-ignore).
- Ownership message vectors ghi rõ (sidecar tạm hay cortex-embed) — handoff mạch lạc sang phase-06.
- Fallback nếu FAIL: giữ message-enabled parsers trên Python children (mở rộng fallback end-state của plan) — dừng trước flip.
