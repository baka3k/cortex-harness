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
2. Run containment mode and persist its raw/validated identity ledger. Record
   every structural node and relation by label, stable ID, normalized source
   span, project, and file.
3. Run sparse semantic mode on the identical horizon. Persist worker responses,
   validation/quarantine accounting, evidence merge, coverage, journal effects,
   and provider readback.
4. Assert exact set equality for Tree-sitter-owned files, namespaces, types,
   functions, declarations, includes, structural relations, source ranges, and
   weak callsites. Clang may only add approved semantic evidence,
   configurations, coverage, and derived strict direct-call edges.
5. Compare raw worker observations to validated/staged/persisted rows so every
   loss is explained by a stable rejection reason. A total count alone cannot
   pass or fail the canary.
6. Rerun without changes and require identical fingerprints/counts with no
   duplicate identities or stale edges.
7. Remove or downgrade one formerly faithful context, rerun incrementally, and
   prove its strict edges disappear while Tree-sitter structure and unrelated
   configurations remain unchanged.
8. Inject publication/readback failure and prove rollback retains containment
   plus the last valid generation without reparsing source.
9. Repeat the staging, deterministic rerun, crash-resume, publication, and
   rollback checks on both Neo4j and FalkorDB.

## Promotion gates

- Direct-call reviewed precision `>= 98%` and recall `>= 95%`.
- Priority faithful-context ratio `>= 90%`; all uncovered contexts have stable
  visible reasons.
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
.venv/bin/python -m unittest \
  tests.test_cplus_clang_parser \
  tests.test_cplus_clang_differential \
  tests.test_cplus_parse_recovery \
  tests.test_cplus_semantic_context \
  tests.test_cplus_semantic_worker \
  tests.test_cplus_call_evidence \
  tests.test_cplus_evidence_merge \
  tests.test_cplus_guarded_publication \
  tests.test_cplus_dual_plane_integration \
  tests.test_cplus_graph_runtime \
  tests.test_cplus_pilot_rollout
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
  semantic publication, retains Tree-sitter and the last valid generation, and
  never restores whole-payload Clang fallback.

## Acceptance criteria

- Both provider readbacks prove exact structural invariance and additive-only
  semantic behavior.
- The pilot manifest references a reachable revision and all evidence is
  fingerprinted/replayable.
- Parent Phase 07 and parser-quality Phase 06 reports are updated with the same
  terminal decision and no contradictory completion claim.

## Todo

- [ ] Repair focused-suite environment and immutable-manifest prerequisites.
- [ ] Complete raw/validated/persisted differential canary.
- [ ] Pass clean FalkorDB and Neo4j deterministic/rollback checks.
- [ ] Recalculate all promotion gates on one horizon.
- [ ] Record the terminal decision and update parent reports.
