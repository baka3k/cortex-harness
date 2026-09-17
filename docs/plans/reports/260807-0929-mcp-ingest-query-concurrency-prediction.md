---
type: prediction
date: 2026-08-07
depth: deep
verdict: CAUTION
proposal: Keep MCP available during ingestion and support bounded concurrent queries and ingestion submissions.
---

# Prediction: MCP query and ingestion concurrency

## Executive summary

The goal is achievable, but the current embedded-storage architecture is not safe for direct multi-process concurrency. Many clients may query one MCP owner without triggering the file lease, but the current shared synchronous driver, threaded retrieval paths, and Qdrant local mode do not yet provide a verified high-concurrency contract.

The recommended design is one long-lived storage owner plus immutable data generations: ingestion builds and validates a staging FalkorDB/Qdrant generation, then atomically publishes it while MCP continues serving the previous generation. Same-store writers must be queued and serialized; query concurrency must be bounded and tested. For multi-process or horizontal scale, move FalkorDB and Qdrant to server mode rather than bypassing embedded-store ownership.

## Direct answers

| Question | Answer |
|---|---|
| Can MCP remain available while ingestion runs? | Yes. Serve the last committed generation while building a new generation, then atomically switch the active generation. |
| Can many clients query concurrently? | They can submit concurrent queries to one MCP process without a lease conflict. Actual parallel execution is currently unverified and should be bounded until the shared driver and local clients pass concurrency tests. |
| Will many query clients necessarily error? | No. The ownership lease is process-level, so clients sharing one MCP owner do not each open the files. Without admission control, however, they can cause blocking, high tail latency, timeouts, or thread-pool exhaustion. |
| Can many ingestions run concurrently? | Accept requests concurrently, but execute only one writer per physical store/generation target. Parsing and embedding can run in parallel outside the store owner; writes to isolated stores may run in bounded parallel. |
| Should the lease be removed for the same machine/user? | No. The lease protects process ownership, not OS-user authorization. Removing it permits unsafe duplicate embedded database owners. |

## Evidence from the current implementation

- [`StorageLease.acquire`](../../cortex_harness/storage/lease.py#L44) takes an exclusive, non-blocking OS lock. A second process opening the same physical target is intentionally rejected.
- [`get_shared_graph_driver`](../../code-tiny/tools/graph/core/shared_runtime.py#L44) caches one graph driver per target inside a process. This prevents a second embedded owner in that process, but does not establish operation-level concurrency safety.
- [`FalkorDBDriver.execute_query`](../../code-tiny/tools/graph/driver/falkordb_driver.py#L309) delegates to synchronous `graph.query` calls. No bounded executor, read/write gate, or operation-level mutex is visible at this boundary.
- [`_run_bridge_query`](../../code-tiny/mcp/unified_mcp.py#L1156) intentionally reuses the process-global driver because creating a second embedded driver conflicts with storage ownership.
- [`ExploreService._run_retrieval`](../../code-tiny/mcp/services/explore_service.py#L438) uses `asyncio.to_thread`, so multiple requests may invoke shared synchronous dependencies from worker threads.
- [`cortex_harness.storage.qdrant`](../../cortex_harness/storage/qdrant.py#L39) protects client creation with `_client_lock`, but the visible lock does not serialize ordinary Qdrant operations.
- The existing local-storage plan explicitly says that multiple MCP processes must never open the same embedded store and that non-owners must use the owner interface ([plan](../260806-1648-local-file-storage/plan.md#L30)).

External implementation contracts support the same boundary:

- FalkorDB supports parallel read-only queries per graph and serializes writes FIFO; readers see a consistent pre-write snapshot ([FalkorDB concurrency documentation](https://docs.falkordb.com/design/concurrency.html)).
- The FalkorDB Python client provides async pooled usage, including concurrent operations with `asyncio.gather` ([falkordb-py](https://github.com/FalkorDB/falkordb-py)).
- FalkorDBLite exposes async APIs within one owning process, but this does not authorize separate processes to open the same embedded store ([FalkorDBLite 0.10.0](https://github.com/FalkorDB/falkordblite/blob/v0.10.0/README.md)).
- Qdrant local mode takes an exclusive storage lock and explicitly recommends Qdrant server when concurrent access is required ([Qdrant local source, v1.18.0](https://github.com/qdrant/qdrant-client/blob/v1.18.0/qdrant_client/local/qdrant_local.py)). Local mode is positioned for development, prototyping, and testing; server mode is the scaling path ([Qdrant client](https://github.com/qdrant/qdrant-client), [Qdrant server](https://github.com/qdrant/qdrant)).

## Consensus agreements

1. Direct multi-process access to one embedded physical store is unsafe; the lease must remain fail-closed.
2. Exactly one process or service should own each embedded FalkorDB/Qdrant store set.
3. Same-store ingestion must have one active writer with bounded, idempotent queueing, deduplication, cancellation, and backpressure.
4. Query concurrency must be bounded, observable, and protected by deadlines and overload handling.
5. Graph and vector updates need one publication boundary. Queries must never observe a new graph generation paired with an old vector generation, or vice versa.
6. Mixed-load, crash, retry, cancellation, and rollback tests are required before declaring concurrent operation safe.

## Conflicts and resolution

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
|---|---|---|---|---|---|---|
| Immediate backend | Add a storage gateway; server mode for scale | Keep one authenticated owner | Use one dispatcher until benchmarks justify server mode | Keep MCP transport alive | Prefer the smallest owner/queue change | Phase 1 uses one embedded owner/gateway; phase 2 adds server mode only when scale or SLOs require it. |
| Live ingestion model | Immutable generations preferred | Generation publication is required for integrity | Snapshot swap protects query latency | Serve last committed generation with freshness metadata | Accept stale reads if explicit | Use immutable graph/vector generations and an atomic active-generation manifest. |
| Query parallelism | Bounded readers through gateway | Treat current shared-driver behavior as unsafe | Benchmark before increasing concurrency | Give predictable overload responses | Queuing can be better than parallelism | Start with a conservative bounded reader semaphore; migrate to async pooled drivers and increase only from measured evidence. |
| Writer parallelism | One writer per physical target | Serialize and make idempotent | Parallel only for disjoint projects/stores | Queue jobs with visible status | Concurrent acceptance is not concurrent execution | One active writer per physical store; parallelize parsing/embedding and writes to truly isolated targets only. |
| Pause/restart workaround | Replace with owner service | Retain until controls pass | Useful fallback | Hard outage is poor UX | Lowest-risk current behavior | Keep it as a rollback path while introducing the gateway and generation cutover. |

## Risk summary

| Risk | Severity | Raised by | Required mitigation |
|---|---|---|---|
| Bypassing the lease lets multiple embedded owners corrupt or invalidate the store | Critical | All personas | Preserve the OS lease; route all access through one owner or server-mode databases. |
| Graph and vector stores expose different generations | Critical | Architect, Security, UX, Devil's Advocate | Build, validate, and publish both stores under one generation manifest. |
| Ambiguous write failure is retried and duplicates a non-idempotent mutation | High | Security, Devil's Advocate | Retry reads freely; require ingestion IDs/deduplication and retry writes only when non-commit is proven. |
| Query bursts exhaust threads or starve ingestion | High | Security, Performance, UX | Separate bounded executors/queues, per-client limits, deadlines, fairness, and overload responses. |
| Long ingestion blocks live readers | High | Architect, Performance, UX | Serve an immutable committed generation while staging the next one. |
| Generation swap closes a store while a query still uses it | High | Security, UX | Pin each request to a generation and retire it only after its in-flight reference count reaches zero. |
| Current concurrent reader behavior is assumed safe without proof | High | All personas | Add reader-reader and mixed-load stress tests before raising concurrency. |
| Clients cannot tell whether results are stale | Medium | UX | Return generation, source revision, last commit time, and ingestion state in status/query metadata. |

## Persona findings

### Systems architect

- The current private, process-global driver coupling is not a stable concurrency boundary.
- A simple read/write lock would protect integrity but could still make long writes block or starve queries.
- Introduce a `StoreGateway` owned by one long-lived service, keyed by resolved physical target.
- Expose query, ingest, publish-generation, health, and lifecycle operations through that boundary.
- Retain the coordinator and generation manifest even after migrating to server databases, because separate graph and vector systems do not share one transaction.

### Security engineer

- The current same-process read/write contract is unproven; unsafe interleaving could cause lost updates, partial visibility, or checkpoint races.
- A catch-all retry is dangerous for non-idempotent writes after an ambiguous commit.
- Authenticate the gateway, authorize project-to-store mapping server-side, and keep query paths read-only.
- Apply concurrency limits, query-cost limits, rate limits, timeouts, circuit breakers, and writer fairness.
- Fault-test process kills, checkpoints, ambiguous commits, rollback, and cross-store generation consistency.

### Performance engineer

- Moving synchronous work to threads prevents event-loop blocking but can create unbounded queues, high p99 latency, and ineffective cancellation.
- Separate query and ingestion admission/executors, with bounded capacity and per-project fairness.
- Benchmark 1/8/32/128 query clients and 1/2/4 ingestion submissions, both same-target and isolated-target.
- Measure throughput, p50/p95/p99 latency, queue delay, event-loop lag, executor saturation, CPU, RSS, disk I/O, lease failures, and partial/stale-result exposure.
- Immutable generations may temporarily approach 2x storage, but provide predictable query latency and rollback.

### UX engineer

- MCP should remain connected and serve the last committed snapshot instead of failing at the transport level.
- Expose `served_generation`, `source_revision`, `last_committed_at`, `ingestion_state`, queue position, and freshness; support `require_fresh` or `min_generation`.
- Return structured `OVERLOADED`, `INGESTION_ALREADY_RUNNING`, or `STORE_MAINTENANCE` responses rather than raw lease errors.
- A generation swap must pin existing queries to their original generation; failed/cancelled ingestion must leave the previous generation available.
- Readiness requires health and representative query probes, not merely successful process creation.

### Devil's advocate

- More concurrent execution is not automatically better; bounded queuing can provide better availability and integrity.
- The current project-level run lock may not cover different scopes that resolve to the same physical store.
- Accept concurrent ingestion requests as jobs, but do not execute multiple same-store writers.
- If true simultaneous multi-process access is mandatory, embedded mode is the wrong substrate; use server-mode databases.
- Keep the pause/restart workflow as a safe fallback until the new design passes stress and fault testing.

## Recommended design

```text
MCP clients ───────────────┐
                          v
                    Store Gateway/Owner ─────> active generation (read-only)
                          ^                         ├─ FalkorDB generation N
Ingestion API ─> job queue─┘                         └─ Qdrant generation N
      │
      ├─ parallel parse/embed workers
      └─ single writer ─> staging generation N+1 ─> validate ─> atomic publish
```

The owner remains available during ingestion. Queries are pinned to generation `N`; ingestion writes only to staging generation `N+1`. After both graph and vector validation succeeds, one small manifest update publishes `N+1`. Existing requests drain from `N`; failed ingestion discards or retains the staging generation for diagnostics without changing visible data.

## Numbered recommendations

1. **Do not weaken `StorageLease`.** It is the correct protection for embedded process ownership; machine/user ownership does not make two database processes safe.
2. **Add a single storage-owner/gateway boundary.** MCP and ingestion workers must call it instead of opening local database files directly.
3. **Introduce a per-physical-store ingestion scheduler.** One active writer, bounded queue, job IDs, idempotency keys, deduplication, cancellation checkpoints, and writer fairness.
4. **Publish immutable graph/vector generations atomically.** This is the main mechanism that keeps MCP available without exposing partial ingestion.
5. **Bound query concurrency.** Start conservatively, use separate capacity from ingestion, add timeouts/cancellation, and return structured overload responses.
6. **Adopt async/pool-aware storage adapters.** Use FalkorDB's async client or a bounded executor; serialize Qdrant local operations until proven safe, or use Qdrant server for real concurrent clients.
7. **Remove unconditional retry from mutation paths.** Make all ingestion writes explicitly idempotent before retrying ambiguous failures.
8. **Add freshness and operational status.** Make the active generation and ingestion progress visible to both humans and automation.
9. **Move to server mode when horizontal concurrency is required.** FalkorDB and Qdrant servers are the appropriate substrate for multiple independent processes; keep generation publication for cross-store consistency.

## Next steps for a CAUTION verdict

1. Write an ADR choosing between embedded owner/gateway and immediate server mode; default to the embedded gateway for a Docker-free developer workflow.
2. Define the generation manifest, query pinning/ref-count lifecycle, ingestion job state machine, and failure semantics.
3. Implement the gateway behind the current pause/restart path so rollback remains available.
4. Add reader-reader, reader-writer, writer-writer, saturation, cancellation, crash, ambiguous-commit, generation-swap, and rollback tests.
5. Run the mixed-load benchmark matrix and set explicit query-latency and ingestion-throughput SLOs.
6. Enable no-downtime ingestion only after tests show zero lease conflicts, zero partial-generation reads, bounded queues, and successful rollback.

## Final verdict

**CAUTION — proceed with an architectural boundary, not a lease bypass.** Concurrent query submissions and zero-downtime ingestion are viable. Direct concurrent opening of one embedded store and concurrent same-store writers are not.
