# Validation Interview

status: superseded by validation-checklist.md
date: 2026-07-06

## Critical Questions

1. What is the target FalkorDB topology?
   - Local Docker, FalkorDB Cloud, existing Redis-compatible deployment, or embedded FalkorDBLite?
   - This affects connection config, auth, persistence, and test automation.

2. Should Neo4j remain supported after migration?
   - Recommended answer: yes during rollout.
   - A hard removal can happen later after parity tests pass.

3. Does `code-tiny` use multiple Neo4j databases in production?
   - FalkorDB graph names may need to map to Neo4j databases or project IDs.

4. Are concurrent imports required for production workloads?
   - If yes, MERGE/constraint behavior must be stress-tested under FalkorDB before cutover.

5. Are `neo4j-graphrag` features actively used in `doc-tiny` runtime?
   - If yes, replacement strategy may require more than swapping the driver.

## Default Answers Used For Planning

- Use dual-provider migration first.
- Preserve Qdrant.
- Use `falkordb-py`.
- Add new FalkorDB config while keeping Neo4j rollback.
- Treat constraint and full-text behavior as compatibility risks until verified.
