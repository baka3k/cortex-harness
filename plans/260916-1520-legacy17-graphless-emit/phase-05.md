# Phase 05 — Verify + hand-back to phase-04 of `260916-1432`

## Mục tiêu

End-to-end verification:
1. All 24 analyzers (22 from prior plan + csharp/android) emit artifact in graphless mode
2. cortex-sync capability table 24/24 wired
3. Drill 3 legs (from phase-04 of plan `260916-1432`) PASS:
   - Leg 1: `--full-scan` → vectors_upserted > 0
   - Leg 2: incremental → short-circuit skip child
   - Leg 3: `CORTEX_LEGACY_VECTOR_EMIT=python` → vector_status="disabled-no-emitter"
4. Cross-update both plans
5. Final report

## Scope

### Patch 1 — Final verification suite

Test 24 analyzers × 3 cases = 72 tests (cumulative from phases 01-04):
- All PASS
- No regression in primary pass

### Patch 2 — End-to-end drill trên procsample

Live run (default flag ON, phase-04 of `260916-1432` resumes):

**Leg 1:** Default ON, `--full-scan`
```
$ /Users/.../dev.sh sync code --project-dir /Users/.../procsample --full-scan 2>&1 \
  | grep -E "vectors_upserted|embedding pass|pass complete"
$ curl http://localhost:6333/collections | jq '.result.collections[] | {name, points_count}'
```

Expect:
- `[embedding] pass complete: parsers=N vectors_upserted=M backend=orchestrator` (M > 0)
- Qdrant có ≥3 collections (cplus_bcb95d2ac8__*, java_*_..., etc.)

**Leg 2:** Incremental (no flags) → short-circuit skip child
```
$ /Users/.../dev.sh sync code --project-dir /Users/.../procsample 2>&1 \
  | grep -E "skipping child launch|embedding"
```

Expect:
- `[embedding] cplus/java/sql: skipping child launch (primary-pass artifact fresh)`
- vectors_upserted = same as leg 1 (no re-embed duplicate)

**Leg 3:** Opt-out `CORTEX_LEGACY_VECTOR_EMIT=python`
```
$ CORTEX_LEGACY_VECTOR_EMIT=python /Users/.../dev.sh sync code --project-dir /Users/.../procsample 2>&1 \
  | grep -E "vectors_upserted|vector_status"
```

Expect:
- vectors_upserted = 0
- vector_status = "disabled-no-emitter"

### Patch 3 — Cross-update `260916-1432-legacy-17-vector-emit`

Trong `plans/260916-1432-legacy-17-vector-emit/plan.md`:
- Update status: phase-04 no longer deferred; ready to execute
- Update blocks: add `260916-1520-legacy17-graphless-emit` as completed prerequisite

Trong `plans/260916-1432-legacy-17-vector-emit/phase-04.md`:
- Re-activate (no longer DEFERRED section header)
- Restore original drill 3 legs spec
- Reference this plan as the unblocker

### Patch 4 — Cross-update `260915-2230-python-legacy-cleanup`

Trong `plans/260915-2230-python-legacy-cleanup/plan.md`:
- Update blockedBy: add `260916-1520-legacy17-graphless-emit` (completed)
- Update status: ready to delete Python analyzer scripts after phase-04 of
  `260916-1432` PASS

### Patch 5 — Final report + memory update

Trong `reports/phase05-final.md`:
- Drill 3 legs results (with metrics)
- Final capability table size
- Cross-plan update log
- Lessons learned (architectural pattern hoisting)

Trong `MEMORY.md` (root project):
- Add memory: "Legacy 17 analyzers emit EmbeddingInputArtifact only when graph
  store = Some (writer block in main). Embedding pass sets CORTEX_DISABLE_GRAPH=1,
  so always hoist emission OUTSIDE writer block — see plan 260916-1520."

## Deliverables

- Code changes: none (verification only) — unless drill reveals gap
- Tests: 72 cumulative PASS
- Drill logs: `reports/phase05-drill-leg{1,2,3}.log`
- Cross-plan updates: 2 plan.md files touched
- Final report: `reports/phase05-final.md`
- MEMORY.md update

## Gate

- [ ] All 72 unit tests PASS
- [ ] Drill leg 1 PASS (vectors_upserted > 0 + Qdrant collections)
- [ ] Drill leg 2 PASS (skip child message + same vector_count)
- [ ] Drill leg 3 PASS (vector_status="disabled-no-emitter" + vectors=0)
- [ ] Cross-plan updates complete (1 commit touch 2 plans)
- [ ] Final report written
- [ ] User sign-off (waiver acceptable if drill legs PASS but dogfood skipped)

## Rollback

- Per-analyzer revert possible (atomic commits)
- Capability table revert possible
- Phase-04 of `260916-1432` re-defer if drill fails
- Worst case: stay at status quo pre this plan (legacy 17 = `disabled-no-emitter`)

## Sign-off

- User explicit confirmation: drill 3 legs reviewed
- User explicit confirmation: cross-plan updates acceptable
- Hand-back to `260916-1432` phase-04 acknowledged (next plan resume)