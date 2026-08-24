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
3. Keep each versioned `logical_id` stable across generations, but use the
   physical address `(resolved_target, project_id, generation_id, logical_id)`;
   store the latter three fields on every graph/vector/coverage row, constraint,
   `MATCH`, `MERGE`, deletion, endpoint join, and readback. Remove global
   `LIMIT 1`/ID-only matches; test identical symbols in two projects and two
   generations.
4. Route the combined set through the admitted owner into an inactive staged
   graph/vector generation. Journal deterministic stale deletion and replacement
   operations; no worker or analyzer writes around the owner. The first run
   consumes Phase 1's incompatibility marker and must rebuild a clean
   Tree-sitter baseline rather than copy legacy Clang structural rows.
5. Under the owner's lock/transaction boundary, read back the exact staged
   target and compare canonical row sets/full content digests, dangling
   observations, manifest coverage, and stale-edge absence. Bind validation to
   provider/physical target/project/generation/revision/policy/content, recheck
   immediately before an atomic pointer flip, and keep the old active generation
   immutable. Equal counts are insufficient; a provider without generation
   isolation cannot be promoted.
6. When ingestion is accepted—or a fidelity downgrade is detected—the owner
   durably advances a monotonic `scope_epoch` plus `latest_desired_horizon` and
   sets a revocation/pending fence before staging. Default/current queries
   require the served generation/revision/context/epoch to equal that authority
   and no pending/failed downgrade. Stage and atomically
   publish a containment generation—Tree-sitter structure + weak evidence +
   exact noncoverage, no stale strict edges—then clear the fence. If publication
   fails, default queries return stale/incomplete; the prior semantic snapshot
   is accessible only through an explicit historical revision.
7. Define query scope before traversal from `SemanticScopeManifest`. The default
   authoritative caller/impact domain is the entire selected project and exact
   selected configuration domain. In the first release every smaller shard is
   `partial` and cannot license an authoritative negative. Completeness requires
   actual keys = expected keys, exactly one current complete row per key, and
   matching generation/revision/policy.
8. Change `_semantic_coverage_block` to left-join expected manifest keys to
   actual rows. Missing, duplicate, stale, mismatched, capped, pending, or
   partial keys make scope incomplete; runtime visited nodes cannot shrink it.
9. Store strong observations per configuration and require an explicit query
   policy: exact configuration, union, or intersection. Never materialize or
   cache a configuration-neutral `CALLS` edge from one favorable variant.
   Complete empty results are policy-qualified:
   `no_callers_in_exact_configuration`,
   `no_callers_in_any_selected_configuration`, or
   `no_caller_common_to_all_configurations`; intersection must never emit the
   generic claim that no callers exist.
10. Separate `result_state` from `coverage_state`. A non-empty traversal under
    incomplete coverage is `partial`; an empty traversal yields only the
    corresponding policy-qualified negative for exact complete scope, otherwise
    `incomplete` with stable reasons and a suggested semantic scope.
11. Thread both states through MCP/HTTP subgraph, path, trace, impact, Pro*C
    call/data impact, and workflow scoring. Query-cache keys include provider,
    resolved physical target, project, generation, revision, schema, profile,
    semantic policy, configuration policy/set, and scope fingerprint; pointer
    flips invalidate atomically, and cached negatives retain coverage digest.

## Configuration-policy truth table

| Policy | Included strict edge | Authoritative empty result |
| --- | --- | --- |
| `exact(C)` | Accepted observation in C; full C scope complete | `no_callers_in_exact_configuration` |
| `union(S)` | Present in any selected config; retain config provenance; every selected scope complete for a negative | `no_callers_in_any_selected_configuration` |
| `intersection(S)` | Same resolved logical edge present in every selected config; every selected scope complete | `no_caller_common_to_all_configurations` (never generic `no_callers`) |

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
| Empty intersection but callers differ by config | `no_caller_common_to_all_configurations`, never generic `no_callers` |
| Downgrade fence set; containment publish fails | Default/current query is stale/incomplete; explicit historical read only |

## Tests

- Add a forged `direct_resolved` row with synthetic/inherited context and prove
  rejection at validator, merge, writer, and publication boundaries.
- Add scope tests where aggregate project coverage is complete but one expected
  TU/config is absent; negative conclusions must remain blocked. Shards remain
  partial, and exact/union/intersection negatives use distinct assertions.
- Verify graph cache isolation across project, revision, policy, profile, and
  configuration, provider target, generation, schema, and scope.
- Rehearse context downgrade into a new containment generation and prove the
  old semantic snapshot is historical only; inject containment failure after
  the durable fence and prove it cannot answer a default/current query.
- Crash before staging and after scope-manifest persistence; the monotonic
  epoch/fence must survive and prevent the prior generation from appearing
  current until reconciliation succeeds.
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
- Context loss either publishes a current containment generation or leaves a
  durable revocation fence that blocks old semantics from default/current
  queries; no structure deletion, generation mixing, or historical relabeling.

## Todo

- [ ] Compose context fidelity into every strong-edge boundary.
- [ ] Wire additive evidence and coverage publication into runtime.
- [ ] Scope coverage and caches to the exact query frontier.
- [ ] Enforce fail-closed negatives across all consumers.
- [ ] Prove downgrade cleanup and atomic generation behavior.
