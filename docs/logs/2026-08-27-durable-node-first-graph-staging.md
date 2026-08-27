# Durable node-first graph staging — 2026-08-27

## Context

The graph writer can currently emit some edge families before all node producers finish, and its process-local dependency mapping cannot represent nodes registered later. Phase 04E therefore defines a run-level node-first boundary and durable local staging instead of an unbounded in-memory cache (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:5`).

The same contract must work when graph/vector materialization targets are
owner-local files, remote services, or an explicitly resolved mix. The existing
factory already chooses remote components when their endpoints are configured
and local components when a remote component endpoint is absent
(`cortex_harness/storage/factory.py:270`,
`cortex_harness/storage/factory.py:292`). This amendment clarifies that backend
routing does not change ownership or placement of the graph-write spool.

## Change

The plan assigns SQLite WAL the control-plane and accounting role for run identity, producer lifecycle, manifests, barriers, leases, retries, dispositions, and conservation counters; immutable JSONL artifacts retain full canonical payloads, while graph receipts and exact readback prove materialization (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:84`). Edge execution now requires closed producer registration, a drained node barrier, and a sealed endpoint audit (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:281`).

The integrity contract adds typed compound node identities, explicit source/target endpoint ledgers, producer-completion records, row-conservation equations, relationship cardinality rules, and exact per-edge readback (`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:188`, `plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:211`, `plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:232`, `plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:308`).

File-backed and remote are materialization lanes, not journal locations. In both
lanes, SQLite WAL and its referenced immutable JSONL artifacts remain an
owner-local, single-ingest-owner spool. File-backed FalkorDBLite and Qdrant use
exclusive local leases (`code-tiny/tools/graph/driver/falkordb_driver.py:294`,
`cortex_harness/storage/qdrant.py:44`); remote services own server-side
concurrency, while the local ingest owner retains the journal and resolves an
ambiguous remote submit through receipts and exact readback rather than a local
filesystem lock (`cortex_harness/storage/qdrant_remote.py:8`,
`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:155`).

Every run is bound to a credential-free effective topology fingerprint covering
the actual component mode and canonical path or endpoint, graph/database or
collection, owner role, TLS/capability context, project scope, and generation.
The graph target fingerprint controls journal compatibility; the complete
graph/vector topology controls generation publication
(`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:128`).
This closes a known planning gap in the current journal helper: FalkorDB physical
identity is reconstructed from `FALKORDB_PATH` and graph name but ignores
`FALKORDB_URI`, so two remote services can currently collapse onto the same
placeholder identity (`code-tiny/tools/graph/journal/config.py:177`). The storage
adapter must become the source of the resolved effective descriptor
(`plans/260817-storage-backend-adapter/plan.md:24`).

The validation plan now covers file graph/file vector, remote graph/remote
vector, both mixed permutations, and force-local. Each lane uses the same frozen
fixture and compares canonical node, edge, vector-point, project-scope, exact
readback, and representative MCP results. It also injects remote ambiguous
commit/restart failures and local lease, disk, corruption, and reopen failures
(`plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md:448`,
`plans/260817-storage-backend-adapter/phase-05-testing-validation.md:87`).

## Impact

**Risk level: high.** This is a cross-writer and cross-backend ingestion contract
affecting every node and edge producer, crash recovery, project isolation,
target isolation, and generation publication. Once implemented, an accepted row
cannot disappear without an explicit disposition, and an edge cannot bind by ID
alone, cross project or label, or pass validation merely because aggregate
counts match (`plans/260807-1202-graph-ingest-write-path-hardening/plan.md:298`).
A local/remote, endpoint, graph/collection, role, TLS, mixed-mode, force-local,
or generation change also cannot claim or publish another topology's pending
work. This log records planning decisions only: runtime behavior is unchanged,
including the known remote physical-target gap, until Phase 04E and the storage
adapter amendment are implemented and validated.

## Decision

Use SQLite as a compact durable coordinator, not as the graph database, full-payload cache, or semantic resolver. Keep payloads in content-addressed artifacts and deterministic analyzer IDs in endpoint keys. Reject RAM-only staging, database-generated ID remapping, name/ID-only matching, implicit endpoint creation, and count-only validation because those alternatives cannot provide restart-safe conservation or detect swapped, missing, duplicated, ambiguous, or wrongly scoped edges.

Do not dual-write one run to local and remote targets, move SQLite/JSONL into the
remote graph service, or treat the local spool as a distributed queue. A run has
exactly one resolved target per component. Missing component configuration may
select an explicit mixed topology only before run creation; a connection, auth,
TLS, timeout, or ambiguous-commit failure after admission never falls back to a
file target. The current emergency force-local switch changes factory mode
before clients are created (`cortex_harness/storage/factory.py:198`); the planned
contract therefore fingerprints it as a separate topology and generation rather
than allowing it to resume the remote journal.

Use backend-specific recovery without weakening shared integrity invariants:
retain `StorageLease` ownership through validation and publication for embedded
paths (`cortex_harness/storage/lease.py:19`), but keep remote outcomes pending or
`reconciling` until service health, receipts, and exact graph/vector readback
prove ACK or retry. Promotion requires the complete parity matrix, not merely
matching adapter return types or aggregate counts.

## References

- Owning plan: [Phase 04E — durable node-first staging and edge release](../../plans/260807-1202-graph-ingest-write-path-hardening/phase-04e-node-first-staging.md)
- Parent plan: [Graph ingestion write-path hardening](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md)
- Storage dependency: [Storage backend adapter](../../plans/260817-storage-backend-adapter/plan.md)
- Backend parity gates: [Storage adapter testing and validation](../../plans/260817-storage-backend-adapter/phase-05-testing-validation.md)
- Queue boundary: `plans/260807-0929-mcp-ingest-query-concurrency/plan.md:126`
- Current remote-target identity gap: `code-tiny/tools/graph/journal/config.py:177`
- Current mixed and force-local routing: `cortex_harness/storage/factory.py:198`, `cortex_harness/storage/factory.py:270`, `cortex_harness/storage/factory.py:292`
- Current embedded ownership: `cortex_harness/storage/lease.py:19`, `code-tiny/tools/graph/driver/falkordb_driver.py:294`, `cortex_harness/storage/qdrant.py:44`
- Current remote concurrency boundary: `cortex_harness/storage/qdrant_remote.py:8`
