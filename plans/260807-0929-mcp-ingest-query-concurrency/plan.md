---
title: "MCP ingest/query concurrency with a single embedded-store owner"
status: pending
created: 2026-08-07
scope: "Embedded graph/vector owner, bounded MCP queries, staged ingestion publication"
blockedBy: []
blocks:
  - 260807-1329-parser-quality-recovery
phaseBlockedBy:
  "03": [260807-1202-graph-ingest-write-path-hardening]
  "06": [260807-1202-graph-ingest-write-path-hardening]
relatedPlans:
  - 260806-1648-local-file-storage
  - 260728-0000-unified-ingest-query-contract
  - 260807-1202-graph-ingest-write-path-hardening
  - 260807-1329-parser-quality-recovery
---
# MCP ingest/query concurrency with a single embedded-store owner

## Overview

Implement the concurrency boundary recommended by
`plans/reports/260807-0929-mcp-ingest-query-concurrency-prediction.md`.
MCP remains connected and serves the last committed graph/vector generation
while ingestion builds the next generation in isolated staging storage. One
owner continues to hold the embedded-store lease; same-target writes are
queued and serialized; queries are admitted through a bounded reader policy.

The plan is intentionally an embedded-owner first release. It does not weaken
`StorageLease`, permit multiple processes to open one embedded target, or claim
that local Qdrant/FalkorDB is a horizontal-concurrency substrate. Server-mode
adapters remain a later scale path behind the same gateway contract.

## Scope challenge decisions

### 1. Which runtime is the first delivery target?

**Decision: embedded owner/gateway first.** The current Docker-free storage
layout, shared graph-driver cache, and shared Qdrant adapter are the active
runtime. The gateway must work with those seams and preserve the existing
pause/restart workflow as a rollback path. FalkorDB/Qdrant server mode is a
follow-up only when benchmarked SLOs or independent process scale require it.

### 2. What does “available during ingestion” mean?

**Decision: stale-but-consistent reads are valid.** Every query pins one
committed generation. Ingestion writes only to a staging graph/vector pair;
failed or cancelled jobs never change the active manifest. Query and status
responses expose `served_generation`, `source_revision`,
`last_committed_at`, and ingestion state so clients can choose freshness
policy without receiving partial data.

### 3. What concurrency is allowed?

**Decision: concurrent acceptance, bounded execution.** Requests may be
accepted concurrently, but admission is keyed by resolved physical target
(instance, owner, graph path, vector path), not only by `project_id`. One
writer runs per physical target. Reader capacity is bounded and isolated from
writer capacity; truly isolated physical targets may run in parallel. Direct
multi-process embedded access remains fail-closed.

## Verified baseline

- `cortex_harness/storage/lease.py:19-80` takes a non-blocking OS lease for
  one embedded target and must remain the process-ownership guard.
- `cortex_harness/storage/qdrant.py:35-68` caches one local Qdrant client per
  path and locks only client creation; ordinary query/upsert/scroll operations
  have no operation-level concurrency policy.
- `code-tiny/tools/graph/core/shared_runtime.py` correctly shares one graph
  driver per physical provider target inside a process, but it does not bound
  query execution or coordinate writers.
- `code-tiny/tools/graph/driver/falkordb_driver.py:309-351` exposes a
  synchronous `graph.query` through an async method and currently retries
  mutations without an idempotency distinction.
- `code-tiny/mcp/unified_mcp.py:1156-1182` intentionally reuses the shared
  driver to avoid a second embedded owner; this is the main query gateway
  seam.
- `code-tiny/mcp/services/explore_service.py:438-491` moves synchronous
  retrieval into threads, which avoids event-loop blocking but can exhaust
  the default executor under burst load.
- `code-tiny/tools/sync/incremental_sync.py` has a project/root run lock and
  ingestion state, but the lock scope is not guaranteed to equal the resolved
  physical store scope.
- `260807-1202-graph-ingest-write-path-hardening` now owns a durable writer-local
  graph mutation journal. This plan's request queue schedules ingestion jobs;
  it must consume rather than duplicate mutation batch state.
- `code-tiny/tools/common/local_qdrant.py` and the completed local-storage
  plan already provide the application-owned Qdrant boundary and role paths.
- Existing local-storage and unified-contract plans are completed inputs; this
  plan extends their runtime boundaries rather than changing project naming or
  removing the lease.

## Target architecture

```text
MCP clients ───────────────┐
                           v
                    StoreGateway / Owner
                    ├─ bounded query admission
                    ├─ per-target writer queue
                    ├─ generation pin + ref-count
                    └─ status / health / metrics
                           │
             active generation N (read-only)
             ├─ graph store N
             └─ vector store N

Ingestion request ─> idempotent job queue ─> parse/embed workers
                                      └─ single writer -> staging N+1
                                                         -> validate both
                                                         -> atomic manifest publish
                                                         -> retire N after readers drain
```

### Queue ownership boundary

- StoreGateway queue records request admission, fairness, cancellation, source
  revision, and staging-generation lifecycle.
- GraphWriteJournal records serializable graph mutation batches, node barriers,
  leases, reconciliation, and ACK state inside one ingestion job.
- The gateway submits one stable run identity to the writer. It publishes only
  after `queue_drained` and graph/vector validation. It never copies graph batch
  payloads into a second request queue.

### Generation contract

Each physical target has an immutable generation record containing at least:

- `generation_id`, `target_key`, `project_scope`, and `source_revision`;
- graph and vector physical paths for the generation;
- creation, validation, publication, and retirement timestamps;
- manifest schema version and validation summary.

The active manifest is the only reader selection authority. It is written to a
temporary file, fsynced, and atomically renamed while holding the owner-side
publication lock. A generation is publishable only when representative graph
queries, vector queries, counts, schema checks, and cross-store identity
checks pass. Existing requests hold a generation reference until completion;
retirement/cleanup cannot close or delete a referenced generation.

### Gateway contract

Define an application-owned gateway with operations equivalent to:

- `query(target, operation, deadline, freshness_policy)`, returning results
  plus generation/freshness metadata;
- `submit_ingest(request_id, target, source_revision, payload)`, returning a
  durable job/status record or a structured duplicate/overload response;
- `get_ingestion_status(target, job_id)` and `health(target)`;
- `publish(staging_generation)` and `retire(generation)` for owner-internal
  lifecycle use.

The gateway owns leases, client/driver instances, reader and writer admission,
generation references, and shutdown. Callers do not open database paths or
construct a second local Qdrant/FalkorDB owner.

### Admission and failure contract

- Separate bounded reader and writer capacities; never let retrieval consume
  all capacity needed for publication or health probes.
- Enforce request deadlines, queue limits, per-client/per-project fairness,
  and structured `OVERLOADED`, `INGESTION_ALREADY_RUNNING`,
  `STORE_MAINTENANCE`, `REQUEST_TOO_LARGE`, and `STALE_GENERATION` responses.
- Bound queues by both item count and estimated bytes. Large parser/embed
  payloads are referenced through immutable staging artifacts rather than
  copied into memory; query `top_k`, traversal depth, result bytes, and model
  batch size have validated cost limits.
- Deduplicate ingestion by `(target_key, idempotency_key)` and reject or
  explicitly supersede stale source revisions.
- Retry reads only when safe. Retry a mutation only after the writer proves
  that the previous attempt did not commit; otherwise mark it ambiguous and
  require reconciliation.
- Cancellation stops admission immediately and is observed at parser/embedder
  checkpoints; synchronous store calls are not falsely reported as cancelled
  until they return.

### Process model and lifecycle rules

- Embedded mode runs exactly one MCP/`StoreGateway` process per storage owner
  role (`code` and `document`). The MCP transport worker count is fixed at one;
  adding HTTP/MCP workers is not a scaling mechanism because every worker
  would try to own the same embedded files.
- The owner process acquires `StorageLease` before opening graph/vector
  clients and holds it until all admission lanes, generation handles,
  executors, and clients are closed. The lease is the first resource acquired
  and the last released.
- When an owner acquires both graph and vector leases, it sorts their canonical
  absolute paths, acquires in that order, and releases in reverse order. A
  partial acquisition closes/releases everything already acquired before
  reporting the conflict.
- Parser and embedding workers may run as bounded child processes, but they
  never receive live graph/Qdrant handles and never open owner paths. They
  return immutable artifacts or batches to the owner for storage writes.
- Do not `fork` after a database client, thread pool, event loop, or ML model
  has been initialized. Existing subprocess orchestration remains preferred;
  new multiprocessing must use a spawn-safe contract.
- Startup progresses through `STARTING -> RECOVERING -> WARMING -> READY`.
  Readiness is false until manifest recovery, active-generation open, and
  representative graph/vector probes succeed. Liveness remains separate so a
  slow warmup does not trigger a restart loop.
- First `SIGTERM`/`SIGINT` moves the process to `DRAINING`: stop admission,
  preserve status endpoints, drain active queries to their deadlines, persist
  queued/running job state, and either finish an already-started manifest
  publication or leave the prior manifest active. A second signal is the
  documented force-exit path.
- Child-process death, owner restart, or abandoned staging data is reconciled
  from durable job/manifest state. PID and lock-file metadata are diagnostic;
  the OS lock and manifest are authoritative.

### Lock hierarchy and correctness rules

Use one documented acquisition order across code and document owners:

1. process-lifetime `StorageLease`;
2. per-physical-target scheduler/admission lock;
3. short generation publication mutex;
4. generation registry/reference-count lock;
5. adapter client-cache lock.

Rules:

- Acquire locks only in the listed order; never upgrade a read/reference lock
  to a writer/publication lock. Multi-target administrative operations either
  avoid simultaneous locks or sort `PhysicalTargetKey` values canonically.
- Never call graph/Qdrant, wait on a future, execute user code, or perform
  parser/embed work while holding an in-process state lock. The publication
  mutex may cover only validation-state recheck plus temporary-manifest
  write/fsync/atomic rename.
- Client-cache and reference-count locks are leaf locks, are held only for
  dictionary/state mutation, and are released before close/open/query calls.
- The target writer permit is acquired only for `WRITING -> VALIDATING ->
  PUBLISHING`; parallel parse/embed preparation happens before it. This keeps
  lock hold time independent of repository parsing duration.
- `StorageLease` remains fail-fast. User submissions do not spin or retry that
  lease; a conflicting owner returns holder metadata and an actionable status
  command. Queue waits and publication-lock waits are bounded and reported
  separately from execution time.
- Every acquisition records wait time, hold time, target key, job/request ID,
  and timeout outcome. Tests must assert lock release on exceptions,
  cancellation, process termination, and startup recovery.

### Thread, executor, and model rules

- The asyncio event loop performs orchestration only. No blocking graph,
  Qdrant, filesystem scan, model inference, or subprocess wait runs directly
  on it.
- Replace storage-path `asyncio.to_thread()` and default-executor use with
  named, bounded gateway lanes: graph read, vector read, storage write,
  control/health, and CPU preparation. Queue capacity is explicit; there
  is no unbounded executor and no nested executor submission.
- Until mixed-load tests prove otherwise, each embedded graph handle and each
  local Qdrant handle executes at concurrency `1`; request-level concurrency
  can still overlap query understanding/embedding and queue storage work.
  Raising per-handle concurrency is a measured configuration change.
- Exactly one writer lane exists per physical target. Control/health has
  reserved capacity, and interactive query work uses weighted fair admission
  so batch clients cannot starve a human query or the writer indefinitely.
- Graph/vector writes use bounded item+byte batches with cancellation and
  pressure checkpoints between batches. When memory, disk, or interactive
  queue pressure crosses its high watermark, the gateway stops starting new
  parse/embed preparation before it rejects interactive queries.
- CPU-bound parser work uses bounded subprocesses outside the store owner.
  The automatic worker budget is capped by CPU and memory headroom; it must
  leave one core for MCP/event-loop/lifecycle work.
- Embedding concurrency defaults to one active batch per device. Models use
  single-flight initialization and bounded caching; do not reload a model per
  request. Torch/BLAS/OpenMP thread counts are capped to prevent nested
  oversubscription across parser, embedding, and gateway workers.
- A queued request is cancellable immediately. A running synchronous store
  call is non-preemptive: cancellation is recorded, its result is reconciled,
  and the client is told whether the operation completed, cancelled before
  commit, or reached an ambiguous state.

### Performance and resource budgets

Phase 01 records a cold and warm baseline; Phase 06 may revise these
provisional acceptance targets with evidence. They are SLOs, not sleeps or
hard-coded assumptions:

| Concern | Initial acceptance target |
| --- | --- |
| Ingestion submission | Return `job_id`, state, queue position, and active generation at p95 <= 200 ms without waiting for parse/write. |
| Status/health | p95 <= 250 ms using reserved capacity; never queue behind normal retrieval. |
| Event-loop lag | p99 <= 50 ms during the supported mixed-load profile. |
| Interactive warm query | Representative-fixture p95 <= 2 s and p99 <= 5 s; report model/graph/vector stage timings when missed. |
| Queueing | Query queue wait <= 20% of its deadline; a full queue returns overload within 100 ms with `retry_after_ms`. |
| Queue memory | Enforce item-count and estimated-byte limits; queue/artifact metadata growth is bounded during soak. |
| Query cost | Enforce validated caps for `top_k`, graph depth/path count, result bytes, and embedding batch bytes with actionable errors. |
| Storage concurrency | Safe profile: one graph operation, one vector operation, and one writer per handle/target until Phase 06 approves more. |
| Memory | Keep >= 20% process/system headroom, no sustained swap growth, bounded model/cache size, and bounded thread/process counts. |
| Disk | Before staging, require estimated active + staging/rollback footprint plus 20% safety; reject early with required/free bytes. |
| Shutdown | Normal queries drain within their existing deadlines; publication gets a configurable bounded grace period and never exposes a partial manifest. |

The first release uses fixed bounded limits with `safe`, `balanced`, and
validated `custom` profiles. `safe` is the fallback and default until the
benchmark report approves `balanced`; adaptive concurrency is out of scope.
Configuration is validated in one owner config object, printed by doctor/status,
and rejected at startup when capacities, timeouts, or memory/disk budgets are
internally inconsistent.

### User experience contract

- Ingestion is asynchronous by default: submit returns quickly with `job_id`,
  deduplication result, queue position, current phase, and active generation.
  CLI/API supports status, `--follow`/streamed progress, bounded `--wait`, and
  cancel without requiring the user to stop MCP manually.
- A duplicate idempotency key returns the existing job instead of an error or
  duplicate work. Progress reports stable phases and completed/total counters;
  do not invent an ETA when throughput is not stable.
- Queries continue on the last committed generation during ingestion and
  always expose freshness. `require_fresh`/`min_generation` waits only within
  the caller deadline, then returns a typed stale-generation response.
- Overload and maintenance responses include a stable code, human message,
  `retryable`, `retry_after_ms`, queue depth/capacity, active generation, and
  request correlation ID. Raw lock exceptions and stack traces remain in
  diagnostics, not user-facing results.
- Infrastructure failure, timeout, overload, or cancellation must never be
  converted into an empty successful search result. Empty results mean the
  query completed successfully and found nothing.
- Cold-start/model warmup is visible as `WARMING` with storage queries served
  where possible. Doctor/status shows owner PID, uptime, lease holder,
  generation/freshness, queue pressure, active job, and the exact safe action
  available to the user.
- Liveness is a cheap process/event-loop check. Readiness uses a recent cached
  deep graph/vector probe maintained by the reserved control lane; ordinary
  status reads a lock-free/atomic snapshot and never launches an expensive
  probe on the user's request path.
- Existing pause/restart remains an automatic rollback path. Normal users do
  not manage lock files, kill child processes, or choose thread counts to make
  the system work.

## Phases

1. [Phase 01 — concurrency contract, generation schema, and baseline](phase-01-contract-and-baseline.md)
2. [Phase 02 — StoreGateway, leases, and bounded admission](phase-02-store-gateway-and-admission.md)
3. [Phase 03 — staged graph/vector generations and atomic publication](phase-03-staged-generations-and-publication.md)
4. [Phase 04 — MCP query and ingestion routing](phase-04-mcp-ingest-routing.md)
5. [Phase 05 — idempotency, observability, cleanup, and scale seam](phase-05-failure-observability-and-scale.md)
6. [Phase 06 — mixed-load acceptance and guarded rollout](phase-06-acceptance-and-rollout.md)

## Cross-plan dependencies

### Completed plans reused

- `260806-1648-local-file-storage`: preserves centralized role paths,
  `StorageLease`, shared local Qdrant, and provider-neutral graph construction.
- `260728-0000-unified-ingest-query-contract`: preserves registry-resolved
  logical graph/collection names and stateless `project_id` targeting.
- `260728-0900-simplify-search-full-removal` and
  `260728-1400-remove-db-param-unify-project-id`: do not reintroduce removed
  `db` or `search_full` tool contracts while adding concurrency metadata.

### Active-plan coordination

- `260807-1202-graph-ingest-write-path-hardening` owns canonical graph schema
  preflight, indexable relationship queries, batch integrity, and writer-local
  recovery/progress. This concurrency plan owns physical-store admission,
  staged generation lifecycle, and publication. Phase 03 must consume the
  hardened writer contract before staging integration is considered complete;
  Phase 06 must use its query-plan/integrity gates during rollout. Phases 01-02,
  04, and 05 may proceed against the frozen interface without duplicating that
  plan's schema or relationship compiler.
- `260728-1500-testtool-runall-error-tracing` is a read-only consumer of
  `tools/list`; update its expected error/status rendering only after Phase 04
  settles structured overload and job responses. It is not a blocker.
- Parser plans that touch `incremental_sync.py`, registries, or shared MCP
  tests must keep their edits additive. This plan owns concurrency wrappers,
  not parser-specific ingestion semantics.
- No other unfinished plan owns the physical-store concurrency boundary. Any
  graph-writer/schema changes remain in the hardening plan and are consumed
  through its typed preflight and batch-result contracts.

## Expected file areas

### New runtime contracts

- `cortex_harness/storage/contracts.py` — target key, generation manifest,
  freshness/status/error models.
- `cortex_harness/storage/gateway.py` — owner lifecycle and public gateway.
- `cortex_harness/storage/admission.py` — bounded reader/writer queues,
  fairness, deadlines, and job deduplication.
- `cortex_harness/storage/generation.py` — staging, validation, manifest
  publication, reference tracking, and safe retirement.

### Existing runtime integration

- `cortex_harness/storage/config.py`, `layout.py`, `lease.py`, `qdrant.py`,
  `migration.py`, and `__init__.py` — generation-aware physical paths,
  validated performance profiles/worker limits, owner lifecycle, and exports.
- `code-tiny/tools/graph/core/shared_runtime.py` and
  `code-tiny/tools/graph/driver/falkordb_driver.py` — route through the owner,
  bound sync execution, and make retry policy operation-aware.
- `code-tiny/tools/common/local_qdrant.py` and
  `code-tiny/tools/sync/incremental_sync.py` — explicit target/generation
  injection and writer job checkpoints.
- `code-tiny/mcp/unified_mcp.py`, `code-tiny/mcp/services/explore_service.py`,
  and backend wrappers under `code-tiny/mcp/{cplus,android,java}/` — query
  admission, pinning, freshness metadata, and structured overload errors.
- `cortex_harness/dev.py`, `scripts/mcp-lifecycle.py`, and lifecycle helpers —
  enforce one embedded worker, owner startup/drain/shutdown, status probes,
  validated performance profiles, feature flag, and rollback command.
- `doc-tiny/graph_store.py`, `doc-tiny/graphrag_ingest_langextract.py`, and
  `doc-tiny/mcp_graph_rag.py` — route document-owner graph/vector access and
  ingestion jobs through the gateway; extraction/model semantics remain
  unchanged.

### Tests and operational evidence

- New focused suites under `tests/` for manifests, gateway admission,
  generation pinning, idempotency, crash/recovery, and structured errors.
- Existing `tests/test_storage_lifecycle.py`,
  `tests/test_qdrant_adapter.py`, `tests/test_falkordb_driver_local.py`,
  `tests/test_incremental_sync_lock.py`, MCP flow tests, and local smoke
  tests updated without touching real repository stores.
- Benchmark/fault fixtures under `scripts/` or `tests/fixtures/` using
  temporary instance/owner paths only.

## Scope boundaries

### Included

- Single embedded owner/gateway per physical graph/vector target.
- Immutable staging generations and one atomic active-manifest publication.
- Bounded, observable concurrent queries with generation pinning.
- One active writer per physical target, queued concurrent submissions,
  idempotency, deduplication, cancellation checkpoints, and backpressure.
- MCP availability/status semantics, health probes, metrics, and rollback.
- Stress and fault acceptance before enabling no-downtime ingestion.

### Excluded

- Removing or weakening `StorageLease`.
- Direct multi-process access to one embedded FalkorDB/Qdrant target.
- Immediate FalkorDB/Qdrant server-mode migration or horizontal sharding.
- A distributed queue or cross-host job coordinator.
- A cross-server/federated query planner.
- Rewriting parser/analyzer semantics or legacy doc-tiny extraction/model
  semantics.
- Claiming arbitrary parallel local-Qdrant writes are safe without evidence.

## Success criteria

- MCP remains connected and serves generation `N` while generation `N+1` is
  parsed, embedded, written, validated, or failing.
- No query observes a graph/vector generation mismatch; all queries report the
  generation and source revision they served.
- A second owner process fails before opening an embedded target and receives
  actionable lease metadata.
- Same-target ingestion submissions are accepted as jobs but only one writer
  mutates the target; duplicate idempotency keys do not duplicate effects.
- Query bursts are bounded: queue depth, rejection, timeout, p50/p95/p99,
  executor saturation, event-loop lag, process/thread count, and lock
  wait/hold time are observable.
- Embedded MCP starts with exactly one owner/transport worker per role; parser
  and embedding children cannot open database paths or inherit live clients.
- The documented lock order is enforced by tests; no storage call, await, or
  parser/model work occurs while an in-process state lock is held.
- The event loop meets its lag budget under the supported mixed-load profile;
  storage work uses named bounded executors and never the default executor.
- Submit/status/cancel/follow and freshness behavior satisfy the user contract;
  overload and infrastructure failures cannot masquerade as empty results.
- Normal shutdown drains or reconciles work, closes generation handles and
  executors, and releases the process lease last; forced shutdown recovers to
  the previous committed manifest.
- A generation swap never closes a store still referenced by an in-flight
  query; failed/cancelled publication leaves the previous generation usable.
- Crash, ambiguous commit, cancellation, rollback, reader/writer, and
  saturation tests pass on temporary stores.
- The benchmark matrix establishes initial reader/writer capacities and SLOs;
  raising concurrency requires measured evidence rather than a code default.
- The existing pause/restart path remains executable until all rollout gates
  pass and is documented as the immediate rollback procedure.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Graph and vector stores publish different generations | Build/validate both under one manifest; readers use only manifest-selected pairs; fault-test every publication boundary. |
| Active generation closes during a query | Per-request generation pin and reference count; retire only after drain. |
| Query load starves ingestion or health | Separate bounded capacities, writer fairness, and reserved probe capacity. |
| Ambiguous mutation retry duplicates data | Idempotency keys, commit reconciliation, and no catch-all mutation retry. |
| Project IDs resolve to one physical target | Queue by resolved physical target key, not logical project ID. |
| Staging doubles storage usage | Measure peak disk/RSS, retain only active+rollback generations, and expose cleanup status. |
| Thread cancellation is misleading | Checkpoint cancellation around synchronous boundaries and report actual store state. |
| Default executor or nested BLAS/Torch threads oversubscribe the host | Named bounded executors, one embedding batch per device, capped native threads, and thread/process-count acceptance gates. |
| Multiple MCP workers each try to own embedded files | Enforce one transport/owner worker in embedded mode and fail startup with the configured worker count. |
| Lock-order inversion deadlocks status, query, or publication | One lock hierarchy, no lock upgrade, leaf cache locks, bounded acquisition, and deterministic multi-target ordering. |
| Graceful shutdown interrupts manifest publication | Drain state machine, bounded publication grace, active-manifest recheck on recovery, and lease release last. |
| Retrieval errors appear as successful empty results | Typed gateway errors propagate to MCP/CLI with correlation IDs; empty success is reserved for a completed zero-hit query. |
| Cold model load makes the first query appear hung | Single-flight warmup, visible `WARMING` status, optional preload, and separate storage readiness. |
| Existing parser plans collide in shared files | Keep gateway changes additive and coordinate registry/sync/MCP test edits. |
| Local mode cannot meet independent-process scale | Keep server-mode adapter seam and make scale trigger explicit in Phase 06. |

## Verification strategy

- Unit/contract: manifest schema, atomic rename/recovery, target-key
  normalization, admission limits, fairness, deadlines, duplicate jobs,
  structured error payloads, generation reference counting, lock order, no
  await/I/O-under-lock, and executor/worker configuration validation.
- Integration: real temporary local FalkorDB/Qdrant owner; reader-reader,
  reader-writer, writer-writer, generation swap, failed validation, rollback,
  process restart, and lease-conflict scenarios.
- MCP: concurrent calls to graph/vector/retrieval paths, freshness metadata,
  overload response, status/health probe, and no transport-level disconnect
  during ingestion. Assert timeout/overload/storage errors are never returned
  as empty successful results.
- Benchmark: 1/8/32/128 query clients and 1/2/4 ingestion submissions for
  same-target and isolated-target cases. Record throughput, queue delay,
  p50/p95/p99, timeout/rejection rate, event-loop lag, CPU, RSS, disk I/O,
  thread/process count, native-thread oversubscription, lock wait/hold time,
  cold/warm model latency, and stale/partial result counts.
- Rollout: feature flag off by default, canary on a disposable target,
  automatic rollback on manifest/health/SLO failure, then enable by default
  only after zero partial-generation reads and zero lease bypasses.

## Delivery command

After approval, implement with:

```text
/hi-craft plans/260807-0929-mcp-ingest-query-concurrency/plan.md
```
