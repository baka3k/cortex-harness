#!/usr/bin/env python3
"""Phase-13 mind tool parity — shared case set + server environment.

Cases run against BOTH the live Python mind server (`doc-tiny/
mcp_graph_rag.py`, launched by `record_mind.py`) and the Rust mind flavor
(`cortex-mcp --server mind`, launched by `compare_mind.py`) with an
IDENTICAL environment:

* `CORTEX_HARNESS_CONFIG_PATH` → `fixtures/mind_config` (project `mindfix`,
  storage_backend=remote → qdrant http://127.0.0.1:6333 + FalkorDB
  redis://127.0.0.1:6379, collection/graph `mindfix_doc` — populated once by
  `ingest_mind_fixture.py`);
* `CORTEX_DATA_HOME` → `fixtures/mind_local_store` — a FRESH instance root so
  the local-embedded fallback paths (unscoped queries, unregistered project
  ids) resolve to an empty deterministic store on both sides;
* `FALKORDB_URI`/`FALKORDB_GRAPH` env (env-seeded base store);
* `PYTHONHASHSEED=0`, `EMBEDDING_MODEL=BAAI/bge-m3`, `EMBEDDING_DEVICE=cpu`.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

PROJECT = "mindfix"
COLLECTION = "mindfix_doc"

MIND_TOOLS = [
    "list_source_ids",
    "list_qdrant_collections",
    "semantic_search",
    "query_graph_rag_langextract",
    "get_paragraph_text",
]


def server_env(port: int) -> dict[str, str]:
    """Environment shared by the Python record server and Rust server."""
    return {
        "CORTEX_HARNESS_CONFIG_PATH": str(FIXTURE_DIR / "mind_config"),
        "CORTEX_DATA_HOME": str(FIXTURE_DIR / "mind_local_store"),
        "CORTEX_STORAGE_INSTANCE": "default",
        "FALKORDB_URI": "redis://127.0.0.1:6379",
        "FALKORDB_GRAPH": COLLECTION,
        "PYTHONHASHSEED": "0",
        "EMBEDDING_MODEL": "BAAI/bge-m3",
        "EMBEDDING_DEVICE": "cpu",
        "MCP_SERVER_NAME": "mind_mcp",
        "FASTMCP_PORT": str(port),
    }


def mind_cases() -> list[dict]:
    """Deterministic mind-tool cases (id, tool, arguments, options)."""
    scoped = {"project_id": PROJECT}
    cases: list[dict] = [
        {
            "id": "list_qdrant_collections.unscoped",
            "tool": "list_qdrant_collections",
            "arguments": {},
        },
        {
            "id": "list_qdrant_collections.scoped",
            "tool": "list_qdrant_collections",
            "arguments": dict(scoped),
        },
        {
            "id": "list_qdrant_collections.prefix",
            "tool": "list_qdrant_collections",
            "arguments": {"project_id": "mind"},
        },
        {
            "id": "list_source_ids.default",
            "tool": "list_source_ids",
            "arguments": {},
        },
        {
            "id": "list_source_ids.limit2",
            "tool": "list_source_ids",
            "arguments": {"limit": 2},
        },
        {
            "id": "list_source_ids.scoped",
            "tool": "list_source_ids",
            "arguments": {"limit": 10, "project_id": PROJECT},
        },
        {
            "id": "list_source_ids.zero_limit",
            "tool": "list_source_ids",
            "arguments": {"limit": 0},
        },
        {
            "id": "semantic_search.basic",
            "tool": "semantic_search",
            "arguments": {"query": "Digital Key 3.0 dùng chuẩn gì?", **scoped},
        },
        {
            "id": "semantic_search.topk20",
            "tool": "semantic_search",
            "arguments": {"query": "Qdrant vector search HNSW cosine", "top_k": 20, **scoped},
        },
        {
            "id": "semantic_search.source_filter",
            "tool": "semantic_search",
            "arguments": {
                "query": "graph database Cypher operations",
                "source_id": "mindfix__falkordb-ops.md",
                **scoped,
            },
        },
        {
            "id": "semantic_search.entity_mentions",
            "tool": "semantic_search",
            "arguments": {
                "query": "n8n webhook JWT authentication",
                "include_entity_ids": False,
                "include_entity_mentions": True,
                **scoped,
            },
        },
        {
            "id": "semantic_search.max_passage_chars",
            "tool": "semantic_search",
            "arguments": {
                "query": "DeerFlow system architecture planner researcher",
                "max_passage_chars": 120,
                **scoped,
            },
        },
        {
            "id": "semantic_search.unscoped",
            "tool": "semantic_search",
            "arguments": {"query": "FalkorDB backup strategy"},
        },
        {
            "id": "semantic_search.explicit_collection",
            "tool": "semantic_search",
            "arguments": {"query": "Plug&Charge xác thực", "collection": COLLECTION},
        },
        {
            "id": "semantic_search.missing_collection",
            "tool": "semantic_search",
            "arguments": {"query": "x", "collection": "no_such_collection_p13"},
        },
        {
            "id": "semantic_search.unregistered_project",
            "tool": "semantic_search",
            "arguments": {"query": "test query", "project_id": "mindfix_unknown_zz"},
        },
        {
            "id": "semantic_search.missing_query",
            "tool": "semantic_search",
            "arguments": {},
        },
        {
            "id": "query_graph_rag.basic",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "Chuẩn ISO 15118 và Plug&Charge hoạt động thế nào?",
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.depth2",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "FalkorDB Qdrant DeerFlow kiến trúc",
                "graph_depth": 2,
                "related_k": 100,
                **scoped,
            },
            # Hop ≥ 2 builds the frontier from a Python set (hash-iteration
            # order): relation ROW ORDER is implementation-defined — compare
            # as multiset (same rows, tolerance on scores unchanged).
            "relations_multiset": True,
        },
        {
            "id": "query_graph_rag.rerank",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "DeerFlow chạy trên hạ tầng nào?",
                "rerank": True,
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.rerank_weights",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "Digital Key 3.0 của hãng xe nào?",
                "rerank": True,
                "rerank_entity_weight": 0.2,
                "rerank_type_weight": 0.05,
                "rerank_confidence_weight": 0.5,
                "rerank_length_penalty": 0.001,
                "entity_types": "ORG,STANDARD",
                "top_k": 8,
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.entity_types_list",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "webhook JWT SSI FastConnect",
                "entity_types": ["ORG", "TECH"],
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.no_expand",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "Qdrant quantization on-disk payload index",
                "expand_related": False,
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.no_entities",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "Grafana Loki monitoring",
                "include_entities": False,
                "include_relations": False,
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.min_score_to_expand",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "Digital Key 3.0",
                "min_score_to_expand": 2.0,
                **scoped,
            },
        },
        {
            "id": "query_graph_rag.min_entity_occurrences",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": "FalkorDB và Qdrant trong DeerFlow",
                "min_entity_occurrences": 3,
                **scoped,
            },
        },
        {
            "id": "get_paragraph_text.found",
            "tool": "get_paragraph_text",
            "arguments": {
                "source_id": "mindfix__digital-key-overview.md",
                "paragraph_id": 0,
                **scoped,
            },
        },
        {
            "id": "get_paragraph_text.missing",
            "tool": "get_paragraph_text",
            "arguments": {
                "source_id": "mindfix__digital-key-overview.md",
                "paragraph_id": 999,
                **scoped,
            },
        },
        {
            "id": "get_paragraph_text.no_source_id",
            "tool": "get_paragraph_text",
            "arguments": {},
        },
        {
            "id": "get_paragraph_text.string_paragraph_id",
            "tool": "get_paragraph_text",
            "arguments": {
                "source_id": "mindfix__guides__security-baseline.md",
                "paragraph_id": "0",
                **scoped,
            },
        },
    ]
    return cases


# Float keys compared with an absolute tolerance (vector-lane phase-02
# re-baseline: 1e-9 là gate phase-13 với embedder python cả 2 phía; từ khi
# ONNX là backend embed mặc định, drift chữ số thứ 7 ~1e-7 giữa ort và torch
# là đặc tính đã đo ở plans/260914-1706 phase-01/02 — tolerance 1e-6 bao trọn).
TOLERANCE_KEYS = {"score", "rerank_score", "confidence", "graph_proximity"}
TOLERANCE = 1e-6
