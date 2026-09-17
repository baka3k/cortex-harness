# Phase 05: Provider canary, rollback, and promotion decision

## Goal

Prove raw-to-persisted dual-plane behavior on clean isolated FalkorDB and
Neo4j generations, repair the current test/pilot prerequisites, and record one
terminal promote/contain/reconvene decision without changing defaults early.

## Preconditions

- The focused suite must be runnable in the declared environment. The research
  baseline reports four import failures because `neo4j` is absent and two pilot
  failures because manifest revision `0eede02...` is unreachable; classify and
  repair these prerequisites before treating the suite as a product signal.
- Use one reachable immutable revision, one file manifest, one compile-context
  set, one semantic policy, and cold caches for the baseline comparison.
- Use fresh, explicitly named test graph/database targets. Do not run this
  differential against an existing user or production graph.
- Complete a FalkorDB/Neo4j capability matrix for generation isolation, target
  identity, constraints, locks/transactions, atomic pointer semantics,
  deletion, readback consistency, and graph/vector pairing. An unsupported
  hard invariant blocks that provider rather than receiving an adapter waiver.
- Obtain explicit test-target authorization and deny production endpoints.
  Credentials must come from protected files/handles or the harness secret
  provider, never argv, rendered commands, reports, or logs. Require TLS where
  remote, host allowlisting, least privilege, a per-run nonce/ownership marker,
  revalidation before destructive fault injection, and deterministic cleanup.

## Files and artifacts

- `tests/test_cplus_dual_plane_integration.py`
- `tests/test_cplus_graph_runtime.py`
- `tests/test_explore_graph_falkor_compat.py`
- `tests/test_falkordb_driver.py`
- `tests/test_falkordb_driver_local.py`
- `tests/test_cplus_pilot_rollout.py`
- `tests/benchmark_cplus_semantic_calls.py`
- `code-tiny/tools/cplus/pilot_rollout.py`
- `plans/260821-1144-cplus-semantic-call-graph/pilot-manifest.json`
- `plans/260821-1144-cplus-semantic-call-graph/pilot-gate-evidence.json`
- `plans/260821-1144-cplus-semantic-call-graph/phase-07-report.md`
- `plans/260807-1329-parser-quality-recovery/phase-06-validation-and-rollout.md`

## Differential protocol

1. Freeze the reviewed corpus: multiple functions, prototype/external target,
   pure virtual, overloads, dependent template, function pointer, macro/static
   linkage, header context, missing/generated header, context loss, and existing
   Pro*C preservation fixtures.
2. Run containment mode and persist its raw/validated canonical projection:
   label, signature-v2/stable ID, normalized source span, relation type,
   logical endpoints, and stable properties. Exclude generation values,
   ordering, timestamps, elapsed time, and run-local IDs; validate physical
   `(project, generation, logical_id)` isolation in a separate provider ledger.
3. Run sparse semantic mode on the identical horizon. Persist worker responses,
   validation/quarantine accounting, evidence merge, coverage, journal effects,
   and provider readback.
4. Assert exact set equality for Tree-sitter-owned files, namespaces, types,
   functions, declarations, includes, structural relations, source ranges, and
   weak callsites over the logical projection. Clang may only add approved
   semantic evidence, configurations, coverage, and derived strict direct-call
   edges; differing physical generation keys are expected and isolated.
5. Compare raw worker observations to validated/staged/persisted rows so every
   loss is explained by a stable rejection reason. A total count alone cannot
   pass or fail the canary.
6. Rerun without changes and require identical fingerprints/counts with no
   duplicate identities or stale edges.
7. Remove or downgrade one formerly faithful context, rerun incrementally, and
   prove its strict edges disappear while Tree-sitter structure and unrelated
   configurations remain unchanged.
8. Inject publication/readback failure and prove the active pointer never
   exposes a mixed generation. On fidelity/context loss, publish a current
   containment generation; retain the prior semantic snapshot only as immutable
   historical data, never as current.
9. Repeat the staging, deterministic rerun, crash-resume, publication, and
   rollback checks on both Neo4j and FalkorDB.

## Crash and reconciliation matrix

Inject process loss at: (1) scope-manifest persistence before enqueue, (2)
worker completion before/after cache rename, (3) journal artifact/enqueue, (4)
provider mutation before ACK, (5) stale delete before replacement, (6) graph
before vector, (7) readback before pointer flip, (8) pointer flip before ledger,
and (9) rollback pointer. Each restart must prove one visible generation,
idempotent replay, no duplicate/mixed revision, fail-closed queries, and
deterministic reconciliation of ambiguous mutations. Also exercise concurrent
cache writers, fsync loss, corrupt entries, temp files, and orphan journals.
For accepted new horizons/downgrades, inject loss before staging, after manifest
persistence, and after failed containment publication; the durable monotonic
scope epoch must prevent the old generation from answering as default/current.

## Promotion gates

- Direct-call reviewed precision `>= 98%` and recall `>= 95%` over at least 500
  independently reviewed direct callsites and at least 50 per overload,
  template, virtual, indirect/function-pointer, macro, and cross-file cohort.
  If a cohort is smaller, review all of it and remain in reconvene until the
  sample floor is supplied; aggregate success cannot hide a cohort failure.
- Priority faithful-context ratio `>= 90%` across at least 30 TU/configuration
  keys per declared build cohort, with no cohort below 80%; every uncovered key
  has a stable visible reason.
- Zero weak or non-faithful observation promoted to `CALLS`.
- Zero unsafe negative answer across exact-frontier replay scenarios.
- Exact Tree-sitter structural invariance across containment and semantic modes.
- Raw -> validated -> staged -> provider readback accounting balances exactly.
- Deterministic rerun, crash resume, context-loss downgrade, atomic publication,
  and rollback pass on both providers.
- Existing Pro*C SQL facts and source mappings survive semantic failure modes.
- Security, resource, queue, cache, fan-out, and required production-scale gates
  from the parent semantic plan pass; they cannot be averaged away.

## Verification commands

```bash
.venv/bin/python -m pytest -q \
  tests/test_cplus_clang_parser.py \
  tests/test_cplus_clang_differential.py \
  tests/test_cplus_parse_recovery.py \
  tests/test_cplus_semantic_context.py \
  tests/test_cplus_semantic_worker.py \
  tests/test_cplus_call_evidence.py \
  tests/test_cplus_evidence_merge.py \
  tests/test_cplus_guarded_publication.py \
  tests/test_cplus_dual_plane_integration.py \
  tests/test_cplus_graph_runtime.py \
  tests/test_cplus_pilot_rollout.py
```

Provider commands and credentials must use the repository's existing test
harness, remain redacted from reports, and target validated isolated names.

## Decision and rollback

- **Promote** only when every hard gate is green on the same immutable horizon.
- **Contain** on weak-to-strong leakage, unsafe negatives, structural drift,
  publication/rollback failure, or a critical security finding.
- **Reconvene** when correctness is safe but useful faithful-context coverage or
  performance evidence is below threshold.
- Until promotion, default mode remains `containment`; rollback disables
  semantic publication and atomically serves a current Tree-sitter containment
  generation. The last semantic generation remains historical only; rollback
  never restores whole-payload Clang fallback.

## Acceptance criteria

- Both provider readbacks prove exact structural invariance and additive-only
  semantic behavior.
- Both providers pass their capability matrix and all nine crash boundaries;
  secrets and production targets are absent from argv/log/report evidence.
- The pilot manifest references a reachable revision and all evidence is
  fingerprinted/replayable.
- Parent Phase 07 and parser-quality Phase 06 reports are updated with the same
  terminal decision and no contradictory completion claim.

## Todo

- [x] Repair focused-suite environment and immutable-manifest prerequisites.
- [ ] Complete raw/validated/persisted differential canary.
- [ ] Pass clean FalkorDB and Neo4j deterministic/rollback checks.
- [ ] Recalculate all promotion gates on one horizon.
- [ ] Record the terminal decision and update parent reports.
