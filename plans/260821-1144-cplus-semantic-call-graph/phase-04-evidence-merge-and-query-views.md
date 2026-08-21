# Phase 04: Evidence merge, strict view, and conservative impact view

## Context

After Phases 01-03, Tree-sitter and Clang produce intentionally different
evidence. Flattening them into one caller/callee relationship would erase
provider, configuration, coverage, macro, virtual, and indirect-call semantics.
Current MCP and workflow-impact consumers traverse `CALLS` as if it were one
uniform relationship and can return negative conclusions without semantic
coverage.

## Requirements

- Merge observations by stable callsite/evidence identity, not callee name.
- Preserve contradictory but valid build configurations.
- Deduplicate exact repeated observations without erasing provenance.
- Derive strict and conservative views with explicit, versioned semantics.
- Return semantic coverage/freshness in graph, impact, explanation, and workflow
  results.
- Prohibit negative impact/no-caller conclusions across incomplete frontiers.
- Keep provider-neutral graph schema and Neo4j/FalkorDB parity.
- Maintain compatibility through additive/versioned response fields and a
  documented deprecation path, not silent reinterpretation.
- Join Pro*C semantic functions/calls to original SQL, cursor, host-variable,
  directive, and table evidence without changing their existing identities.
- Propagate dynamic SQL, ambiguous host/cursor/function joins, mapping quality,
  and generated-only evidence into migration/data-impact completeness.

## Architecture

Write canonical `CallSite`, configuration/coverage, and evidence records to a
staging graph. Materialized direct `CALLS` may remain as a compatibility/query
optimization only when derived from accepted `direct_resolved` evidence and
linked back to its stable site/evidence IDs.

Strict traversal selects accepted direct observations under a declared
configuration policy. Conservative traversal unions approved possible classes
without changing their labels. Each traversal accumulates coverage over the
visited frontier and returns `complete`, `partial`, or `unknown` with reasons.

The Pro*C view composes, but never flattens, two evidence paths:

```text
caller -> mapped semantic Function -> original SqlStatement -> DatabaseTable
                                  `-> SqlHostVariable -> C declaration evidence
```

The existing `DECLARES_*`, `BINDS_PARAMETER`, `REFERENCES_*`, `READS_FROM`,
`WRITES_TO`, and `REFERENCES_TABLE` relations retain their meanings. New joins
to semantic functions/variables are separately typed and source-map qualified.
Dynamic SQL or an unresolved join makes the affected data-impact frontier
partial even when the enclosing direct C call is semantically resolved.

## Related files

- call-evidence contract from Phase 01
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/graph/schema/`
- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/cplus/services/graph_service.py`
- `code-tiny/mcp/cplus/services/impact_service.py`
- `code-tiny/tools/common/graph_expander.py`
- `code-tiny/tools/common/workflow_impact_scorer.py`
- MCP metadata/result packagers and focused graph/view tests
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/tool_metadata.py`
- [Pro*C component map](pro-c-component-map.md)

## Implementation steps

1. Define provider-neutral graph labels, relationship types, indexes, identity
   keys, optional endpoint policy, and schema fingerprint in the canonical
   manifest.
2. Implement deterministic merge for Tree-sitter candidates, Clang observations,
   configurations, coverage records, and repeated header observations.
3. Retain provenance and source spans on every relationship or linked evidence
   record; no property-less compatibility edge may become the authority.
4. Add strict and conservative query profiles to call graph, neighborhood,
   explanation, impact, and workflow tools.
5. Return coverage, unresolved reasons, served generation/revision, semantic
   policy, and evidence counts with every relevant result.
6. Implement fail-closed negative-result semantics: incomplete traversal is a
   typed incomplete outcome with suggested next semantic scope.
7. Update impact scoring so possible/unknown/indirect classes affect confidence
   and recommendations without being counted as confirmed direct calls.
8. Add project/configuration isolation, duplicate observation, conflicting
   configuration, legacy cache, and provider-parity tests.
9. Benchmark strict/conservative traversal cardinality, latency, and storage
   overhead before publication is enabled.
10. Reconcile each original Pro*C SQL region's enclosing lexical function with
    mapped Clang function identity; preserve ambiguity rather than choosing by
    name or line proximity alone.
11. Add schema-owner-approved evidence joins for uniquely resolved host and
    indicator declarations while preserving `BINDS_PARAMETER`; represent
    unresolved/ambiguous/cross-configuration bindings explicitly.
12. Extend C++ MCP graph, impact, explanation, and workflow profiles for
    caller→function→SQL→table, cursor lifecycle, directives, dynamic SQL, and
    coverage by original/generated/map/configuration generation.

## Todo

- [x] Register evidence/coverage schema in the canonical graph manifest.
- [x] Implement deterministic evidence merge and exact deduplication.
- [x] Add strict and conservative query profiles.
- [x] Thread coverage/freshness through MCP and impact responses.
- [x] Enforce fail-closed negative answers.
- [x] Update workflow scoring and explanations by evidence class.
- [x] Pass provider parity, isolation, and query-performance tests.
- [x] Join mapped semantic functions with original Pro*C SQL regions.
- [x] Add evidence-qualified host/indicator declaration joins.
- [x] Add Pro*C call-plus-data impact profiles and incomplete-result tests.

## Risks

- Compatibility materialization may drift from the evidence records.
- Configuration-qualified observations can inflate storage and result sets.
- Consumers may ignore new coverage fields while relying on old empty-result
  semantics.

Mitigate with one derivation path, generation fingerprints, bounded configuration
policies, typed incomplete outcomes, contract tests, and an explicit capability
version bump.

## Success criteria

- Every strict edge traces to accepted semantic evidence and configuration.
- Conservative results preserve rather than flatten resolution classes.
- Exact duplicate observations are idempotent; conflicting configurations
  coexist without cross-project or cross-config contamination.
- No MCP/impact/workflow tool returns an authoritative negative result when its
  semantic frontier is incomplete.
- Neo4j and FalkorDB pass the same graph/view contract and query budgets.
- All five Pro*C labels and nine relations remain queryable under `cplus` and
  its Pro*C aliases; cross-domain impact preserves source/map/evidence class.
- Dynamic SQL or ambiguous function/host/cursor mapping cannot produce an
  authoritative `no data impact` result.
