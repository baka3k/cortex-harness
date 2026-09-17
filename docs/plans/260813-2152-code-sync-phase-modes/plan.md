---
title: "Code Sync Graph and Embedding Phase Modes"
status: complete
created: 2026-08-13
mode: hi-plan --fast
scope: dev sync code CLI, incremental orchestrator phase ordering, summaries, tests, documentation
relatedPlans:
  - 260718-2159-incremental-scan-reliability
  - 260716-1615-primary-vector-ingestion-completion
  - 260807-1202-graph-ingest-write-path-hardening
---

# Code Sync Graph and Embedding Phase Modes

## Context

Primary analyzers currently write graph facts and then vectors inside one subprocess. `incremental_sync.py` waits for that subprocess to exit before framework overlays and `project_topology`, so slow or failed embedding delays topology and can leave MCP module queries empty even after base graph facts were persisted.

## Target contract

```text
dev sync code --full-scan --sync-mode both       # graph -> overlays -> topology -> embedding
dev sync code --full-scan --sync-mode graph      # graph -> overlays -> topology
dev sync code --full-scan --sync-mode embedding  # embedding only; never opens graph storage
```

- `--sync-mode` defaults to `both`.
- Existing `dev sync code` incremental behavior remains valid in `both` mode.
- Specialized `graph` and `embedding` modes require `--full-scan` until stage-specific incremental inventories are designed.
- Graph and vector phases use the same locked source inventory and final source-stability verification.
- A selected stage failure returns non-zero and is recorded separately in the run summary.

## Architecture decisions

- Reuse the existing analyzer entry points instead of changing every analyzer API.
- The graph pass omits Qdrant collection arguments and strips Qdrant environment settings.
- The vector pass sets `CORTEX_DISABLE_GRAPH=1`, skips graph setup/journals/framework overlays/topology, and reuses parse caches when it follows a graph pass.
- Topology remains after graph-producing framework overlays because its writer links modules to existing symbols and endpoints.
- Additive summary fields: `sync_mode` and `vector_embeddings`; existing parser/overlay fields remain available.

## Phases

1. [Phase 01 - Freeze CLI and orchestration contract](phase-01-contract.md)
2. [Phase 02 - Implement phased execution](phase-02-implementation.md)
3. [Phase 03 - Verify modes and document usage](phase-03-validation.md)

## Success criteria

- Default `both` execution orders every graph producer and topology before any vector-only analyzer invocation.
- Graph-only mode never opens Qdrant and produces topology nodes.
- Embedding-only mode never prepares, journals, or writes FalkorDB/Neo4j.
- Invalid specialized mode without `--full-scan` fails with an actionable CLI error.
- Interactive and `all` commands propagate the selected mode to `incremental_sync.py`.
- Targeted CLI, orchestration, state, topology, graph, and vector contract tests pass.

## Risks

- `both` can parse source twice. The vector pass must reuse normal analyzer caches where possible.
- Some analyzer families may infer Qdrant configuration from different environment variables. The graph-only environment scrub must cover canonical and legacy names.
- A vector failure occurs after graph/topology persistence. The summary must report that partial reality instead of implying graph rollback.
