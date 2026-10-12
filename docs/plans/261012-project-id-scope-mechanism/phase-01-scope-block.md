# Phase 01 — Scope block + warnings + strict_scope

## Objective

Every `semantic_graph_search` response explains its own scope: which mode ran, which collections were actually searched, how many hits each contributed, and any drift/fallback warnings. Unknown `project_id` degrades to search-all with a warning (or fails under `strict_scope=true`).

## Changes

1. **Scope computation lives in `qdrant_search_entity_payload`** (`doc-tiny/mcp_graph_rag.py:358-488`) — it already tracks `probed_stores` / `available` / `present` (`:401-429`). Return `(rows, scope_info)` where `scope_info` carries: `mode`, `requested_project_id`, `resolved_project_id`, `registry_hit`, `collections_searched` (requested names — meaning unchanged per contract), `collections_present`, `per_collection_hits`, `warnings[]`.
2. **Mode derives from the `_qdrant_search_targets` branch** (`:338-355` — 1:1 mapping):
   - `project_id` + registered store → `mode="scoped"`, `registry_hit=true`
   - `project_id` + fallback stores → `mode="scoped"`, `registry_hit=false` (until Phase 03/04 refine to `fallback_all`)
   - `collection` explicit → `mode="collection"`
   - no scope → `mode="unscoped"`
3. **`strict_scope: bool = False` param** on `semantic_graph_search` (`:960-982` signature; `_coerce_bool` in-body, `:1004-1016`; guard test `tests/test_unified_contract_doc_paths.py:135-148` already tolerates added params).
   - `strict_scope=true` + project id resolves to nothing → raise `ProjectNotRegisteredError` (maps to error code `project_not_registered`, `cortex_harness/mcp_contract.py:57-68`).
   - default (false) + resolves to nothing → `mode="fallback_all"` + warning `"project '<id>' not found in registry or stores; fell back to all projects"`; search proceeds unscoped.
4. **`scope` and top-level `warnings` inside `data`** (`{ok,data,error}` envelope immutable; warnings-list precedent `code-tiny/mcp/unified_mcp.py:647-680`). `collections_searched` keeps its current meaning — no semantic rename (`docs/mcp-output-contract.md:341`).
5. Found-but-empty: collection present, 0 hits for the project filter → keep `passages: []` + warning explaining exactly that (distinguishes from missing-collection).
6. Explicit `collection=` path: `mode="collection"`, **no fallback** (absent collection still raises `LookupError` as today — this plan does not change it).

## Acceptance criteria

- AC1: unscoped query response contains `scope.mode="unscoped"`, `per_collection_hits` matching real hit counts, no warnings when clean.
- AC2: `project_id="<unknown>"` (default strict_scope=false) → `scope.mode="fallback_all"`, warning present, results from all shards.
- AC3: same call with `strict_scope=true` → `ok:false`, error code `project_not_registered`.
- AC4: registered id → `mode="scoped"`, `registry_hit=true`, `resolved_project_id` canonicalized.
- AC5: explicit `collection` → `mode="collection"`; absent collection still errors (`collection_unavailable`).
- AC6: found-but-empty scoped query → 0 passages + warning text naming the collection.

## Test scope

- New: `tests/test_semantic_graph_scope_block.py` — AC1..AC6 with mocked stores (pattern: `tests/test_unified_contract_doc_paths.py` store fakes).
- Update: `tests/test_unified_contract_doc_paths.py` envelope-identity tests (`:150-193`) — assert `scope` presence does not break existing field assertions.
- Merge-gate set (`docs/mcp-output-contract.md:363-370`) + `doc-tiny/tests/test_mcp_project_id_optional.py`.
- **Size: M**
