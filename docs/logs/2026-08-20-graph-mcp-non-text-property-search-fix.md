# graph_mcp "Type mismatch: expected String or Null but was Boolean" fix — 2026-08-20

## Context
`search_functions` with `parser_type` failed on graphs ingested with the new schema
(`askilldev`, `bakatrans`, `hyperpack`): Kuzu-backed FalkorDB raises
`Type mismatch: expected String or Null but was Boolean` when evaluating
`toLower(coalesce(n.is_public_api, ''))` over a boolean column. Old graphs without
boolean values ran fine, so the bug only surfaced during multi-DB fan-out.
Plan: `./plans/260820-graph-mcp-search-property-type-fix/plan.md`

## Change
- `code-tiny/mcp/framework_registry.py`: added `NON_TEXT_SEARCH_PROPERTIES`
  (`is_public_api`, `parse_depth`, `declared`, `start_line`, `end_line`, `position`, ...)
  plus `text_search_properties()` and `backend_text_property_union()` filters, exported
  in `__all__`.
- `code-tiny/mcp/cplus/cplus_mcp.py:2756` (`tool_search_functions`): both the profile
  branch and the fan-out `backend_property_union` branch now use the text-safe variants.
- `code-tiny/mcp/unified_mcp.py:1535` (explore path): passes
  `text_search_properties()` instead of raw `capability.searchable_properties` into
  `intelligent_retrieval`'s `toLower(coalesce(...))` predicate.
- Tests: `code-tiny/tests/mcp/test_framework_registry_text_properties.py` — per-profile
  exclusion, global/backend unions, unknown-parser parity.

## Impact
All graph_mcp text-search callers (search_functions, explore_graph) on
schema-migrated graphs. Risk: low — text matching over boolean/int properties was
meaningless anyway; no other `toLower` predicate sites consume dynamic property lists.

## Decision
Filter at predicate-build time via a registry-level allowlist complement rather than
casting in Cypher (`toString()`), because the non-text properties carry no search value
and per-property casts would bloat the fan-out predicate. Reviewer score 8.5/10,
no critical issues; noted cosmetic gap: capability metadata
(`unified_mcp.py:375`) still advertises the raw property list.

## References
- plan: ./plans/260820-graph-mcp-search-property-type-fix/plan.md (uncommitted — contains
  hook-blocked project names)
- commit: 13cc4c5
