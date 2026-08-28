# Graph ingest write-path hardening — 2026-08-28

## Context

The work since baseline `470acacbac8dd32f1c0c8367fc8470234350c816` closes the graph-ingest plan for the production-required C++/Pro*C lane. The original failure combined unindexed relationship matching with process-local deferral that could not prove ordering, conservation, or safe recovery after interruption ([plan](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md), `plans/260807-1202-graph-ingest-write-path-hardening/plan.md:34-51`).

## Change

Journal schema v3 adds typed node/edge identities, producer completion, manifest dispositions, edge endpoints, conservation state, a sealed endpoint audit, and manifest-bound graph receipts (`code-tiny/tools/graph/journal/models.py:11-61`, `code-tiny/tools/graph/journal/sqlite_store.py:203-368`, `code-tiny/tools/graph/journal/sqlite_store.py:461-550`, `code-tiny/tools/graph/journal/guard.py:70-78`). The runtime gates every non-node operation on both `phase:nodes` and `audit:endpoints`, and closes node production only after producer completion (`code-tiny/tools/graph/journal/runtime.py:25-53`, `code-tiny/tools/graph/journal/runtime.py:168-174`, `code-tiny/tools/graph/journal/runtime.py:289-295`).

Required mode is enabled only for the migrated `cplus` lane; all other 36 sync-selectable graph-writing lanes fail closed and remain shadow-only until their trusted operations and lifecycle migrate (`code-tiny/tools/sync/incremental_sync.py:247-260`, `code-tiny/tools/sync/incremental_sync.py:2775-2790`, `plans/260807-1202-graph-ingest-write-path-hardening/reports/required-mode-mutation-inventory.md:7-24`).

The real 24-file `procsample` canary recovered an interrupted run, ACKed 53/53 batches, sealed its endpoint audit before the first edge lease, and preserved exact local/remote parity at 646 business nodes, 1,327 relationships, and 24 files (`plans/260807-1202-graph-ingest-write-path-hardening/reports/final-validation-2026-08-28.md:21-51`). A deterministic generated 20,186-file fixture then completed the file-backed required-mode scale gate in 49.31 seconds with 122/122 batches ACKed and 60,559 rows conserved (`scripts/generate_graph_ingest_scale_fixture.py:12-15`, `scripts/generate_graph_ingest_scale_fixture.py:35-116`, `plans/260807-1202-graph-ingest-write-path-hardening/reports/final-validation-2026-08-28.md:60-83`). The final regression passed 1,455 tests and 270 subtests with 10 skips and no failures (`plans/260807-1202-graph-ingest-write-path-hardening/reports/final-validation-2026-08-28.md:85-103`).

## Impact

**Risk level: High.** This changes write eligibility, restart reconciliation, integrity evidence, and rollout policy for graph ingestion. Fail-closed lane gating limits production exposure to the validated C++/Pro*C contract, while the read-only audit distinguishes eligible graphs from incomplete graphs without mutation (`scripts/audit_graph_ingest.py:23-108`). The original historical 20,186-file corpus was unavailable, so its semantic distribution and warm-baseline comparison were explicitly waived; schema, ordering, recovery, integrity, backend, and operational-scale gates were not waived (`plans/260807-1202-graph-ingest-write-path-hardening/reports/final-validation-2026-08-28.md:15-19`).

## Decision

Accept the plan as complete with an external-source waiver and promote required node-first mode only for `cplus`. Treat the generated 20,186-file fixture as evidence for operational scale, memory, ordering, and conservation—not as a substitute claim for the unavailable historical corpus's semantics. Keep every other writer blocked in required mode until its inventory promotion gate passes.

## References

- [Graph ingestion write-path hardening plan](../../plans/260807-1202-graph-ingest-write-path-hardening/plan.md) (`plans/260807-1202-graph-ingest-write-path-hardening/plan.md:378-427`)
- Final validation: `plans/260807-1202-graph-ingest-write-path-hardening/reports/final-validation-2026-08-28.md:7-98`
- Required-mode inventory: `plans/260807-1202-graph-ingest-write-path-hardening/reports/required-mode-mutation-inventory.md:1-41`
- Baseline: `470acacbac8dd32f1c0c8367fc8470234350c816`
