# Phase 07 - Validation, Performance, And Rollout

## Goal

Prove functional parity, performance acceptability, and operational readiness before cutover.

## Tasks

1. Unit tests.
   - Driver result parsing.
   - Schema conversion helpers.
   - Query adapter behavior.
   - Error handling and retry behavior.

2. Integration tests.
   - Local FalkorDB smoke test.
   - `code-tiny` MCP representative graph queries.
   - `doc-tiny` ingest/query/reset workflow.

3. Parity tests.
   - Run selected workflows against Neo4j and FalkorDB seed graphs.
   - Compare normalized JSON outputs.

4. Performance tests.
   - Batch ingest throughput.
   - Full-text search latency.
   - Path query latency and memory.
   - GraphRAG query latency.

5. Operational docs.
   - Update READMEs and env examples.
   - Add migration checklist.
   - Document unsupported Neo4j features and manual actions.

## Validation Checklist

- Schema validation: indexes and constraints exist and are operational.
- Data consistency: counts and sampled properties match.
- Query validation: all migrated queries return expected shape.
- Performance validation: representative workloads are within accepted thresholds.
- Regression testing: existing non-graph analyzers and Qdrant flows remain unchanged.
- Integration testing: MCP server tools and doc GraphRAG entry points work against FalkorDB.

## Risks

- FalkorDB query plans may differ enough to require new indexes or query rewrites.
- Large graph path queries may need limits, direction constraints, or alternate algorithms.

