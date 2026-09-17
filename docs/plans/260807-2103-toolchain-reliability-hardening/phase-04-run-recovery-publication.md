# Phase 04: Transactional run, recovery, and publication

## Context

The current incremental run can mark state dirty after graph mutation, but
readers and later retries need a stronger guarantee: the prior committed
generation remains authoritative until graph, vector, integrity, and quality
validation all pass. This phase coordinates the graph journal with the staged
generation/StoreGateway plan.

## Requirements

- One stable run/job identity across CLI, incremental sync, analyzer children,
  journal operations, staging generation, and artifacts.
- Last committed graph/vector generation remains readable during ingestion.
- Baseline advances only after atomic publication.
- Resume compatible unfinished work without reparsing/rewriting ACKed effects.
- Quarantine incompatible/corrupt state; never replay blindly.
- Cancellation, source changes, disk pressure, and process death have explicit
  recovery outcomes.

## Architecture

StoreGateway owns request admission and staged generation publication. The graph
journal owns durable mutation batches and reconciliation. Incremental sync owns
source candidate/baseline state. Connect them through the common run state
machine and fingerprints; do not merge their storage responsibilities.

Publication prerequisites:

- source revision/inventory still matches;
- analyzer accounting and quarantine policy pass;
- all required journal operations are ACKed/reconciled;
- graph/vector barriers and generation fingerprints pass;
- no unresolved required endpoint, ambiguous mutation, or corrupt artifact;
- disk/retention and rollback generation are healthy.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- graph journal/barrier modules from the graph hardening plan
- StoreGateway/generation/admission modules from the concurrency plan
- incremental state/lock helpers
- `cortex_harness/dev.py` lifecycle and status integration
- graph/vector generation manifest and cleanup paths

## Implementation Steps

1. Propagate one run ID and immutable configuration/source/schema fingerprints
   through every child and artifact.
2. Map journal and generation states to the common run state machine.
3. Stage graph/vector effects without changing the active manifest.
4. Add publication prerequisite evaluation and atomic baseline+generation
   commit ordering.
5. Implement compatible resume, expired lease fencing, source-change abort,
   and incompatible/corrupt quarantine.
6. Add cancel checkpoints around parse, validate, batch, verify, and publish;
   reconcile non-preemptive store calls.
7. Implement bounded staging/journal/artifact retention and safe cleanup.
8. Prove rollback to the prior generation without broad deletion.

## Todo

- [ ] Stable run identity crosses all process/storage boundaries.
- [ ] Active readers never observe a staging or graph/vector-mismatched generation.
- [ ] Baseline cannot advance before atomic publication.
- [ ] Crash at every journal/publication boundary resumes or rolls back safely.
- [ ] Source changes during scan produce typed abort and preserve prior state.
- [ ] Disk-full/corrupt/incompatible recovery fails closed with safe action.
- [ ] Cleanup is bounded, target-specific, and tested.

## Risks

- Two durability systems can disagree. Use fingerprints and publication
  prerequisites; keep clear ownership and fault-test every boundary.
- Staging doubles disk. Preflight capacity and retain only bounded active,
  rollback, and diagnostic generations.
- Cancellation of synchronous storage is ambiguous. Record cancellation, wait
  or reconcile completion, and report actual outcome.

## Success Criteria

- No failed run changes active generation or last-good baseline.
- A killed full scan resumes compatible work without duplicate effects.
- Graph/vector generations publish as one validated unit.
- Rollback remains available and is rehearsed in automated canaries.
- State/status accurately names queued, active, reconciling, failed, and
  published runs.
