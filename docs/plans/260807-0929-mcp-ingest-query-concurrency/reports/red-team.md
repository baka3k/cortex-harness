---
type: red-team-review
date: 2026-08-07
---
# Red-team review: MCP ingest/query concurrency

## Summary

The plan is directionally safe, but zero-downtime publication is only
credible if the active manifest is the sole pair-selection authority and
staging paths are truly distinct from active paths. The review found no reason
to bypass the lease. It found eleven risk areas that must remain explicit
gates before rollout.

## Findings

### 1. Manifest atomicity is not a database transaction — high

Graph and Qdrant are separate stores. A successful graph write followed by a
process kill before vector validation must never become visible as a new
generation. The plan now requires both stores to be validated before one
manifest rename, recovery to ignore `BUILDING`/`VALIDATING` generations, and
queries to resolve both paths from the same manifest.

### 2. Client caches can defeat generation isolation — high

The existing graph and Qdrant caches are keyed by physical path. A generation
implementation that mutates a path in place or reuses a cached client after a
swap would violate the pin/ref-count contract. Phase 03 therefore requires
explicit generation paths and generation-owned handles; retirement cannot
close a handle still referenced by a request.

### 3. Project-level locking is insufficient — high

Two logical projects can resolve to the same physical owner target. The
gateway and ingestion scheduler must key locks, queues, deduplication, and
metrics by the resolved physical target tuple. `project_id` remains a logical
scope and display field only.

### 4. Thread cancellation and mutation retry are easy to misreport — high

`asyncio.to_thread` does not stop an already-running synchronous database
operation. A cancelled request may still commit. Similarly, the current
single-shot retry in `FalkorDBDriver` cannot be applied indiscriminately to
mutations. Phase 05 requires checkpointed cancellation, ambiguous-commit
reconciliation, and read-only retry classification.

### 5. “More concurrency” can worsen availability — medium

Increasing executor size without a queue budget can increase tail latency and
starve health/publish work. Phase 02 keeps reader/writer capacities separate,
and Phase 06 makes measured SLOs the only reason to raise them.

### 6. Document-owner bypass remains a scope risk — medium

The indexed evidence shows `doc-tiny` ingestion still opens graph/vector
stores directly. The plan therefore includes owner/generation routing for
`doc-tiny` while explicitly excluding extraction/model rewrites. If that
routing is deferred, the release must not claim that all ingestion paths are
safe during live MCP service.

### 7. Multiple MCP workers recreate the ownership conflict — critical

An HTTP/MCP server configured with multiple worker processes would make each
worker open the same embedded files. Embedded mode must enforce one
owner/transport worker per role and fail startup if a launcher requests more.
Scaling request acceptance happens inside the gateway; independent process
scale requires server-mode stores.

### 8. Lock-order inversion can deadlock health and publication — high

The gateway adds scheduler, publication, reference-count, and cache locks on
top of the process lease. Without one order, a query release, cleanup, and
publication can wait on each other while health also stalls. The plan now
defines a hierarchy, forbids lock upgrades, keeps cache/ref locks leaf-only,
and prohibits awaits or database calls while state locks are held.

### 9. Default executors and native threads can oversubscribe the host — high

`asyncio.to_thread`, parser pools, Torch, BLAS, and OpenMP can independently
create threads. A small burst can therefore produce event-loop lag, swap, and
tail-latency collapse even when request admission is bounded. Store work must
use named bounded lanes; parser workers leave one core for MCP; embedding is
one batch per device initially; native thread counts are capped and measured.

### 10. Empty-result fallback hides outages — high

`ExploreService` currently catches retrieval exceptions and returns an empty
list. Under overload or store failure this tells the user that no result
exists. Phase 04 must propagate typed failure/overload/cancellation responses;
only a successful zero-hit query may return an empty result.

### 11. Signal handling can corrupt the user experience — high

The current MCP servers treat the first signal mainly as an instruction to
send a second force signal. The owner needs an explicit `DRAINING` state,
bounded query/publication grace, persisted job state, dependency-ordered close,
and lease release last. Recovery must ignore incomplete staging and retain the
last committed manifest after forced exit.

## Required gates

- No generation publish without paired graph/vector validation.
- No active-path mutation during staging.
- No store close/delete while a generation reference is held.
- No same-target writer parallelism, even when project IDs differ.
- No default increase in concurrency until the benchmark matrix passes.
- Pause/restart remains an operational rollback until all gates pass.
- Exactly one embedded owner/transport worker per role; no live store handle
  crosses a child-process boundary.
- Paired graph/vector leases use canonical path order and partial acquisition
  always unwinds before returning an error.
- Lock hierarchy and no-await/I/O-under-lock rules are executable tests, not
  documentation only.
- No default executor or unbounded/nested thread pool performs store work.
- Timeout, overload, cancellation, and storage failure cannot be returned as
  empty successful search results.
- First-signal drain and forced-exit recovery pass at every publication stage.

## Disposition

The plan is accepted with the above constraints. No lease bypass, broad
refactor, or immediate server-mode migration is authorized by the report.
