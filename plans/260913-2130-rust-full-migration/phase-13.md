# Phase 13 — WAVE F3: MCP mind tools + semantic expansion (ONNX decision)

## Scope

| Tool | Nguồn | Ghi chú |
|---|---|---|
| semantic_search | mind_mcp | đã thấy chạy thật (Vietnamese → SSI docs); vector-only path: qdrant client Rust (Phase 09 đã có adapter) + collection resolution per project |
| query_graph_rag_langextract | `mcp_graph_rag.py` (doc-tiny) + mind_mcp wrapper | vector search + graph expansion (expand_related, min_score_to_expand) + heuristic rerank (`_compute_heuristic_rerank_score` — entity/type/confidence weights, đã phân tích: pure math) + entity output từ GLiNER store |
| list_source_ids / get_paragraph_text | doc store access | |
| semantic_graph_expansion | 260 LOC, **torch tại query-time** | embed query + similarity expansion |

## Decision: semantic expansion runtime (chốt trong phase)

- **A. ONNX `ort` (mục tiêu):** jina-v3 int8 ONNX + `tokenizers` crate cho query embedding
  lúc query-time; parity gate cosine ≥ 0.999 vs Python torch trên bộ query thật (đã có ở
  addendum GLiNER/Jina strategy). Lợi ích: bỏ torch khỏi runtime MCP, single binary.
- **B. Fallback (không chặn): embedding sidecar Python** (ML worker từ phase-07-1715 plan)
  — Rust gọi qua stdio JSON; torch chỉ tồn tại trong sidecar.

GLiNER (entities cho GraphRAG): giữ **Python sidecar** (không có ONNX export chính thức);
contract `{text, labels, threshold} → [{entity, type, score, span}]` như addendum.

## Parity

- Golden fixtures mind tools trên stock_doc (164 points thật) + procsample_doc:
  passages + scores (1e-9) + entities (GLiNER sidecar → byte-match vì cùng code).
- `entities` GLiNER taxonomy quirk đã thấy (SSI → type CRYPTO) — pin đúng hành vi.

## Gate

- [ ] mind tools pass golden contract byte-level (trừ scores tolerance 1e-9).
- [ ] Decision record A/B: nếu A — parity vectors + benchmark; nếu B — sidecar contract test.
- [ ] Query latency P95 ≤ Python (đo trên stock_doc + collection lớn hơn nếu có).
