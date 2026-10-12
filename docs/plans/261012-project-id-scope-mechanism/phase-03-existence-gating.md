# Phase 03 — Existence gating for convention shards (anti-junk)

## Objective

A naming-convention shard (`{project_id}_doc`) is **only touched after an existence check**. A wrong/typo'd id can no longer materialize a junk FalkorDB graph or Qdrant collection — the `cortext_doc`-style incident becomes impossible from the query path. This phase supplies the "resolves to nothing" facts that Phase 01's fallback/strict behavior consumes.

## Changes

1. **FalkorDB gate**: before `base.for_graph(f"{project_id}_doc")` in `_graph_store_candidates` (`doc-tiny/mcp_graph_rag.py`, unregistered branch), call the driver's `list_graphs()` (`code-tiny/tools/graph/driver/falkordb_driver.py:627`, side-effect-free `GRAPH.LIST`). Absent → return an "absent" marker instead of a driver; the absent fact flows into `scope_info` (Phase 01) as `scope.graph_present=false` + warning contribution.
2. **Qdrant gate**: in `qdrant_search_entity_payload`, convention names already pass through the `available = list_collection_names()` presence filter (`:406-424`) — extend that existing pattern so an absent convention collection is recorded as `scope.collections_present` mismatch (not silently skipped) and contributes to the "resolves to nothing" verdict.
3. **Cache discipline (junk-prevention is void if we create the thing ourselves)**: `_graph_drivers` / `_qdrant_stores` (`:110-111`) are process-lifetime. The gate must run **before** any `for_graph(...)` / store construction for absent shards — no driver object is ever created for a name that GRAPH.LIST does not list. Negative results are *not* cached as success; re-resolution happens next call (listings are read live; only handles are cached — per plan.md red-team note).
4. **Verdict wiring**: scoped id with no registry entry, no present convention Qdrant collection, and no listed convention graph → "resolves to nothing": `strict_scope` → `ProjectNotRegisteredError`; default → `fallback_all` + warning (Phase 01 behavior, now grounded in real existence facts instead of exception-driven detection).
5. Registered-id path unchanged (registry pinning already existence-owns its targets).

## Acceptance criteria

- AC1: query with typo'd id against a FalkorDB fake where `list_graphs()` returns `[]` → no `for_graph()` call, no Cypher executed, no driver cached; response is `fallback_all` (or `project_not_registered` under strict) with warning naming the attempted convention shard.
- AC2: convention graph **is** listed → driver created for the listed name only; entity fetch works (current behavior preserved).
- AC3: convention Qdrant collection absent → `collections_searched` still shows the requested name, `collections_present` shows the mismatch, warning emitted; **no `ensure_collection`-style creation triggered from the read path** (assert store's create methods never called).
- AC4: second identical query in the same process still re-checks existence (no cached success for an absent shard) — simulate shard appearing between calls, next call finds it.
- AC5: registered project pinning path untouched (existing pinned-store tests pass unchanged).

## Test scope

- New: `tests/test_scope_existence_gating.py` — AC1..AC5 (fakes with call logs; assert negative on creation side effects).
- Run: `doc-tiny/tests/test_mcp_project_id_optional.py` (fallback-store tests `:347,:380`), `tests/test_unified_contract_doc_paths.py` read-fallback tests (`:98`), merge-gate set.
- **Size: M**
