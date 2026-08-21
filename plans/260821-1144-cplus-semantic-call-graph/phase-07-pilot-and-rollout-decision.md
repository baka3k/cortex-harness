# Phase 07: Stratified pilot, scale gates, and rollout decision

## Context

The brainstorm decision was conditional because compile-context coverage,
direct-call accuracy, Pro*C mapping, consumer behavior, and million-LOC resource
cost are not yet measured. Sparse semantic analysis is a rollout mechanism, not
an accepted permanent architecture. This phase earns or rejects comprehensive
eligible-TU publication with a reproducible pilot and an explicit decision.

## Requirements

- Run unit, integration, adversarial, provider, incremental, publication,
  rollback, and consumer-contract suites before a real canary.
- Use a stratified real workload covering C, C++, headers, macro/template-heavy
  code, multiple configurations, generated code, and Pro*C.
- Record compile-context coverage and failure reasons, not just parsed files.
- Compare current contained Tree-sitter evidence, sparse selection, and all
  eligible-TU semantic analysis over the same revision and query scenarios.
- Measure accuracy, impact-answer correctness, latency, RSS, CPU, queue/cache,
  header fan-out, storage, failure isolation, and Pro*C mapping.
- Keep semantic publication shadow-only/canary until every hard gate passes.
- Record one terminal decision and rollback evidence.
- Report Pro*C cohorts separately from C/C++ aggregates: SQL fidelity, mask
  alignment, generated-artifact availability, mapping quality, generated
  filtering, function/host/cursor joins, dynamic SQL completeness, graph/vector
  parity, invalidation, resource use, and failure isolation.

## Architecture

Produce a versioned plan-scoped report bundle containing corpus manifest,
revision, toolchain/configuration fingerprints, semantic/parse policy, expected
facts, compile-context census, coverage by module/TU/configuration, edge-class
counts, direct-call accuracy, impact query outcomes, source-map results,
cold/warm/incremental resource metrics, security/fault results, graph/provider
fingerprints, and promotion decision.

The comparison uses one horizon:

- containment: broad weak evidence, no semantic direct-call claim;
- sparse mode: selected/changed/high-value semantic TUs with explicit gaps;
- comprehensive mode: every eligible TU/configuration within approved bounds.

No mode receives credit for deferred coverage or unmeasured future cost.

The Pro*C pilot manifest follows the complete cohort list in the
[Pro*C component map](pro-c-component-map.md). It includes `.pc` and `.pcc`, C
and C++ modes, encodings, SQL/directive/cursor/host/dynamic forms, application
calls around SQL regions, mapped generated output, runtime wrappers, all map
qualities, configuration variants, and source/generated/map/context changes.

## Related files

- all tests and fixtures introduced in Phases 01-06
- `tests/benchmark_cplus_parse_quality.py`
- new `tests/benchmark_cplus_semantic_calls.py` or equivalent
- representative impact-query fixtures
- run-scoped reports under this plan's `reports/`
- operator and developer documentation for coverage, strict/conservative views,
  semantic worker readiness, rollback, and diagnosis
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Freeze the pilot manifest, reviewed expected direct calls/classes, priority
   TUs/modules, representative impact queries, supported platforms, and
   resource budgets before running candidates.
2. Pass functional/adversarial suites: unsafe flags, external paths, malformed
   compile data, runtime mismatch, deep/large source, timeout, crash/OOM,
   configuration collision, cache drift, stale mapping, graph ambiguity, and
   cancellation/publication boundaries.
3. Run the compile-context census and report faithful, inherited, synthetic,
   missing, rejected, and failed cohorts.
4. Benchmark contained Tree-sitter, sparse semantic, and comprehensive eligible
   semantic modes in cold, warm, and changed-TU conditions.
5. Calculate reviewed direct-call precision/recall separately from virtual,
   indirect, dependent, and unresolved cohorts.
6. Replay impact/migration workflows and verify strict/conservative semantics,
   coverage interpretation, and prohibition of unsafe negative claims.
7. Verify Pro*C source-map accuracy and zero SQL/data-flow regression.
8. Run Neo4j/FalkorDB staging canaries with integrity, deterministic rerun,
   crash/resume, publication, and rollback checks.
9. Apply the decision rules and publish the report plus exact configuration and
   safe action.
10. Produce a Pro*C-specific census and result table for discovery/routing,
    decode/mask alignment, all five SQL labels and nine relations, compiler and
    redacted precompiler contexts, artifact/map coverage, semantic accuracy,
    generated filtering, cross-domain impact, cache/incremental behavior,
    graph/vector parity, security, resources, publication, and rollback.
11. Replay migration questions that combine callers, transaction/SQL
    statements, cursors, host variables, and affected tables; independently
    review false negatives and false certainty from dynamic SQL or missing maps.

## Decision rules

### Promote comprehensive eligible-TU semantic publication

Promote only when all plan acceptance gates pass, including:

- zero weak-to-`CALLS` promotion;
- direct-call precision >=98% and recall >=95% on well-configured reviewed cases;
- faithful contexts for at least 90% of agreed priority canary TUs;
- every non-covered TU has a stable visible reason;
- reviewed Pro*C source maps pass 100% with zero SQL regression;
- mask alignment passes 100% for accepted Pro*C files, all five labels/nine
  relationships pass golden and provider-parity checks, and zero generated
  wrapper/runtime/unmapped call is published as original application `CALLS`;
- every incomplete Pro*C artifact, map, dynamic SQL, host/cursor/function join,
  or context has a stable visible reason and blocks unsafe negative impact;
- worker, scale, provider, consumer, incremental, publication, and rollback
  budgets pass without waived critical findings.

### Remain in containment

Remain in containment when Clang packaging/readiness, faithful context coverage,
semantic accuracy, source mapping, security, resource, publication, or consumer
safety fails a hard gate. Preserve the evidence report and exact remediation
needed before reconvening.

### Continue sparse canary without architectural promotion

Sparse mode may remain an explicitly experimental/canary capability when it is
safe and useful but comprehensive gates remain unresolved. It must not support
repository-complete or negative impact claims and must retain visible coverage.

## Todo

- [x] Freeze pilot manifest, budgets, expected facts, and query scenarios.
- [x] Pass functional, adversarial, provider-contract, and failure-boundary suites.
- [x] Publish compile-context and semantic-coverage census.
- [x] Benchmark containment, sparse, and comprehensive modes.
- [x] Calculate reviewed accuracy and impact-answer correctness.
- [ ] Complete the full Pro*C cohort/map gate. Developer contract tests pass,
  but `.pcc`, C++ mode, CP932, directive/cursor/dynamic-SQL, eligible mapped
  output, wrapper, all-map-quality, variant, and generated/map change cohorts
  remain visibly unavailable in the manifest census.
- [ ] Complete live Neo4j/FalkorDB staging publication and rollback canaries.
  Local simulated-owner publication, deterministic rerun, crash/resume, and
  rollback contracts pass; no live provider configuration was supplied.
- [x] Record the containment decision with evidence.
- [x] Update operator/developer documentation; defaults remain unchanged.
- [x] Publish the separate Pro*C component/gate scorecard.
- [x] Review the available developer C-call and SQL/data-impact scenarios;
  full Pro*C cohort replay remains part of the open gate above.

## Risks

- A synthetic corpus can overstate real compile coverage and semantic accuracy.
- Aggregate accuracy can hide critical macro/template/Pro*C cohorts.
- High edge count can be mistaken for high recall.
- Sparse canary success can be misrepresented as repository completeness.
- A successful clean run does not prove incremental invalidation or rollback.

Mitigate with a stratified real corpus, cohort metrics, reviewed workflow
answers, explicit coverage, cold/warm/changed runs, fault injection, and no gate
waiver for critical correctness or safety concerns.

## Success criteria

- The report is reproducible from an immutable manifest and contains every
  accuracy, coverage, mapping, resource, security, provider, incremental,
  publication, and rollback result required by the plan.
- One decision is explicit: promote comprehensive eligible-TU publication,
  remain in containment, or revise/reconvene.
- Defaults change only after promotion; rollback remains tested and documented.
- No unresolved critical objection is averaged away by aggregate scores.
- No Pro*C failure is hidden inside aggregate C/C++ success; promotion requires
  every Pro*C hard gate or an explicit containment decision for that lane.
