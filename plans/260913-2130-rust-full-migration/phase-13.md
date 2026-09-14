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

- [x] mind tools pass golden contract byte-level (trừ scores tolerance 1e-9).
- [x] Decision record: **Plan B** — embedding sidecar Python (persistent worker
      `embed_worker.py`, newline-JSON stdio, cùng embedding_utils model/device → vectors
      identical); ONNX `ort` là spike sau, không chặn. GLiNER giữ Python sidecar
      (byte-identical cùng code — verify-once PASS).
- [x] Query latency P95: **Rust 49.8ms** (P50 48.7) vs Python 57.0ms (P50 52.6) trên
      fixture corpus — Rust nhanh hơn.

**Trạng thái 2026-09-14:** PASS toàn bộ — mind tools 30/30 call byte-parity, 5/5
tools/list metadata, initialize `mind_mcp`/1.29.0 khớp. Architecture: `--server mind`
flavor trong cortex-mcp (ureq REST qdrant client mirror cortex-storage::qdrant_remote,
FalkorDB store reads qua cortex-falkordb với Cypher byte-identical, heuristic rerank
math chính xác, FastMCP-signature error text). Regression: cortex-mcp 65/65 tests,
contract 106/106, graph 38/38, workspace 390 tests. Report:
reports/phase13-mind-tools-parity.md. Ghi chú tham chiếu: get_qdrant/get_neo4j bắt
nhầm class ProjectNotRegisteredError (cùng tên, khác module) — fallback dead code,
Rust replicate hành vi quan sát được.
