# Durable node-first graph staging — 2026-08-27

## Context

The graph writer can currently emit some edge families before all node producers finish, and its process-local dependency mapping cannot represent nodes registered later. Phase 04E therefore defines a run-level node-first boundary and durable local staging instead of an unbounded in-memory cache (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:5`).

## Change

The plan assigns SQLite WAL the control-plane and accounting role for run identity, producer lifecycle, manifests, barriers, leases, retries, dispositions, and conservation counters; immutable JSONL artifacts retain full canonical payloads, while graph receipts and exact readback prove materialization (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:77`). Edge execution now requires closed producer registration, a drained node barrier, and a sealed endpoint audit (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:204`).

The integrity contract adds typed compound node identities, explicit source/target endpoint ledgers, producer-completion records, row-conservation equations, relationship cardinality rules, and exact per-edge readback (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:111`, `plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:169`, `plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:231`).

## Impact

**Risk level: high.** This is a cross-writer ingestion contract affecting every node and edge producer, crash recovery, project isolation, and generation publication. Once implemented, an accepted row cannot disappear without an explicit disposition, and an edge cannot bind by ID alone, cross project or label, or pass validation merely because aggregate counts match (`plans/260807-1202-graph-ingest-write-path-hardening/plan.md:288`). This log records a planning decision only; runtime behavior is unchanged until Phase 04E is delivered.

## Decision

Use SQLite as a compact durable coordinator, not as the graph database, full-payload cache, or semantic resolver. Keep payloads in content-addressed artifacts and deterministic analyzer IDs in endpoint keys. Reject RAM-only staging, database-generated ID remapping, name/ID-only matching, implicit endpoint creation, and count-only validation because those alternatives cannot provide restart-safe conservation or detect swapped, missing, duplicated, ambiguous, or wrongly scoped edges.

## References

- Owning plan: [Phase 04E — durable node-first staging and edge release](../../plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md)
- Parent plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Queue boundary: `plans/260807-0929-mcp-ingest-query-concurrency/plan.md:126`
