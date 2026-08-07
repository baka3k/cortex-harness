---
type: plan-validation
date: 2026-08-07
---
# Plan validation: MCP ingest/query concurrency

## Validation questions and provisional decisions

### 1. Is the target embedded mode or immediate server mode?

**Decision:** Embedded owner/gateway first. The repository is actively
finishing a Docker-free local storage contract. Server mode is a scale seam,
not a prerequisite for bounded single-owner concurrency.

### 2. Are stale reads acceptable during ingestion?

**Decision:** Yes, provided they are explicitly identified. Queries serve the
last committed generation and return generation/source/freshness metadata.
Failed or cancelled staging work cannot change the active manifest.

### 3. Can same-target writes run in parallel?

**Decision:** No. Concurrent submissions become idempotent jobs; one writer
mutates one physical target. Parsing and embedding may parallelize outside the
store owner, and isolated physical targets may run concurrently.

### 4. Must the plan cover legacy doc-tiny direct access now?

**Decision:** Yes for ownership/routing, no for extraction semantics. The
document graph/vector access in `doc-tiny/graph_store.py`,
`graphrag_ingest_langextract.py`, and `mcp_graph_rag.py` must use the shared
owner/generation boundary so document ingestion cannot bypass leases or
cross-store publication. Rewriting entity extraction or document query
shaping is out of scope.

### 5. What is the rollback path?

**Decision:** Keep the existing pause/restart workflow executable behind a
feature flag until stress, fault, and canary gates pass. Rollback means stop
new jobs, continue serving the previous committed generation where safe, and
return to pause/restart if owner health or publication checks fail.

### 6. How many embedded MCP processes and storage threads are safe initially?

**Decision:** One MCP/owner process per storage role. Graph operations,
local-Qdrant operations, and same-target writes each start at concurrency `1`
per handle/target. Request preparation may overlap through named bounded
lanes. `balanced` raises any storage concurrency only after Phase 06 evidence;
multi-process scale requires server mode.

### 7. What is the lock policy?

**Decision:** Process lease first and lifetime-held, then target scheduler,
publication, generation refcount, and adapter-cache locks. There is no lock
upgrade. Database/model/parser calls and awaits occur outside in-process state
locks. Writer admission covers write/validate/publish, not repository parsing
or embedding preparation.

### 8. What should users see under overload or failure?

**Decision:** A quick, typed response with retryability, retry delay, queue
pressure, generation/freshness, and correlation ID. Infrastructure failure
must not be converted into empty search results. Ingest submission remains
asynchronous and supports status/follow/cancel; duplicate requests return the
existing job.

### 9. How does process shutdown behave?

**Decision:** First signal enters `DRAINING`, stops new admission, keeps status
available, drains active queries to deadlines, reconciles running jobs, and
finishes or safely abandons publication within a bounded grace period. Handles
and executors close before the lease is released. Second signal forces exit;
startup recovery preserves the last committed manifest.

### 10. Are the performance numbers fixed product guarantees?

**Decision:** They are provisional acceptance targets. Phase 01 records
cold/warm baselines and validates `safe`; Phase 06 may promote `balanced` and
revise exact values with a stored benchmark report. Queue, worker, thread,
memory, disk, and timeout limits remain bounded in every profile.

## Validation result

**PASS WITH CONDITIONS.** The plan is implementation-ready for contract and
gateway work. The no-downtime feature must remain disabled until paired
generation validation, reference-safe retirement, mixed-load tests, and
rollback evidence are complete.

## Open decisions before Phase 04

- Select initial reader/writer capacities from Phase 01 measurements.
- Confirm whether existing MCP schemas can accept freshness controls
  additively; metadata should remain backward-compatible if not.
- Confirm the document-owner gateway can be introduced without changing
  extraction/model behavior; if a legacy path cannot be adapted safely, keep
  that path on pause/restart and do not claim zero-downtime coverage for it.
- Select benchmark-approved `balanced` capacities; `safe` remains the default
  until the evidence report records event-loop, latency, memory, disk,
  thread/process, fairness, and soak results.
