# C/C++/Pro*C Phase 04: evidence merge, strict/conservative views, fail-closed coverage — 2026-08-21

## Context

Implements Phase 04 of the cplus semantic call-graph plan
(`plans/260821-1144-cplus-semantic-call-graph/phase-04-evidence-merge-and-query-views.md`).
After Phases 01–03, Tree-sitter and Clang produced intentionally different
evidence with no merge layer; MCP/workflow consumers traversed `CALLS` as a
uniform relationship and could return authoritative negatives over
incomplete semantic coverage.

## Change

- New deterministic merge module `code-tiny/tools/cplus/evidence_merge.py`:
  merge by stable callsite/evidence identity (not callee name), exact dedup
  with run provenance, contradictory build configurations coexist, project
  isolation fail-closed, strict CALLS rows derived only from a single
  unambiguous accepted `direct_resolved` observation, plus Pro*C
  function-join (`unique`/`ambiguous`/`unresolved`) and host-declaration
  (`unique`/`ambiguous`/`cross_config`/`unresolved`) reconciliation and
  dynamic-SQL partial coverage.
- Schema manifest v2 (`code-tiny/tools/graph/schema/manifest.py`):
  `CallSite`/`BuildConfiguration`/`SemanticCoverage` labels, relationship
  registry with endpoint policies (`HAS_CALLSITE`, `RESOLVES_TO`,
  `OBSERVED_AS`, `IN_CONFIGURATION`, `MAPS_TO_SOURCE`, `EXECUTES_SQL`,
  `RESOLVES_HOST_DECLARATION`); versioned fingerprint covers relationships.
- Staging writers in `language_writer.py`: site nodes persist before caller
  resolution; unlinked observations counted on the site
  (`dangling_observation_ids`), never silently dropped; Pro*C evidence joins
  never touch the nine existing Pro*C relations or `BINDS_PARAMETER`.
- Query layer (`code-tiny/mcp/cplus/cplus_mcp.py`): `query_profile`
  strict/conservative on `query_subgraph` (strict post-filters edges to
  `resolution_class=direct_resolved` — relationship type alone cannot
  express the contract); every result carries `semantic_coverage` +
  `outcome`; empty traversals over incomplete/unknown frontiers return
  `outcome=incomplete` with `suggested_next_semantic_scope`. New
  `analyze_proc_data_impact` tool (caller→function→SQL→table, host joins,
  dynamic-SQL fail-closed; failed evidence queries downgrade to partial).
- Registry (`framework_registry.py`): cplus `strict`, `conservative`,
  `proc_data_impact` profiles; evidence labels/relations registered.
- `workflow_impact_scorer.py`: negative recommendations gated on coverage;
  weak relationship classes flag conservative evidence and reduce indirect
  confidence (0.7×) without counting as confirmed direct calls.
- `impact_service.py`: seed-excluded emptiness, coverage-threaded typed
  outcome (HTTP-proxy path falls back to `unknown` coverage — safe
  direction, documented).

## Impact

All C++/Pro*C graph consumers. Risk: medium — additive/versioned response
fields; strict deep traversals are documented best-effort (post-filtered
paths may share legacy CALLS hops). Known deferred: param-declaration label
coverage for host joins; conservative confidence penalty applies per-config
not per-path.

## Decision

- Merge identity includes config fingerprint so configurations coexist
  rather than "win"; ambiguous callee across configs blocks strict
  derivation instead of choosing.
- Strict view implemented as post-filter on `resolution_class` because the
  driver traversal API cannot express property predicates; alternative
  (staging-plane traversal) deferred until publication (Phase 06).
- Two review cycles (score 5.5 → 7.5) fixed: strict contract violation,
  silent evidence drops in writer, fail-open impact queries, registry/profile
  divergence.

## References

- plan: ./plans/260821-1144-cplus-semantic-call-graph/phase-04-evidence-merge-and-query-views.md
- commit: 4d779c2
- tests: tests/test_cplus_evidence_merge.py (37 tests)
