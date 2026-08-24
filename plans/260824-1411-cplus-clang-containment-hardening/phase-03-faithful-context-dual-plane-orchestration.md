# Phase 03: Faithful-context dual-plane orchestration

## Goal

Connect the already implemented context registry, semantic cache, bounded
protocol-2 worker, and evidence merge to the real analyzer/sync path. Clang is
useful without a successful whole-project build only where one TU/configuration
has faithful frontend inputs.

## Runtime contract

```text
source inventory
  -> Tree-sitter structural payload (always)
  -> lexical call evidence (always weak)
validated compile context
  -> faithful? -> bounded Clang protocol 2 -> semantic observations
  -> otherwise -> explicit coverage reason, zero strict observations
both evidence streams
  -> deterministic merge, never structural replacement
```

Whole-project link, test, or package failure does not make an otherwise faithful
TU ineligible. Missing/stale flags, target, macros, headers, generated inputs,
or source/dependency fingerprints do.

## Files and symbols

- `code-tiny/tools/cplus/cplus_analyzer.py`
  - `build_call_graph`, `raw_payload_for`, callsite buffer/write path, `parse_args`
- `code-tiny/tools/sync/incremental_sync.py`
  - `_build_analyzer_cmd`, C/C++ policy forwarding and run artifacts
- `cortex_harness/dev.py`
  - sync-code option forwarding/status surface
- `code-tiny/tools/cplus/semantic_context.py`
  - `CoverageState`, `RegisteredContext.eligible`, `ContextRegistry`
  - `SemanticCache`, `BoundedSemanticScheduler`, `build_baseline_report`
- `code-tiny/tools/cplus/parse_recovery.py`
  - `load_compile_database`, `run_semantic_worker`
- `code-tiny/tools/cplus/semantic_worker.py`
  - `validate_semantic_request`, `build_semantic_response`
- `code-tiny/tools/cplus/evidence_merge.py`
  - `merge_call_evidence`, coverage frontier
- `tests/test_cplus_semantic_context.py`
- `tests/test_cplus_semantic_worker.py`
- new `tests/test_cplus_dual_plane_integration.py`

## Implementation steps

1. Add one explicit C/C++ semantic mode surface reused from the pilot contract:
   `containment` (default), `sparse`, or `comprehensive`. Forward it through
   `dev sync code` and incremental sync without adding another scheduler.
2. Load only an existing bounded/sanitized compile database. Do not execute
   repository build commands, bootstrap CMake/Make/Bear, or synthesize missing
   generated inputs in this plan.
3. Register every TU/configuration and normalize external coverage states to
   `faithful`, `inherited`, `synthetic`, `missing`, `rejected`, or `failed` with
   one stable reason. Header-borrowed/inherited contexts remain ineligible for
   strict publication.
4. Select protocol-2 work only for `RegisteredContext.eligible` entries. Sparse
   mode additionally applies the bounded cohort/budget policy; comprehensive
   means all eligible contexts, not all repository files.
5. Extend request/response provenance with context state, project, revision,
   semantic-policy version, configuration identity, source/dependency hashes,
   and worker/backend versions. Reject mismatches at both worker response and
   parent-process acceptance boundaries.
6. Stop deriving semantic completeness from zero Clang diagnostics alone.
   `complete` requires faithful context, non-truncated successful extraction,
   current contained dependencies, and exact provenance. Diagnostics may only
   reduce completeness, never establish context fidelity.
7. Emit one `SemanticCoverage` record for every TU/configuration in the served
   scope, including `not_analyzed`, ineligible, rejected, failed, partial, and
   complete outcomes. Absence of a row is treated as unknown later.
8. Feed Tree-sitter lexical observations and eligible Clang observations to
   `merge_call_evidence`. Preserve contradictory configuration observations
   rather than choosing an implicit winner.
9. Use the existing semantic cache and bounded scheduler identities. A change
   to context, target, source, dependency, configuration, policy, worker, or
   generated map must invalidate the affected semantic entry without
   invalidating Tree-sitter structure.
10. Export a run-scoped coverage/context/queue artifact and surface counts plus
    stable failure reasons in the sync summary.

## Failure-state expectations

| State | Structural output | Semantic output | Strict publication |
| --- | --- | --- | --- |
| `faithful` + complete | Tree-sitter unchanged | Classified Clang observations | Eligible for later gates |
| `faithful` + partial/failed | Tree-sitter unchanged | Weak/partial or none | Forbidden |
| `inherited`/`synthetic` | Tree-sitter unchanged | Optional shadow evidence | Forbidden |
| `missing`/`rejected` | Tree-sitter unchanged | None | Forbidden |
| worker timeout/crash/OOM | Tree-sitter unchanged | Typed failure | Forbidden |

## Tests

- Build-free fixture with faithful existing command succeeds semantically even
  though no link/build step runs.
- Missing include, generated header, target flag, or context produces explicit
  noncoverage and zero strict edges while retaining every Tree-sitter identity.
- A forged non-empty fingerprint with non-faithful state is rejected.
- Context/source/dependency/config/policy mutations invalidate only expected
  semantic cache entries.
- Queue overload, timeout, cancellation, crash, and truncated output cannot
  mutate the structural baseline or claim complete coverage.
- Run:

```bash
.venv/bin/python -m unittest \
  tests.test_cplus_semantic_context \
  tests.test_cplus_semantic_worker \
  tests.test_cplus_dual_plane_integration \
  tests.test_incremental_sync_parse_quality
```

## Acceptance criteria

- Normal analyzer/sync code, not only benchmark/shadow tests, invokes protocol
  2 through the bounded context-aware lane.
- Every served TU/configuration has a deterministic coverage record or is
  explicitly outside the requested scope.
- Only faithful, complete, provenance-matched worker results reach the strong
  evidence candidate set.
- Structural payload counts, IDs, ranges, and relations match containment mode
  exactly for the same frozen horizon.

## Todo

- [ ] Add and forward the semantic mode contract.
- [ ] Wire registry/cache/scheduler/worker into the normal path.
- [ ] Normalize all context and worker failure states.
- [ ] Emit complete per-frontier coverage artifacts.
- [ ] Prove build-free faithful-TU behavior and exact invalidation.
