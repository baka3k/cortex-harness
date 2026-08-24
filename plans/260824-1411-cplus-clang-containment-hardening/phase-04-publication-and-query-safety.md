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
3. Put `project_id` and immutable `generation_id` on every graph/vector/
   coverage row and in every identity, constraint, `MATCH`, `MERGE`, deletion,
   endpoint join, and readback. Remove global `LIMIT 1`/ID-only matches; test
   identical symbols in two projects and two generations.
4. Route the combined set through the admitted owner into an inactive staged
   graph/vector generation. Journal deterministic stale deletion and replacement
   operations; no worker or analyzer writes around the owner.
5. Under the owner's lock/transaction boundary, read back the exact staged
   target and compare canonical row sets/full content digests, dangling
   observations, manifest coverage, and stale-edge absence. Bind validation to
   provider/physical target/project/generation/revision/policy/content, recheck
   immediately before an atomic pointer flip, and keep the old active generation
   immutable. Equal counts are insufficient; a provider without generation
   isolation cannot be promoted.
6. On source/config deletion, fidelity loss, rejection, or worker failure,
   stage and atomically publish a current containment generation: Tree-sitter
   structure + weak evidence + exact noncoverage, with no stale strict edges.
   Keep the prior semantic snapshot only as immutable history under its former
   generation/revision/context identity. If containment publication fails,
   there is no current answer; never relabel the old snapshot as current.
7. Define query scope before traversal from `SemanticScopeManifest`. The default
   authoritative caller/impact domain is the entire selected project and exact
   configuration policy; a smaller shard is allowed only when predeclared and
   proven closed. Completeness requires actual keys = expected keys, exactly one
   current complete row per key, and matching generation/revision/policy.
8. Change `_semantic_coverage_block` to left-join expected manifest keys to
   actual rows. Missing, duplicate, stale, mismatched, capped, pending, or
   partial keys make scope incomplete; runtime visited nodes cannot shrink it.
9. Store strong observations per configuration and require an explicit query
   policy: exact configuration, union, or intersection. Never materialize or
   cache a configuration-neutral `CALLS` edge from one favorable variant.
10. Separate `result_state` from `coverage_state`. A non-empty traversal under
    incomplete coverage is `partial`; an empty traversal yields `no_callers`,
    `unaffected`, or `no_impact` only for exact complete scope, otherwise
    `incomplete` with stable reasons and a suggested semantic scope.
11. Thread both states through MCP/HTTP subgraph, path, trace, impact, Pro*C
    call/data impact, and workflow scoring. Query-cache keys include provider,
    resolved physical target, project, generation, revision, schema, profile,
    semantic policy, configuration policy/set, and scope fingerprint; pointer
    flips invalidate atomically, and cached negatives retain coverage digest.

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
| Positive path, one expected key incomplete | Result returned as `partial`, never `complete` |
| Same symbol in another project/generation | Never matches, deletes, or contaminates result |

## Tests

- Add a forged `direct_resolved` row with synthetic/inherited context and prove
  rejection at validator, merge, writer, and publication boundaries.
- Add frontier tests where aggregate project coverage is complete but one
  visited TU/config is absent; negative conclusions must remain blocked.
- Verify graph cache isolation across project, revision, policy, profile, and
  configuration, provider target, generation, schema, and scope.
- Rehearse context downgrade into a new containment generation and prove the
  old semantic snapshot is historical only.
- Inject wrong-target/equal-count readback, forged rows, pointer races, and
  concurrent publication; validation must fail before activation.
- Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cplus_call_evidence.py \
  tests/test_cplus_evidence_merge.py \
  tests/test_cplus_guarded_publication.py \
  tests/test_cplus_pilot_rollout.py
```

## Acceptance criteria

- Weak Tree-sitter evidence and non-faithful Clang observations cannot become
  strict `CALLS` through any validation or writer path.
- Scope-manifest coverage is exact and generation-bound end to end, including
  cache hits and HTTP consumers; incomplete positives are visibly partial.
- Every incomplete empty traversal has `outcome=incomplete`, stable reasons,
  and no unsafe negative wording.
- Context loss publishes a current containment generation without deleting
  structure, mixing generations, or relabeling historical semantics.

## Todo

- [ ] Compose context fidelity into every strong-edge boundary.
- [ ] Wire additive evidence and coverage publication into runtime.
- [ ] Scope coverage and caches to the exact query frontier.
- [ ] Enforce fail-closed negatives across all consumers.
- [ ] Prove downgrade cleanup and atomic generation behavior.
