# Phase 02 — graph_mcp Stateless Query Path

## Goal

Make every project-scoped `graph_mcp` tool resolve its graph and collection
through `resolve_project_targets(project_id)` on each call. Remove silent
env-only fallbacks in the query path. `activate_project` becomes an optional
default only. Add a `search_full` escape hatch so callers can opt into a
cross-project query without a `project_id`.

## Query precedence

Every project-scoped tool follows this precedence (applies to all 5
`_resolve_graph_database` call sites, `semantic_search`, and `explore_graph`):

1. `project_id` present → resolve shard via registry, filter on
   `project_id_normalized`.
2. `project_id` absent + `search_full=true` → query the active graph/collection
   **without** the `project_id_normalized` filter (returns rows from all
   projects that share that graph).
3. `project_id` absent + `search_full=false` (default) → raise
   `ProjectScopeRequiredError`.

`activate_project` and `active_project` state are **removed entirely** (per
Validation Interview). There is no stateful default.

## Deliverables

- `code-tiny/mcp/unified_mcp.py`:
  - Replace `_resolve_graph_database(db)` with
    `_resolve_graph_database(db=None, project_id=None, search_full=False)`.
    When `db` is provided it wins (escape hatch). Otherwise, when
    `search_full=True` resolve via the first registered graph (no project
    filter applied in the Cypher). When `project_id` is provided resolve via
    registry. The combination of missing `db`, `project_id`, and
    `search_full=False` raises `ProjectScopeRequiredError`.
  - Update all 5 call sites to accept and forward `search_full`:
    - `_run_project_context_tool` (line 2050) — already passes `project_id`;
      ensure registry resolution; honor `search_full`.
    - `find_callers_of_endpoint` (2351) — resolve via `be_project_id` (or
      `fe_project_id` when only frontend is in scope); honor `search_full`.
    - `get_api_call_chain` (2466) — same.
    - `analyze_workflow_impact` (2690) — same.
    - `find_workflows_containing` (2850) — same.
  - Add a shared helper `_should_skip_project_filter(project_id, search_full)`
    that returns `True` only when `search_full=True` and `project_id` is
    absent. The Cypher predicate becomes
    `AND ($search_full OR n.project_id_normalized = $project_id_normalized)`
    so a single plan cache is reused for both modes.
  - **Remove `activate_project` tool and `active_project` module-level dict**
    entirely (per Validation Interview). Audit and remove all references.
- Backend modules: add `search_full` arg and forward it to the Qdrant filter
  and graph predicate builders. Replace module-level
  `DEFAULT_GRAPH_DB`/`DEFAULT_QDRANT_*` constants' usage in query paths with
  registry resolution when a `project_id` is available:
  - `code-tiny/mcp/cplus/cplus_mcp.py`
  - `code-tiny/mcp/android/android_mcp.py`
  - `code-tiny/mcp/java/java_mcp.py`
  - `code-tiny/mcp/services/explore_service.py`
  - `code-tiny/mcp/services/semantic_search_service.py` (if present)
- `semantic_search` and `explore_graph` tools: when the caller passes
  `project_id`, derive `collection` from the registry if not explicitly
  provided. When the caller passes `search_full=true` and no `project_id`,
  keep the explicit `collection` arg (or default) and skip the
  `qdrant_project_filter`. An explicit `collection` arg always wins (escape
  hatch).
- `code-tiny/tools/common/project_scope.py`: extend `qdrant_project_filter`
  with an optional `search_full=False` arg — when true, return `None` (no
  filter). Extend `prepare_project_scope_parameters` so the
  `$search_full` parameter is always set on the Cypher params dict.
- Tests:
  - Contract tests: every project-scoped tool raises `ProjectScopeRequiredError`
    when `search_full=false` and `project_id` is absent (no stateful default).
  - `search_full=true` + no `project_id` returns rows from both `projA` and
    `projB`; the Cypher predicate parameter `$search_full` is `true`.
  - `search_full=true` + `project_id="projA"` is accepted but `project_id`
    wins — result is scoped to A (document this precedence).
  - Two-project test: ingest fixtures for `projA` and `projB`; query `projA`
    returns only A's data; query `projB` returns only B's.
  - Existing capability gates still pass; they now consult the
    registry-resolved graph.
  - **Harness audit test**: `harness/scripts/orchestrator.py` and
    `harness/scripts/context_selector.py` call sites are updated to pass
    `project_id` or `search_full=true`; no call site relies on the removed
    `activate_project` default.

## Out of Scope

- Ingest path changes (Phase 03).
- `mind_mcp` query path (Phase 05).
- Launcher env semantics (Phase 06).

## Acceptance

- All 5 `_resolve_graph_database` call sites use the registry when
  `project_id` is available and honor `search_full` when it is set.
- No query silently falls back to an env-derived graph name when `project_id`
  is absent and `search_full` is false.
- `search_full=true` returns cross-project results; `search_full=false` (the
  default) preserves per-project isolation.
- Existing MCP regression suite passes (with test fixtures updated to pass
  `project_id` or `search_full` explicitly).
