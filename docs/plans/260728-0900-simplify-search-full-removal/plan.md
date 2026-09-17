---
title: "Remove search_full — Simplify to project_id-only Scoping"
status: pending
created: 2026-07-28
mode: hi-plan --full
scope: code-tiny MCP tools, doc-tiny MCP tools, shared helpers, tests, metadata
blockedBy: [260728-0000-unified-ingest-query-contract]
relatedPlans:
  - 260728-0000-unified-ingest-query-contract
  - 260723-0908-case-insensitive-project-id
  - 260728-1400-remove-db-param-unify-project-id
reviewed: 2026-07-28
---

> **FOLLOW-UP NOTE (2026-07-28):** Plan `260728-1400-remove-db-param-unify-project-id`
> extends the same single-key contract established here. This plan removed
> `search_full`; the follow-up removes `db`. Together they leave `project_id`
> as the sole scoping key on every MCP tool.

# Remove search_full — Simplify to project_id-only Scoping

## Overview

Every project-scoped MCP tool currently accepts **two** parameters for
project scoping: `project_id` and `search_full`. This is redundant and
overcomplicated. The desired contract is:

- **`project_id` passed** → scope the query to that project's shard.
- **`project_id` absent** → search across all projects (full search).

`search_full` is removed entirely from every function signature, every
helper, every Cypher parameter, every error message, and every piece of
documentation. No backward-compatibility shim, no deprecated no-op —
hard removal.

### Critical research finding

`$search_full` is **not used in any actual Cypher query template**. It
is only set on the params dict by `prepare_project_scope_parameters` and
referenced in docstrings/comments. The real filtering is done by
`$project_id_normalized` which Cypher queries already use conditionally
(included only when `project_id` is set). This makes the removal clean —
no Cypher templates need changing.

## Scope Challenge Decisions

### 1. Cypher layer: remove `$search_full` entirely

**Selected: remove from Cypher.** The `$search_full` parameter is dead
weight — no query reads it. `prepare_project_scope_parameters` stops
setting it. Cypher templates already filter by `$project_id_normalized`
only when that param is present; when `project_id` is absent, the
param is simply not set and queries that reference it conditionally
already skip the filter.

### 2. Error class: remove `ProjectScopeRequiredError` completely

**Selected: full removal.** No `project_id` = full search, always.
Even with an empty registry, the resolution falls through to env
defaults (`FALKORDB_GRAPH` / `NEO4J_DB` / `DEFAULT_GRAPH_DB`) rather
than raising. The error class is deleted from both code-tiny
(`project_registry.py`) and doc-tiny (`project_contract.py`).

### 3. Backward compatibility: hard removal

**Selected: hard remove.** `search_full` is removed from every tool
signature. Clients passing it receive "unknown parameter" — identical
to how `activate_project` was removed. No transition period.

## Verified Current Behavior

### code-tiny

**`project_scope.py`** (3 helpers):
- `prepare_project_scope_parameters`: always sets `search_full = False`
  on the returned dict if not already present. **No Cypher query reads
  this value** — it is dead code.
- `qdrant_project_filter(project_id, search_full=False)`: returns `None`
  when `search_full=True` or when `project_id` is falsy.
- `matches_project_scope(candidate, project_id, search_full=False)`:
  returns `True` when `search_full=True`.

**`project_registry.py`**:
- `ProjectScopeRequiredError`: raised by `_resolve_graph_database` when
  no `project_id` and no `search_full` are given. Message mentions
  `search_full=True` as the escape hatch.
- `resolve_project_targets`, `list_registered_projects`: no `search_full`
  dependency.

**`unified_mcp.py`** (12 affected call sites):
- `_resolve_graph_database(db, project_id, search_full)`: 3-way
  precedence — `db` → `project_id` → `search_full` → error.
- `_run_project_context_tool(..., search_full=False)`: forwards to
  `_resolve_graph_database`.
- 6 project-context tools: `get_project_modules`, `get_public_apis`,
  `get_endpoints`, `get_module_architecture_summary`,
  `get_project_special_files`, `get_framework_context` — all accept
  `search_full: bool = False` and forward it.
- 2 bridge tools: `find_callers_of_endpoint`, `get_api_call_chain` —
  accept `search_full` and pass to `_resolve_graph_database`.
- 2 workflow tools: `analyze_workflow_impact`,
  `find_workflows_containing` — accept `search_full` and pass to
  `_resolve_graph_database`.
- `explore_graph` (line 1879): calls `_resolve_graph_database(db=db)`
  with no project_id or search_full — already works with the simplified
  contract.
- Deprecation notice in `tool_activate_project_removed` mentions
  `search_full=true`.

**Other MCP servers** (comments only):
- `cplus_mcp.py`, `android_mcp.py`, `java_mcp.py`, `fastmcp_server.py`:
  deprecation notices mention `search_full=true`. No `search_full` in
  tool signatures — they call `prepare_project_scope_parameters` via
  graph drivers.

### doc-tiny

**`project_contract.py`**:
- `ProjectScopeRequiredError`: mirrors code-tiny, mentions `search_full`.
- `qdrant_project_filter(project_id, search_full=False)`: same pattern.

**`mcp_graph_rag.py`**:
- `semantic_search(..., search_full=False)`: requires `project_id` or
  `search_full=True` or explicit `collection`.
- `query_graph_rag_langextract(..., search_full=False)`: same pattern.

## Target Contract

### Resolution rule (replaces `_resolve_graph_database` 3-way logic)

```text
db explicit         → use db (escape hatch, unchanged)
project_id present  → resolve via registry → targets.code_graph
project_id absent   → resolve from env defaults
                        (FALKORDB_GRAPH → NEO4J_DB → DEFAULT_GRAPH_DB
                         → "hyper_graph")
```

No error is raised for missing `project_id`. Full search is the
implicit default.

### Qdrant filter rule (replaces `qdrant_project_filter` 2-arg logic)

```text
project_id present  → {"must": [{"key": "project_id_normalized",
                                  "match": {"value": casefold(id)}}]}
project_id absent   → None (no filter — full search)
```

### Parameter preparation (replaces `prepare_project_scope_parameters`)

The function keeps its current behavior of adding `*_normalized`
variants for recognized project-scope keys. The only change: it **stops
setting `search_full`** on the returned dict. No Cypher query reads it.

## Phases

1. [Phase 01 — Core helpers: project_scope.py + project_registry.py](phase-01-core-helpers.md)
2. [Phase 02 — unified_mcp.py: tools + resolution](phase-02-unified-mcp.md)
3. [Phase 03 — doc-tiny: project_contract.py + mcp_graph_rag.py](phase-03-doc-tiny.md)
4. [Phase 04 — Tests + metadata + documentation](phase-04-tests-metadata.md)

## Cross-Plan Dependencies

- **`blockedBy: 260728-0000-unified-ingest-query-contract`** — that plan
  introduced `search_full` and `ProjectScopeRequiredError`. This plan
  supersedes those decisions. The parent plan's Phase 02 (graph_mcp
  query) and Phase 05 (mind_mcp query) are directly amended: their
  `search_full` tool parameters and `ProjectScopeRequiredError` guards
  are removed. The bidirectional update is applied to both plan files.
- **Reuses `260723-0908-case-insensitive-project-id` (completed)**:
  `project_id_normalized` and the casefold comparison key stay as-is.
  Only the `search_full` flag layered on top is removed.

## Scope Boundaries

### Included

- Remove `search_full` parameter from all function signatures in
  code-tiny and doc-tiny.
- Remove `search_full` from Cypher parameter dicts
  (`prepare_project_scope_parameters`).
- Remove `ProjectScopeRequiredError` class and all its raise sites.
- Simplify `_resolve_graph_database` to 2-way logic (db / project_id)
  with env-default fallback for full search.
- Update all deprecation notices, tool descriptions, and metadata.
- Update all tests that assert on `search_full` behavior.

### Excluded

- Changing the `project_id_normalized` comparison contract (stays as-is).
- Changing the ProjectRegistry resolution logic or naming contract.
- Modifying actual Cypher query templates (they don't use `$search_full`).
- Changing the graph driver interface.
- Adding new MCP tools or changing tool names.

## Success Criteria

- No `.py` file in the repository contains the string `search_full`
  (except this plan and archived logs).
- Every project-scoped MCP tool accepts `project_id: str = ""` and
  nothing else for project scoping. Passing `project_id` scopes; omitting
  it searches across all projects.
- `_resolve_graph_database(db, project_id=None)` never raises for missing
  `project_id` — it falls through to env defaults.
- `prepare_project_scope_parameters` does not set `search_full` on the
  returned dict.
- `qdrant_project_filter(project_id)` returns the filter dict when
  `project_id` is truthy, `None` when falsy — single argument.
- `ProjectScopeRequiredError` class is deleted; no import or reference
  remains.
- All existing tests pass (after updating test assertions for the new
  behavior).
- `grep -r search_full code-tiny/ doc-tiny/` returns zero matches.

## Risks

| Risk | Mitigation |
| --- | --- |
| External clients passing `search_full=true` break | By design — hard removal, same as `activate_project`. Document in CHANGELOG. |
| `_resolve_graph_database` with no project_id and no registered projects returns empty results | Falls through to env-derived default graph name. If the graph is empty, results are empty — no crash. |
| Removing `ProjectScopeRequiredError` breaks imports | Grep all imports before deletion; update every reference. |
| `prepare_project_scope_parameters` change affects plan cache | No Cypher query reads `$search_full` — removing the param is transparent to the query engine. |

## Verification Strategy

- `grep -rn "search_full" code-tiny/ doc-tiny/` → 0 matches (excluding
  plan/archive files).
- `grep -rn "ProjectScopeRequiredError" code-tiny/ doc-tiny/` → 0 matches.
- Unit tests: `test_project_scope_search_full.py` renamed/rewritten to
  test the simplified behavior (project_id present → filter; absent →
  None).
- Unit tests: `_resolve_graph_database` with `project_id=None` returns
  a graph name without raising.
- Integration: `test_unified_mcp_input_coercion.py` passes with updated
  assertions.
- `test_project_contract.py` (doc-tiny) updated and passes.

## Delivery Command

After approval, execute the plan with:

```text
/hi-craft plans/260728-0900-simplify-search-full-removal/plan.md
```
