# Code Sync Graph and Embedding Phases — 2026-08-13

## Context

Primary analyzers previously completed embeddings before `project_topology`, so slow or failed vector ingestion delayed usable module topology. The phase-mode plan defines graph/topology and embedding as independently selectable storage stages (`plans/260813-2152-code-sync-phase-modes/plan.md:16`).

## Change

- Added `--sync-mode both|graph|embedding` to interactive and `all` code-sync commands, with specialized modes restricted to full scans (`cortex_harness/dev.py:2438`, `cortex_harness/dev.py:2504`).
- Split child environments so graph passes cannot reopen Qdrant and embedding passes cannot mutate graph storage (`code-tiny/tools/sync/incremental_sync.py:1107`, `code-tiny/tools/sync/incremental_sync.py:1125`).
- Ordered primary/framework graph writes and `project_topology` before the graph-disabled embedding pass, with separate vector summaries (`code-tiny/tools/sync/incremental_sync.py:2630`, `code-tiny/tools/sync/incremental_sync.py:2744`).
- Preserved the shared incremental baseline for graph-only and embedding-only runs (`code-tiny/tools/sync/incremental_sync.py:2921`). Regression coverage verifies phase ordering, isolation, partial failure reporting, and CLI constraints (`tests/test_incremental_sync_phase_modes.py:102`, `tests/test_incremental_sync_phase_modes.py:136`, `tests/test_incremental_sync_phase_modes.py:185`).

## Impact

Graph-only scans now produce MCP-usable topology without waiting for embeddings; embedding-only scans can rebuild vectors without touching FalkorDB/Neo4j. **Risk: medium** because `both` may parse sources twice, and an embedding failure can leave graph/topology complete while the overall run fails; summaries expose that partial state (`code-tiny/tools/sync/incremental_sync.py:2855`).

## Decision

Reuse existing analyzer entry points as two orchestrated passes instead of refactoring every analyzer around a shared parse artifact. Require `--full-scan` for specialized modes until graph and vector stores have independent incremental baselines (`code-tiny/tools/sync/incremental_sync.py:3255`).

## References

- Plan: `plans/260813-2152-code-sync-phase-modes/plan.md:1`
- Validation: `plans/260813-2152-code-sync-phase-modes/reports/validation.md:1`
- Usage specification: `docs/specs/sync-code.md:38`
