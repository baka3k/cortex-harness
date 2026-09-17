# Red Team Review

status: completed
date: 2026-07-06

## Verdict

CAUTION: The plan is directionally sound, but implementation must not proceed without Phase 01 because several Neo4j features in this codebase have provider-specific semantics.

## Findings

1. Full-text search is the biggest compatibility gap.
   - Neo4j named full-text indexes are used in `code-tiny`.
   - FalkorDB full-text procedures are label/type oriented.
   - Mitigation: implement provider-specific full-text search methods instead of forcing one shared Cypher string.

2. Constraint behavior is not equivalent.
   - Current scripts rely on Neo4j unique constraints for concurrent `MERGE` safety.
   - FalkorDB unique constraints require supporting indexes and are asynchronous.
   - Mitigation: add status polling and concurrency stress tests before enabling parallel ingestion.

3. Result parsing can break quietly.
   - Neo4j returns record dictionaries and graph objects; `falkordb-py` returns rows/result sets.
   - Mitigation: make result normalization a first-class Phase 02 deliverable with tests.

4. The graph abstraction is incomplete.
   - `GraphProvider.FALKORDB` exists, but raw Neo4j access remains outside the abstraction in scripts and MCP bridge code.
   - Mitigation: inventory and migrate all direct paths, not only `Neo4jDriver`.

5. `doc-tiny` has a different architecture than `code-tiny`.
   - It lacks a graph provider layer and uses direct script-level sessions.
   - Mitigation: introduce a tiny local adapter rather than copying the larger `code-tiny` abstraction.

## Required Plan Adjustments

- Keep Phase 01 mandatory.
- Add constraint status polling.
- Add explicit full-text provider mapping.
- Add result-shape tests before migrating service code.
- Keep Neo4j provider during rollout for parity and rollback.

