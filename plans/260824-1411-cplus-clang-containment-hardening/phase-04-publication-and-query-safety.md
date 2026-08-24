# Phase 04: Publication and coverage-aware query safety

## Goal

Publish semantic evidence additively through the existing guarded generation
path, and make every caller/impact consumer prove completeness for the exact
frontier it serves before returning an authoritative negative.

## Current evidence

- `is_strong_call_evidence` requires a provider and non-empty fingerprint but
  does not prove faithful context.
- `strong_edge_publication_decision` composes parse/map/bundle gates without a
  context-eligibility check.
- Semantic merge, guarded publication, and writer methods exist, but repository
  search finds no complete normal analyzer/sync orchestration.
- `_semantic_coverage_block` aggregates project-wide coverage rather than the
  requested revision/configuration/TU frontier. An unrelated complete row can
  therefore overstate completeness.
- The HTTP graph service does not surface coverage; impact falls back to
  `unknown`, which is safe but makes the runtime contract incomplete.

## Files and symbols

- `code-tiny/tools/common/call_evidence.py`
  - `is_strong_call_evidence`, `frontier_coverage`, `traversal_outcome`
- `code-tiny/tools/common/payload_validation.py`
  - `validate_cplus_payload`
- `code-tiny/tools/cplus/evidence_merge.py`
  - `merge_call_evidence`, `MergedCallSite.to_writer_rows`
- `code-tiny/tools/cplus/guarded_publication.py`
  - `strong_edge_publication_decision`, staged replacement/deletion/publish
- `code-tiny/tools/cplus/cplus_analyzer.py`
  - actual evidence/coverage writer and publication orchestration
- `code-tiny/tools/graph/writer/language_writer.py`
  - evidence site/observation/coverage methods and `write_all`
- `code-tiny/mcp/cplus/cplus_mcp.py`
  - `_semantic_coverage_block`, `_outcome_payload`, graph/path/impact wrappers
- `code-tiny/mcp/cplus/services/graph_service.py`
  - `GraphQueryService.query_subgraph` and cache identity
- `code-tiny/mcp/cplus/services/impact_service.py`
  - `ImpactAnalyzer.analyze`
- `code-tiny/tools/common/workflow_impact_scorer.py`
- `tests/test_cplus_call_evidence.py`
- `tests/test_cplus_evidence_merge.py`
- `tests/test_cplus_guarded_publication.py`

## Implementation steps

1. Add context eligibility and exact provenance to the common strong-evidence
   predicate, payload validation, merge admission, and publication gate. A
   non-empty fingerprint alone is never sufficient.
2. In the normal analyzer path, write Tree-sitter callsites as weak evidence,
   then append eligible Clang observations/configurations/coverage. Derive
   strict `CALLS` only from accepted `direct_resolved` observations.
3. Route the combined staged set through existing writer/journal/generation
   contracts. Do not write semantic rows directly around those owners.
4. On source deletion, context loss, downgrade, rejection, configuration
   removal, or worker failure, schedule stale strong-edge deletion while
   preserving Tree-sitter structure, weak observations, and the last valid
   generation until validation succeeds.
5. Define the query coverage key as project + served revision + semantic policy
   + configuration set + requested/visited TU frontier. Missing, stale,
   duplicate, or mismatched records make the frontier incomplete.
6. Change `_semantic_coverage_block` to fetch only the exact frontier. It must
   not allow one unrelated `complete` row to cover other files or configs.
7. Thread frontier coverage through direct MCP and HTTP service responses.
   Include project/revision/profile/policy in graph-service cache keys so a
   cached result cannot cross coverage or query-profile boundaries.
8. Keep the negative rule centralized: an empty result yields `no_callers`,
   `unaffected`, or `no_impact` only for exact complete coverage. Otherwise it
   yields `incomplete` with reasons and a suggested next semantic scope.
9. Apply the same contract to subgraph, path, trace, impact, Pro*C call/data
   impact, and workflow-scoring consumers.
10. Validate expected counts, dangling observations, stale-edge absence, and
    exact coverage readback before atomic publication.

## Adversarial query matrix

| Situation | Required outcome |
| --- | --- |
| Empty strict graph, exact frontier complete | Authoritative negative allowed |
| Empty strict graph, one TU missing/partial | `incomplete`, never negative |
| Unrelated TU has complete row | Does not improve requested frontier |
| Revision/policy/config mismatch | `incomplete` with mismatch reason |
| Context downgraded after prior success | Old strict edge removed; weak baseline retained |
| Graph proxy omits coverage | `unknown`/`incomplete`, never negative |
| Conservative view contains weak edge | Evidence labeled weak; no semantic relabeling |

## Tests

- Add a forged `direct_resolved` row with synthetic/inherited context and prove
  rejection at validator, merge, writer, and publication boundaries.
- Add frontier tests where aggregate project coverage is complete but one
  visited TU/config is absent; negative conclusions must remain blocked.
- Verify graph cache isolation across project, revision, policy, profile, and
  configuration.
- Rehearse context downgrade/stale-edge cleanup and last-generation retention.
- Run:

```bash
.venv/bin/python -m unittest \
  tests.test_cplus_call_evidence \
  tests.test_cplus_evidence_merge \
  tests.test_cplus_guarded_publication \
  tests.test_cplus_pilot_rollout
```

## Acceptance criteria

- Weak Tree-sitter evidence and non-faithful Clang observations cannot become
  strict `CALLS` through any validation or writer path.
- Coverage is exact-frontier scoped end to end, including cache hits and HTTP
  service consumers.
- Every incomplete empty traversal has `outcome=incomplete`, stable reasons,
  and no unsafe negative wording.
- Context loss removes stale strong evidence without deleting structure or
  corrupting the last valid generation.

## Todo

- [ ] Compose context fidelity into every strong-edge boundary.
- [ ] Wire additive evidence and coverage publication into runtime.
- [ ] Scope coverage and caches to the exact query frontier.
- [ ] Enforce fail-closed negatives across all consumers.
- [ ] Prove downgrade cleanup and atomic generation behavior.
