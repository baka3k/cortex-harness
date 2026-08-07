# Phase 06: Canary, graph recovery, and rollout

## Context

The observed graph was partially populated when diagnosis occurred. Adding
indexes to it may improve later queries but does not prove identity integrity,
relationship completeness, or safe resumability. Rollout must distinguish a
clean staging rebuild from an audited in-place repair.

## Requirements

- Preserve the last validated graph/baseline until the new build passes.
- Audit partial graphs read-only before selecting repair or rebuild.
- Canary the complete C++/Pro*C workload on disposable/staging storage.
- Provide an automatic rollback and actionable operator status.
- Remove the legacy unlabeled fallback after the rollout window.

## Architecture

Use the generation/staging boundary owned by the concurrency plan when
available. Until then, use an explicitly resolved disposable target and keep
the active target untouched. A short-lived feature flag may select the new
contract during canary; the end state is one automatic hardened path.

## Related files

- `cortex_harness/dev.py` and sync CLI/status output
- `code-tiny/tools/sync/incremental_sync.py`
- Generation/manifest components from the concurrency plan
- Plan-scoped rollout and benchmark reports

## Implementation steps

1. Stop or cancel an old run through supported lifecycle handling; do not kill
   only the database while a client is still awaiting a socket response.
2. Record the old run as incomplete and capture graph/index/count/duplicate/
   orphan diagnostics without mutating it.
3. Run a small disposable canary, then a full 20k-file C++/Pro*C staging build
   with automatic preflight and the hardened writer.
4. Validate schema fingerprint, query plans, node/edge reconciliation, project
   isolation, representative MCP queries, parser summary, and performance SLOs.
5. Publish/select the validated generation only after all gates pass. Retain
   the previous validated generation for bounded rollback.
6. If an in-place repair is requested, require the same audit/validation report
   as a clean build; otherwise choose the safer clean full scan.
7. Enable the new contract by default, monitor slow-query/progress/integrity
   metrics, then remove the feature flag and old unlabeled writer.
8. Update operator documentation: normal use is automatic; manual schema setup
   is doctor/repair only, with exact status and recovery commands.

## Todo

- [ ] Audit and mark the current partial run without destructive cleanup.
- [ ] Complete full-source staging canary (disposable schema/query/scale canaries passed).
- [ ] Verify all schema, integrity, query, parser, and performance gates.
- [ ] Publish with a tested rollback to the last validated generation.
- [x] Enable the hardened path by default.
- [x] Remove the legacy fallback and document automatic behavior.

## Risks

An in-place graph may contain duplicates or silently missing relationships from
older writers. Its index state alone is not proof of correctness. Disk capacity
must be checked before retaining active, staging, and rollback generations.

## Success criteria

The real workload completes without manual indexes or silent queries; the
published graph passes integrity and representative query checks; rollback is
proven; the old unlabeled path is removed rather than becoming permanent debt.
