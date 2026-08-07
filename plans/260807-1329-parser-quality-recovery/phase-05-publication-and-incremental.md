# Phase 05: Incremental queue and guarded graph publication

## Context

This phase consumes active-plan outputs. Pro*C owns its preprocessing model,
graph hardening owns safe writer/checkpoint behavior, and MCP concurrency owns
staged jobs and atomic publication. Parser recovery must integrate with those
contracts instead of creating alternate scheduling or publication paths.

## Requirements

- Queue only changed files and include-impacted dependents during incremental sync.
- Integrate recovery jobs with the owning bounded CPU/job lifecycle.
- Publish quality/provenance with file and extracted-entity records.
- Keep recoverable evidence searchable while suppressing strong relations from
  quarantined files.
- Never expose a partial mixture of baseline and repaired payloads.
- Reuse the active plans' incomplete-run, staging, validation, and rollback rules.

## Architecture

The first-pass generation carries compact quality metadata. Repair results update
the staged generation before validation or become a later idempotent generation;
they never mutate the active generation in place. Publication validation checks
quality counts, relation suppression, endpoint integrity, and source/context
fingerprints atomically.

## Related files

- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/proc_analyzer.py`
- graph writer/publication files finalized by the graph-hardening plan
- job/generation files finalized by the concurrency plan
- `tests/test_incremental_sync_parse_quality.py`
- `tests/test_cplus_graph_runtime.py`

## Implementation steps

1. Consume the completed Pro*C diagnostics and source mapping in the common
   quality adapter; do not duplicate masking or SQL extraction.
2. Build incremental queue membership from changed files, include-impact closure,
   source/context fingerprints, and cached terminal outcomes.
3. Register recovery work with the owning bounded CPU preparation lane and make
   queue/status/cancellation outcomes visible through the existing job contract.
4. Map compact provenance to File and extracted entities using additive fields.
5. Enforce publication rules: `clean` and `recovered` may publish normal evidence;
   `retry_required` publishes marked evidence; `quarantined` publishes file-level
   evidence but no strong call/inheritance/containment relations.
6. Validate before/after entity and relation counts and reject partial or
   incompatible generation fingerprints.
7. Prove failed/cancelled repair leaves the last committed generation available.

## Todo

- [ ] Integrate Pro*C quality adapter after its owning plan stabilizes.
- [ ] Make recovery queue incremental and idempotent.
- [ ] Integrate with staged jobs and bounded CPU preparation.
- [ ] Propagate quality/provenance to graph/vector consumers.
- [ ] Enforce quarantine relation policy during validation/publication.
- [ ] Add rollback, cancellation, stale-edge, and generation-integrity tests.

## Risks

Suppressing new relations without removing stale ones would preserve incorrect
history. Staged generation replacement and before/after integrity checks are
mandatory; in-place best-effort mutation is forbidden.

## Success criteria

Unchanged failures are not retried; changed/impacted files queue once; active
queries never observe a mixed generation; quarantined files retain discoverable
file evidence but no strong relations; failure rolls back cleanly.
