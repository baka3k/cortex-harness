# Phase 02: StoreGateway, leases, and bounded admission

## Context

The current shared runtime prevents duplicate client creation but leaves
operation concurrency implicit. A single owner boundary must own both graph
and vector clients and expose bounded work admission.

## Requirements

- Preserve and surface `StorageLeaseConflictError` diagnostics.
- Reuse one graph driver and one Qdrant client set per physical target.
- Provide separate bounded reader and writer capacity, queue limits,
  deadlines, fairness, and reserved health capacity.
- Track generation references so lifecycle operations cannot close an active
  store while a query is using it.
- Enforce one embedded MCP/owner worker, the documented lock order, and named
  bounded executors; direct/default-executor store access is forbidden.
- Provide deterministic startup, drain, shutdown, and child-worker isolation.

## Architecture

Implement `StoreGateway` as the only runtime owner of leases, client handles,
admission queues, generation pins, and shutdown. Keep the public graph-driver
and Qdrant adapter result contracts stable; adapt them behind the gateway.

## Related files

- New: `cortex_harness/storage/gateway.py`,
  `cortex_harness/storage/admission.py`.
- Update: `cortex_harness/storage/__init__.py`, `qdrant.py`,
  `code-tiny/tools/graph/core/shared_runtime.py`, and driver lifecycle code.
- Tests: gateway ownership, reader bounds, writer fairness, timeout,
  shutdown, duplicate initialization, and lease conflict.

## Implementation steps

1. Construct a gateway from resolved storage configuration and validate all
   target paths before opening clients.
2. Add short state locks and enforce the lock hierarchy. Instrument wait/hold
   time and assert that graph/Qdrant calls, awaits, model work, and callbacks
   happen after locks are released.
3. Add named bounded graph-read, vector-read, storage-write,
   control/health, and CPU-preparation lanes. Start each embedded store
   handle at concurrency `1`; never use `asyncio.to_thread()` or the default
   executor for store operations.
4. Add weighted-fair reader/writer admission with non-blocking overload or
   bounded wait, explicit deadlines, count+estimated-byte queue capacity,
   query-cost limits, per-client limits, and a reserved health lane. Use
   bounded write batches and stop new preparation at memory/disk/query-pressure
   high watermarks.
5. Add `pin_generation()`/release semantics, request correlation, and
   cancellation reconciliation for non-preemptive synchronous calls.
6. Route graph and vector operations through gateway-owned handles without
   opening another local client in backend or child-worker modules.
7. Enforce one owner/transport worker; ensure parser/embed children use
   immutable artifacts and cannot inherit or resolve owner storage paths.
   Acquire paired graph/vector leases by canonical path order and unwind a
   partial acquisition before surfacing an owner conflict.
8. Make first-signal shutdown enter `DRAINING`, persist work, close executors
   and clients in dependency order, and release leases last. Verify forced
   exit is recoverable from manifest/job state.
9. Publish lock-free/atomic status snapshots; keep liveness cheap and refresh
   representative readiness probes asynchronously on the reserved control
   lane.

## Risks

Wrapping synchronous work in an unbounded executor would hide overload and
still starve ingestion. Keep the queue capacity explicit, prevent nested
Torch/BLAS/parser thread oversubscription, and test process/thread counts under
saturation.

## Success criteria

One owner serves multiple bounded readers, one same-target writer is admitted,
second processes fail before store open, and all lifecycle paths release
references and leases deterministically. Event-loop lag, reserved health
capacity, lock-order tests, and one-worker enforcement meet the main-plan
budgets.
