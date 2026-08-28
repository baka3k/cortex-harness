---
title: "Graph ingestion write-path hardening"
status: in_progress
created: 2026-08-07
scope: "Automatic schema preflight, indexable relationship writes, durable node-first staging, restart safety, and truthful progress"
blockedBy: []
blocks:
  - 260807-0929-mcp-ingest-query-concurrency
  - 260804-1640-port-proc-cplus-to-code-tiny
  - 260807-1329-parser-quality-recovery
  - 260807-2103-toolchain-reliability-hardening
  - 260821-1144-cplus-semantic-call-graph
  - 260824-1411-cplus-clang-containment-hardening
phaseBlockedBy:
  phase-06:
    - original-20186-file-source-root-not-mounted
relatedPlans:
  - 260817-storage-backend-adapter
  - 260824-1411-cplus-clang-containment-hardening
  - 260821-1144-cplus-semantic-call-graph
  - 260807-2103-toolchain-reliability-hardening
  - 260807-0929-mcp-ingest-query-concurrency
  - 260804-1640-port-proc-cplus-to-code-tiny
  - 260807-1329-parser-quality-recovery
  - neo4j-to-falkordb-migration
  - 260806-1648-local-file-storage
  - 260813-2152-code-sync-phase-modes
---
# Graph ingestion write-path hardening

## Overview

Eliminate the recurring apparent hang after relationship progress such as
`[falkordb] relations:READS_FROM 87/87`. The observed process is not stopped:
the analyzer is blocked on a database socket while embedded FalkorDB consumes
one CPU core executing later relationship batches. `GRAPH.EXPLAIN` shows two
all-node scans and a Cartesian product because endpoint matches omit labels,
and required `id` indexes are currently created only after streaming ends.

The permanent fix is automatic and tool-owned. Every writer must declare its
node/relationship identity contract, ensure required indexes are operational
before the first graph mutation, and generate label-qualified endpoint
matches. Operators must not create indexes by hand. Manual schema setup remains
only a diagnostic/repair command backed by the same canonical manifest.

The original schema/index/query work and disposable-store validation are
complete. Restart recovery is reopened by the C++ cross-buffer include incident:
deferring relationships in process memory prevents early endpoint matching but
does not provide durable continuation. A SQLite WAL graph write journal is now
required before the final approximately 20,186-file canary and publication.

This plan covers the shared graph write path rather than patching only the C++
analyzer. It preserves Neo4j compatibility, uses local FalkorDB as the primary
performance acceptance target, and treats parse-quality warnings as a separate
diagnostic signal rather than the cause of database write latency.

## Scope challenge decisions

1. **Fix one analyzer or the shared writer?** Fix the provider-neutral shared
   contract and migrate every generic writer that performs unlabeled endpoint
   lookup. C++/Pro*C is the reproduction and first canary, not a special case.
2. **Manual or automatic indexes?** Automatic preflight is mandatory and
   fail-closed. A writer cannot stream until required indexes exist and report
   an operational state. Manual setup consumes the same manifest and cannot be
   a prerequisite for normal `dev sync code` use.
3. **How should interrupted or partial runs recover?** A failed/timeout run is
   marked incomplete and cannot publish success or advance the incremental
   baseline. Validated graph operation batches are durably journaled before
   source memory is released. A compatible retry resumes leased/pending work;
   an incompatible journal is quarantined and never replayed blindly.
4. **C++ only or shared writer?** The durable contract belongs to the shared
   graph writer. C++ is the first canary, followed by all shared writers and an
   explicit migration/blocking inventory for custom/direct mutation paths.
5. **Local queue or external broker?** Use SQLite WAL under the resolved local
   sync cache. It supports transactional enqueue/lease/ACK and crash recovery
   without adding a service. StoreGateway continues to own request-level job
   scheduling and staged-generation publication.

## Verified failure model

```text
current run
  parse warning (non-fatal)
       |
       v
  500-file buffer -> node batches -> relationship batches
                                      |
                                      v
                         MATCH (a {id}), (b {id})
                                      |
                        no labels => indexes unusable
                                      |
                  All Node Scan x2 + Cartesian Product
                                      |
                     FalkorDB ~100% CPU for each query
                                      |
          previous completed progress line remains on screen
                                      |
                         appears to be hung

  create_indexes() currently executes only after all buffers finish
```

Live evidence and official FalkorDB references are recorded in
[`research/local-evidence.md`](research/local-evidence.md).

## Target contract

### Canonical schema manifest

Create one code-owned manifest under `code-tiny/tools/graph/schema/`. For each
writer/analyzer contract it records allowed labels, identity properties,
required range/uniqueness indexes, optional lookup indexes, project scoping,
provider capabilities, and a stable schema version/fingerprint.

- `setup_constraints.py`, analyzers, and writers consume the manifest; they do
  not maintain independent label/index lists.
- Only indexes required by activated contracts are applied, avoiding an
  unbounded global index set and its write/storage cost.
- Manifest validation rejects duplicate names, malformed tuples, unsafe Cypher
  identifiers, conflicting identity definitions, and unsupported provider
  requirements before connecting to a user graph.

### Automatic schema preflight

Add a structured driver operation such as
`ensure_schema(contract, database) -> SchemaEnsureResult`.

- Run eagerly in the sync orchestrator before cleanup/streaming and enforce it
  again inside the writer before its first mutation so direct callers cannot
  bypass the invariant.
- Create missing indexes idempotently and inspect `CALL db.indexes()` until
  every required label/property index is operational.
- Cache success only by physical target, database, provider, and manifest
  fingerprint; never cache a failed or still-building result.
- Raise a typed startup error for required failures. Do not swallow them as
  warnings and continue into a predictably slow partial load.
- Audit duplicate identities before introducing a uniqueness constraint. Use a
  range index when uniqueness is not yet proven, with an explicit remediation
  report rather than deleting or merging data automatically.

### Indexable relationship compiler

Replace generic unlabeled endpoint lookup with one safe query builder:

- Group rows by `(source_label, target_label, relationship_type)` and, where
  the identity contract requires it, project scope.
- Validate all interpolated labels and relationship types against the manifest
  allowlist; values remain query parameters.
- Generate `MATCH (a:SourceLabel {id: row.source_id})` and
  `MATCH (b:TargetLabel {id: row.target_id})`, plus declared project keys.
- Return expected, matched, created/updated, and unresolved counts. Required
  missing endpoints fail the batch; explicitly optional relations are reported
  and reconciled, never silently dropped.
- Include the query-shape/schema fingerprint in checkpoints so an old resume
  state cannot be replayed through new grouping semantics.

### Query-plan and runtime safety

- Run `GRAPH.EXPLAIN` in integration tests for representative node and edge
  upserts. Endpoint lookup must use `Index Scan`; plans containing endpoint
  `All Node Scan` or Cartesian product fail acceptance.
- Apply supported database query/memory limits as a last-resort circuit breaker,
  not as the performance fix. Detect FalkorDB version/capability first because
  write-timeout behavior differs by release.
- Treat a timed-out write as potentially ambiguous until an idempotent readback
  reconciles it. Never blindly retry a mutation.

### Run lifecycle and observability

- Emit global rather than per-buffer progress: phase, run ID, file count,
  relationship label triple, batch number, elapsed time, rate, and query state.
- Use one logging sink. Remove duplicate logger/`print` output.
- Log `query_started` before awaiting and periodic watchdog heartbeats while a
  synchronous store call is still running, then `query_finished` or a typed
  timeout/failure. The last visible line must identify current work.
- Persist an incomplete/failed state with manifest/query fingerprints. Do not
  advance baseline or publish a generation until schema, counts, integrity,
  and representative query checks pass.
- Persist serializable mutation batches in a writer-local SQLite WAL journal.
  Queue delivery is at-least-once; graph effects are effectively-once only for
  deterministic, reconciled operations. Opaque/non-idempotent operations are
  converted or blocked before journal retry is enabled.
- Use produced/drained node-label barriers. Relationship jobs are claimable
  only after their required endpoint barriers drain.
- Enforce a run-level node-first publication boundary. Node batches may drain
  while parsing continues, but every relationship/call/evidence-edge batch is
  durably staged and remains ineligible until node production is explicitly
  closed and every staged node batch is ACKed. Per-endpoint barriers remain a
  secondary integrity aid; they are not the primary phase-ordering mechanism.
- Claim with bounded leases and fencing tokens. Timeout/cancel after submission
  is ambiguous until deterministic graph readback reconciles the outcome.
- Report parser health separately: files with errors, explicit `ERROR` nodes,
  `MISSING` nodes, encoding/fallback counts, alternate-parser choices, and the
  coverage gap between scanned files and compile commands.

## Phases

1. [Phase 01 — contract, inventory, and reproducible baseline](phase-01-contract-and-baseline.md)
2. [Phase 02 — canonical schema manifest and automatic preflight](phase-02-schema-preflight.md)
3. [Phase 03 — label-qualified, integrity-aware relationship writes](phase-03-indexable-write-path.md)
4. [Phase 04 — restart safety and truthful observability](phase-04-recovery-and-observability.md)
   - [Phase 04A — durable journal contract and storage](phase-04a-journal-contract-and-storage.md)
   - [Phase 04B — serializable operations, producers, and barriers](phase-04b-producer-operations-and-barriers.md)
   - [Phase 04C — leased consumer, reconciliation, and resume](phase-04c-consumer-reconciliation-and-resume.md)
   - [Phase 04D — CLI, observability, validation, and rollout](phase-04d-cli-observability-validation-rollout.md)
   - [Phase 04E — durable node-first staging and edge release](phase-04e-node-first-staging.md)
5. [Phase 05 — correctness, query-plan, and scale gates](phase-05-tests-and-performance.md)
6. [Phase 06 — canary, graph recovery, and rollout](phase-06-rollout-and-backfill.md)

## Dependencies and ownership

- `260821-1144-cplus-semantic-call-graph` consumes this plan's canonical
  schema, typed evidence relationships, mutation journal, integrity checks,
  and restart-safe publication mechanics in semantic Phases 06-07. This plan
  remains the graph-write authority; the semantic plan owns which call
  evidence is eligible for each graph view.

- `260817-storage-backend-adapter` owns resolution of the effective file-backed
  or remote graph/vector targets. Phase 04E consumes a credential-free canonical
  target descriptor and must pass the same node-first, journal, integrity, and
  readback contract against FalkorDBLite files and remote graph services. A
  backend switch creates an incompatible run/generation; journal work is never
  replayed onto a different effective target.

- `260807-2103-toolchain-reliability-hardening` consumes this plan's schema,
  mutation journal, reconciliation, barrier, and integrity contracts for its
  end-to-end outcome model and certification gates. This plan remains the owner
  of graph write mechanics and blocks reliability Phases 03, 04, and 07.

- `neo4j-to-falkordb-migration` is a completed architectural input. This plan
  strengthens its schema/query contract without reopening the migration.
- `260807-0929-mcp-ingest-query-concurrency` owns physical-store admission,
  staged generations, and publication. This plan owns writer schema readiness,
  query shape, batch integrity, and the durable writer mutation journal. The
  StoreGateway ingestion queue schedules whole requests; it does not duplicate
  journal batches. Its Phase 03 and Phase 06 consume `queue_drained` plus graph
  validation as publication prerequisites.
- `260804-1640-port-proc-cplus-to-code-tiny` owns Pro*C extraction semantics and
  labels. This plan owns how those labels are indexed and written. Its graph
  integration and acceptance phases consume this contract.
- Parser-coverage plans may continue independently. They must not add new
  analyzer-local schema/index lists or generic unlabeled relationship queries.

## Expected file areas

- New: `code-tiny/tools/graph/schema/` for typed contracts, manifest validation,
  provider compilation, readiness results, and fingerprints.
- Update: `code-tiny/tools/graph/driver/{base,neo4j,falkordb}_driver.py` for
  schema preflight, capability detection, readiness, and typed failures.
- Update: `code-tiny/tools/graph/writer/language_writer.py` first, then
  `spring_writer.py`, `mybatis_writer.py`, and project-topology paths that use
  generic endpoint lookup.
- Update direct mutation paths in Android Java/Kotlin, TypeScript backend, and
  `cross_edge_ops.py`; add a repository guard so new unlabeled identity
  mutations cannot bypass the shared compiler.
- Audit unlabeled identity reads in graph expanders and MCP paths. Route
  genuinely polymorphic reads through an explicit bounded API; otherwise make
  their label/identity contract concrete. Read-path migration is required when
  it can reproduce the same full-graph lookup, but does not broaden this plan
  into an MCP behavior redesign.
- Update: `code-tiny/tools/cplus/cplus_analyzer.py` and
  `code-tiny/tools/sync/incremental_sync.py` for preflight ordering, global
  progress, fingerprints, and incomplete-run handling.
- New: `code-tiny/tools/graph/journal/` for versioned operations, SQLite WAL
  state, immutable artifacts, barriers, leased execution, reconciliation, and
  safe retention.
- Update: journal runtime/store and shared writers for a persisted run-level
  node-production barrier, node/edge staging manifests, phase closure, and
  edge eligibility that survives process restart without retaining payloads in
  analyzer memory.
- Update: `cortex_harness/dev.py` so outer retries preserve one stable compatible
  run identity and expose journal status rather than restarting anonymous work.
- Refactor: `code-tiny/scripts/setup_constraints.py` into a thin consumer of
  the canonical manifest.
- Tests: focused root and `code-tiny/tests/` suites plus temporary-store
  integration and benchmark fixtures. Never point tests at registered user
  graph paths.

## Performance acceptance targets

Phase 01 captures the reproducible baseline and Phase 05 may tighten these
provisional gates, but it may not weaken the query-plan invariant.

| Concern | Initial gate |
| --- | --- |
| Schema preflight | All required indexes report operational before mutation; missing/failed indexes abort with zero streamed batches. |
| Query plan | No endpoint `All Node Scan` or Cartesian product; both endpoints resolve through label/property index scans. |
| Fixed batch scaling | A 1,000-row relation batch at 500k nodes takes no more than 2x the same batch at 100k nodes. |
| Batch latency | Representative local FalkorDB p95 <= 5 s at 100k nodes; any exception requires an evidence-backed threshold revision. |
| Integrity | Expected = matched + explicitly unresolved; no silent relationship loss; second identical run changes no final counts. |
| Visibility | No active database query is silent for more than 10 s; progress names the in-flight query rather than only the previous batch. |
| Durable resume | A forced exit at every enqueue/lease/commit/ACK/barrier boundary resumes only unfinished compatible work; ACKed effects are not replayed. |
| Node-first ordering | Zero relationship/call/evidence-edge mutation queries execute before the run-level node barrier reaches `drained`; the invariant holds across restart and across streamed analyzer buffers. |
| Staging memory | Peak writer memory is bounded by the configured batch plus identity index overhead; graph payload volume is stored in immutable artifacts rather than one process-wide node/edge list. |
| Row conservation | Every emitted node/edge row reaches one explicit staged, duplicate, rejected, ACKed, and graph-verified disposition; all per-producer and per-contract equations reconcile with zero unexplained loss. |
| Endpoint binding | Every required edge endpoint resolves exactly once by project scope, label, identity property, and canonical typed value; ID-only, cross-project, and ambiguous fallback matching are absent. |
| Exact edge parity | Final readback compares canonical edge identities and endpoints, not only totals; swapped, compensating duplicate/missing, or wrongly scoped edges fail validation. |
| Backend parity | The same canonical fixture produces identical node/edge manifests, project-scoped readback, and representative query results on file-backed FalkorDBLite and remote FalkorDB; the existing Neo4j remote provider passes the provider-neutral graph contract where enabled. |
| Target isolation | Journal fingerprints bind the effective backend kind, credential-free canonical endpoint/path, graph/database, project, and generation; local/remote, mixed-mode, force-local, or endpoint changes cannot share resumable work. |
| Journal safety | No graph mutation occurs without committed enqueue; disk-full/corrupt/incompatible journals fail closed; storage is bounded by configured item/byte/retention limits. |
| Journal overhead | Record enqueue/ACK p50/p95, peak journal/artifact bytes, restart latency, and end-to-end delta. Required-mode rollout needs <=10% warm end-to-end overhead or an evidence-backed revision. |
| Full canary | The 20k-file repository finishes without an unbounded query and within 2x the indexed warm baseline established in Phase 01. |
| Incident improvement | The same fixture/query improves relationship-batch p95 by at least 10x over the captured 18.6-34.1 s incident range, or the rollout stops with evidence. |

## Failure and recovery policy

- Existing partially written graphs are not assumed valid. Inventory duplicate
  identities, orphan candidates, schema state, and run metadata before repair.
- The supported default is a fresh staged full scan after the new contract is
  enabled. In-place repair is allowed only when identity/integrity audit passes
  and produces the same validation report as a clean build.
- Cancellation or timeout leaves the current committed generation/baseline
  unchanged. Temporary staging is retained for diagnosis or safely removed by
  generation lifecycle tooling, never by broad filesystem deletion.
- Compatible incomplete journals resume automatically. Expired leases are
  recovered with fencing. Incompatible fingerprints are quarantined; corrupt,
  disk-full, integrity, and dead-letter states fail closed.
- Database timeouts are not immediate retries. Deterministic readback ACKs
  confirmed effects and retries only confirmed missing work.
- Rollback disables the short-lived feature flag and selects the last validated
  generation. It does not restore the old unlabeled writer as a permanent mode.

## Success criteria

- `dev sync code --full-scan` requires no manual index creation.
- Required indexes are present and operational before the first node or
  relationship batch on every supported invocation path.
- Representative relationship plans use indexes and remain sublinear as total
  graph size grows.
- C++/Pro*C and all migrated generic writers preserve node/edge counts,
  provider parity, project isolation, and idempotency.
- Android Java/Kotlin, TypeScript backend, topology, and cross-edge mutation
  paths contain no unlabeled identity upsert or endpoint lookup.
- Missing endpoints, duplicate identities, schema failure, timeout, cancellation,
  and restart have typed, tested, non-silent outcomes.
- Every journal-enabled operation is serializable, deterministic, reconciled,
  and replay safe; bypass paths are migrated or explicitly rejected.
- Every supported edge family, including repository, navigation, workflow,
  call, evidence, framework, topology, Android, TypeScript, and C++ paths, is
  staged before execution and cannot mutate the graph until all node jobs for
  that run are drained.
- Edge rows persist deterministic endpoint references containing project scope,
  endpoint label, identity property, and identity value; no database-generated
  ID remapping is introduced.
- SQLite remains the durable control/accounting plane while immutable artifacts
  hold full payloads and exact graph receipts/readback prove materialization.
  SQLite state alone never certifies a graph as complete or semantically
  correct.
- Producer completion, canonical typed identities, sealed endpoint audits, and
  row-level conservation/readback prevent silent node loss and wrong endpoint
  binding even when aggregate counts happen to match.
- File-backed and remote lanes use the same operation/artifact/manifest
  contract. Backend-specific transport, lease, timeout, and reconciliation
  policies may differ, but neither lane may weaken ordering or integrity.
- A compatible crash/retry resumes pending work without reparsing or replaying
  ACKed batches, and an incompatible run cannot claim current work.
- Progress and health output distinguish parsing, queueing, database execution,
  completion, and failure, with no duplicate lines.
- The parser warning is quantified accurately and does not block ingestion
  unless a separately configured parse-quality gate is exceeded.
- Cross-plan metadata and task ownership remain bidirectionally consistent.

## Non-goals

- Rewriting Tree-sitter grammars or solving all legacy C/Pro*C parse errors.
- Making embedded FalkorDB execute multiple writes concurrently.
- Adding every possible property index globally.
- Silently deleting duplicate nodes or partially written user graphs.
- Treating timeouts, larger batches, or more CPU as substitutes for an
  indexable query plan.
- Claiming exactly-once delivery across SQLite and the graph store. The contract
  is at-least-once delivery plus idempotent effects and explicit reconciliation.
- Replacing StoreGateway request scheduling or staged-generation publication
  with the writer-local mutation journal.

## Implementation status

The original Phases 01-05 are implemented and validated. Phase 04A-04D reopened
restart safety with a durable mutation journal. Phase 04E now strengthens the
current per-batch/per-endpoint ordering into a durable run-level node-first
boundary before Phase 06. Phase 06 remains blocked until these subphases pass
and the full approximately 20k-file source canary is available. See
[`research/durable-write-journal.md`](research/durable-write-journal.md),
[`reports/durable-write-journal-red-team.md`](reports/durable-write-journal-red-team.md),
and [`reports/durable-write-journal-validation.md`](reports/durable-write-journal-validation.md).

### 2026-08-28 C++/Pro*C checkpoint

The C++/Pro*C writer now uses the versioned `phase:nodes` journal barrier for
generic, call, possible-call, evidence, include, and repository-file edges.
Specialized Pro*C, unknown-call, and parse-run nodes use trusted node
operations, and the sync parent pins child analyzers to its resolved FalkorDB
path/URI and graph instead of allowing project configuration to reroute them.

The 24-file `procsample` canary passed fresh local ingest and a forced kill
after six node batches. The compatible restart skipped those ACKed batches,
finished 36/36 batches, drained `phase:nodes` at 12/12, and leased its first
edge only after the barrier-close event. Fresh and resumed readback matched at
646 business nodes, 1,327 edges, and 24 files; required mode additionally
materialized 36 expected `GraphWriteReceipt` audit nodes. The repository suite
passed 1,402 tests and 270 subtests with 10 skips.

This is a scoped checkpoint, not Phase 04E or Phase 06 completion. The durable
identity/endpoint conservation ledgers, sealed endpoint audit, remaining
analyzer/custom writers, full backend matrix, and approximately 20k-file
canary remain open, so their acceptance checkboxes stay unchecked.

## Delivery command

After approval, implement with:

```text
/hi-craft plans/260807-1202-graph-ingest-write-path-hardening/plan.md
```
