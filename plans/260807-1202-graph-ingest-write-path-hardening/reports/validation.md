---
type: plan validation
date: 2026-08-07
result: passed-with-conditions
---
# Plan validation: graph ingestion write-path hardening

## Summary

The plan is implementable, evidence-backed, and addresses the recurring defect
class rather than one log line. It passes planning validation with execution
conditions that are already represented as phase gates.

## Validated decisions

- **Index ownership:** automatic and tool-managed; manual setup is not a normal
  prerequisite.
- **Ordering:** eager orchestrator preflight plus writer-local enforcement,
  both before mutation.
- **Query shape:** label-qualified endpoint matches are mandatory; indexes
  without query changes do not satisfy the plan.
- **Provider scope:** provider-neutral contract with FalkorDB-specific readiness
  and explain-plan acceptance, preserving Neo4j semantics.
- **Recovery:** partial/incomplete runs do not publish or advance baselines;
  clean staged rebuild is the default recovery for the observed graph.
- **Parser warning:** independently measured and reported; not treated as the
  cause of the FalkorDB stall.
- **Plan ownership:** this plan owns schema/query/integrity, while the active
  concurrency plan owns generation publication and the Pro*C plan owns parser
  semantics.

## Execution conditions

1. Phase 01 must freeze identity and optional-edge policies before manifest
   constraints are applied.
2. Phase 02 and Phase 03 must deploy together behind the canary boundary.
3. Critical query-plan tests must run on real temporary FalkorDB and fail on
   endpoint all-node scans/Cartesian products.
4. No uniqueness constraint or in-place repair may mutate a dirty graph before
   the duplicate/integrity audit is reviewed.
5. Absolute timing thresholds may be calibrated from the recorded hardware
   baseline, but structural plan and scaling gates cannot be waived.

## Coverage check

| Area | Covered by |
| --- | --- |
| Root cause and reproducibility | Research report; Phase 01 |
| Automatic indexes/readiness | Phase 02 |
| Indexable relationship writes | Phase 03 |
| Integrity and idempotency | Phases 03-05 |
| Timeout/restart/progress | Phase 04 |
| Regression and scale | Phase 05 |
| Partial graph and rollout | Phase 06 |
| Parser diagnostic clarity | Phases 01 and 04 |
| Cross-plan ownership | Main plan and bidirectional metadata updates |

## Final assessment

Proceed through the phases in order. The implementation should not short-cut
to manual index creation, larger timeouts, or a C++-only patch because each
would leave the measured defect class able to recur.
