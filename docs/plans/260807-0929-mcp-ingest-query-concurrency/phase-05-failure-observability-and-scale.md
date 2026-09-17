# Phase 05: Idempotency, observability, cleanup, and scale seam

## Context

Concurrency changes are unsafe without explicit ambiguous-commit handling,
operational visibility, and a cleanup policy that cannot delete data still in
use.

## Requirements

- Make mutation retries idempotent or reconcile them before retry.
- Expose queue depth, active readers/writers, generation, freshness, lease,
  latency, timeout, rejection, and cleanup state.
- Add health/readiness probes that execute representative graph and vector
  checks, not just process-start checks.
- Define an adapter seam for future server-mode graph/vector clients without
  enabling them in this release.
- Expose process/thread/native-thread counts, event-loop lag, executor queue
  depth, lock wait/hold, model warmup/cache, disk headroom, and drain status.

## Related files

- `cortex_harness/storage/{gateway,admission,generation}.py`.
- `code-tiny/tools/graph/driver/falkordb_driver.py`,
  `tools/common/local_qdrant.py`, `scripts/validate_retrieval.py`.
- `cortex_harness/dev.py`, lifecycle scripts, MCP health/status helpers, and
  documentation under `docs/`.

## Implementation steps

1. Add idempotency records keyed by target and ingestion request identity;
   reconcile ambiguous writes from source revision/generation state.
2. Add structured logs/metrics and bounded diagnostic payloads with no secret
   leakage. Correlate user response, queue entry, executor operation, lock
   timing, storage call, job, and generation.
3. Add separate liveness/readiness, user status/doctor, graceful drain, and
   generation cleanup commands with dry-run support. Status must remain
   responsive through overload and `DRAINING`; deep readiness probes are
   periodic/cached rather than executed synchronously by each status caller.
4. Detect event-loop stalls, thread/process leaks, nested native-thread
   oversubscription, model cache growth, low disk/memory headroom, and queries
   pinned too long to retired generations.
5. Add fault injection for process kill, checkpoint failure, ambiguous commit,
   disk-full/permission failure, cancellation, and manifest corruption.
6. Document the server-mode adapter requirements and the measured trigger for
   leaving embedded mode.

## Risks

Observability code can become an accidental second state store. The manifest
and job record remain authoritative; metrics and logs are projections.

## Success criteria

Operators can explain what generation is served, whether ingestion is queued or
committed, why work was rejected, and how to roll back without opening a
second embedded owner.
