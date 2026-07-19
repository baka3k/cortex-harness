# Project-Scoped Semantic Retrieval — 2026-07-19

## Context

Semantic collections and graph databases can contain facts from multiple projects. Although MCP tools accepted `project_id`, vector and explore paths did not consistently enforce it, so a scoped query could admit foreign-project Qdrant hits, keyword candidates, BM25-only candidates, or graph-expanded neighbors.

## Change

- A shared helper now normalizes project IDs, builds the canonical Qdrant payload filter, and provides a final candidate-scope predicate (`code-tiny/tools/common/project_scope.py:8`, `code-tiny/tools/common/project_scope.py:16`, `code-tiny/tools/common/project_scope.py:28`).
- Server-side Qdrant filtering is applied in all four semantic backends—generic/fast, CPlus, Android, and Java—and in explore retrieval (`code-tiny/mcp/fastmcp_server.py:751`, `code-tiny/mcp/cplus/cplus_mcp.py:707`, `code-tiny/mcp/android/android_mcp.py:791`, `code-tiny/mcp/java/java_mcp.py:617`, `code-tiny/tools/common/intelligent_retrieval.py:200`).
- Explore propagates one normalized scope through Qdrant and graph keyword retrieval, prevents scoped BM25-only hits from creating unverified candidates, constrains seed/neighbor graph expansion, and applies a final defensive filter before scoring (`code-tiny/tools/common/intelligent_retrieval.py:564`, `code-tiny/tools/common/intelligent_retrieval.py:585`, `code-tiny/tools/common/intelligent_retrieval.py:610`, `code-tiny/tools/common/graph_expander.py:133`, `code-tiny/tools/common/graph_expander.py:136`, `code-tiny/tools/common/intelligent_retrieval.py:636`). Semantic-result expansion likewise scopes seeds, neighbors, edge sources, and edge targets (`code-tiny/mcp/semantic_graph_expansion.py:137`, `code-tiny/mcp/semantic_graph_expansion.py:141`, `code-tiny/mcp/semantic_graph_expansion.py:215`, `code-tiny/mcp/semantic_graph_expansion.py:216`).
- Tests cover server-side filters across four backends and all semantic modes, explore Qdrant filtering, keyword/graph propagation, foreign and BM25-only rejection, service forwarding, and scoped semantic graph edges (`tests/test_qdrant_project_scope.py:32`, `tests/test_qdrant_project_scope.py:57`, `tests/test_qdrant_project_scope.py:75`, `tests/test_explore_project_scope.py:48`, `tests/test_explore_project_scope.py:60`, `tests/test_explore_project_scope.py:76`, `tests/test_explore_project_scope.py:123`, `tests/test_semantic_graph_expansion.py:15`).

## Impact

Scoped semantic and hybrid queries no longer mix candidates from other projects across vector, keyword, BM25, or graph paths; unscoped calls retain their prior behavior. **Risk level: medium** because correctness depends on ingested points and graph nodes carrying an accurate `project_id`; scoped retrieval intentionally excludes missing or mismatched metadata, which can reveal legacy ingestion gaps as fewer results.

## Decision

Enforce project scope at the earliest provider query and retain downstream defenses instead of relying on collection names or a single final filter. Server-side Qdrant and graph predicates reduce leakage and wasted ranking work, BM25 is allowed to enrich only already-scoped candidates, and the final candidate predicate protects against incomplete adapters or future retrieval signals. Keep `project_id` optional to preserve backward-compatible unscoped searches.

## References

- Shared scope contract: `code-tiny/tools/common/project_scope.py:8`
- Explore service propagation: `code-tiny/mcp/services/explore_service.py:277`, `code-tiny/mcp/services/explore_service.py:347`
- Retrieval regression tests: `tests/test_qdrant_project_scope.py:32`, `tests/test_explore_project_scope.py:48`
- Implementation commit: `05d1ecda0bd1795e49b86614ee672d078c4474e0`
