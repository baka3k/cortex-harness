---
type: red-team review
date: 2026-08-07
verdict: caution
---
# Red-team review: graph ingestion write-path hardening

## Summary

The plan addresses the measured root cause, but implementation is safe only if
schema readiness, dynamic identifiers, dirty-graph identities, ambiguous
timeouts, and cross-plan ownership remain explicit. All high-severity findings
below have been incorporated into the plan and phase acceptance criteria.

## Findings

| Severity | Challenge | Required mitigation | Incorporated |
| --- | --- | --- | --- |
| Critical | Index creation alone leaves unlabeled `MATCH` queries on all-node scans. | Make label-qualified compilation and explain-plan gates inseparable from schema preflight. | Plan target contract; Phases 02, 03, 05. |
| Critical | Dynamically interpolated labels/types create Cypher-injection and invalid-plan risk. | Accept identifiers only from a validated manifest allowlist; parameterize values. | Phases 02-03. |
| Critical | DDL success does not mean an index is operational. | Inspect exact label/property/status and bound readiness wait before any mutation. | Phase 02. |
| High | Existing duplicate IDs can make uniqueness DDL fail or create ambiguous endpoints. | Audit first; fail with a report; use range index until uniqueness is proven; never auto-delete/merge. | Phases 01-02, 06. |
| High | Missing endpoints can silently reduce relationship counts. | Reconcile expected/matched/unresolved and default required edges to fail-closed. | Phases 03, 05. |
| High | A write timeout may have committed or may still be rolling back. | Treat as ambiguous, use idempotent readback, and never blindly retry. | Phase 04. |
| High | Resume checkpoints become invalid after grouping/query changes. | Fingerprint schema and query shape; reject incompatible checkpoints. | Phases 03-04. |
| High | Tests or rollout could corrupt registered user stores. | Use resolved disposable/staging paths and assert user-store isolation. | Phases 01, 05-06. |
| Medium | Indexing every label adds write/storage cost. | Activate only the contracts required for the current analyzer/writer. | Phase 02. |
| Medium | Database heartbeats could queue behind the same expensive query. | Emit client-side lifecycle heartbeat without issuing competing DB work. | Phase 04. |
| Medium | The concurrency plan may duplicate staging/publication work. | This plan owns schema/query/integrity; concurrency plan owns admission/generations/publication. | Cross-plan metadata. |
| Medium | A feature flag could preserve the defective writer indefinitely. | Make it rollout-only and remove the legacy unlabeled path after gates pass. | Phase 06. |

## Recommendations

- Implement Phase 02 and Phase 03 as one release boundary: do not ship automatic
  indexes while retaining unlabeled endpoint queries or vice versa.
- Make `GRAPH.EXPLAIN` structure the non-negotiable gate; use wall-clock
  thresholds as hardware-qualified supporting evidence.
- Prefer a clean staged full rebuild for the current partial graph unless an
  identity/integrity audit proves in-place repair equivalent.
- Keep parser remediation out of the critical write-path patch while improving
  parser summary definitions and follow-up evidence.

## Unresolved questions

None blocks implementation. Phase 01 must supply the exact optional-edge policy
and hardware-qualified baseline before Phase 05 locks final absolute latency.

