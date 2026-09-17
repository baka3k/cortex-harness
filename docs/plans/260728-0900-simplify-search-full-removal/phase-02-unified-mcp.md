# Phase 02 — unified_mcp.py: Tools + Resolution

## Objective

Simplify `_resolve_graph_database` to 2-way logic, remove `search_full`
from all 10 tool function signatures, and remove `ProjectScopeRequiredError`
imports/usage.

## Files

- `code-tiny/mcp/unified_mcp.py`

## Changes

### 1. Rewrite `_resolve_graph_database`

**Current** (3-way: db → project_id → search_full → error):

```python
def _resolve_graph_database(
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    search_full: bool = False,
) -> str:
```

**New** (2-way: db → project_id → env default):

```python
def _resolve_graph_database(
    db: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
    """Resolve one provider-neutral graph/database name.

    Precedence:
    1. ``db`` — explicit override, always wins.
    2. ``project_id`` — resolves through the ProjectRegistry.
    3. Neither — falls through to env defaults (FALKORDB_GRAPH,
       NEO4J_DB, DEFAULT_GRAPH_DB, "hyper_graph"). This is the
       full-search path.
    """
    requested = str(db or "").strip()
    if requested:
        return requested

    if project_id:
        normalized = project_id_lookup_key(project_id)
        if normalized:
            try:
                targets = resolve_project_targets(project_id)
                return targets.code_graph
            except ProjectNotRegisteredError:
                pass  # fall through to env defaults

    # Full search: resolve from env defaults
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("NEO4J_DB")
        or str(cplus_backend.DEFAULT_GRAPH_DB)
        or "hyper_graph"
    )
```

Key changes:
- `search_full` parameter removed.
- No `ProjectScopeRequiredError` — missing `project_id` falls through to
  env defaults silently (full search).
- If `project_id` is given but not registered, fall through to env
  defaults instead of raising (graceful degradation).

### 2. Update `_run_project_context_tool`

Remove `search_full: bool = False` from parameters and from the
`_resolve_graph_database` call:

```python
# BEFORE
async def _run_project_context_tool(
    *,
    tool_name: str,
    project_id: str,
    parser_type: str,
    db: str,
    search_full: bool = False,
    ...
) -> Dict[str, Any]:
    database = _resolve_graph_database(
        db=db,
        project_id=project_id or None,
        search_full=search_full,
    )

# AFTER
async def _run_project_context_tool(
    *,
    tool_name: str,
    project_id: str,
    parser_type: str,
    db: str,
    ...
) -> Dict[str, Any]:
    database = _resolve_graph_database(
        db=db,
        project_id=project_id or None,
    )
```

### 3. Update 6 project-context tools

Remove `search_full: bool = False` from signatures and the
`search_full=search_full` kwarg from `_run_project_context_tool` calls:

- `tool_get_project_modules` (line ~2126)
- `tool_get_public_apis` (line ~2161)
- `tool_get_endpoints` (line ~2198)
- `tool_get_module_architecture_summary` (line ~2237)
- `tool_get_project_special_files` (line ~2270)
- `tool_get_framework_context` (line ~2313)

Pattern for each:

```python
# BEFORE
async def tool_get_project_modules(
    ...
    search_full: bool = False,
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        ...
        search_full=search_full,
        ...
    )

# AFTER
async def tool_get_project_modules(
    ...
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        ...
        ...
    )
```

### 4. Update 2 bridge tools

- `tool_find_callers_of_endpoint` (line ~2362): remove `search_full`
  param, remove from `_resolve_graph_database` call, remove from
  docstring.
- `tool_get_api_call_chain` (line ~2496): same pattern.

### 5. Update 2 workflow tools

- `tool_analyze_workflow_impact` (line ~2729): remove `search_full`
  param, remove from `_resolve_graph_database` call, remove from
  docstring.
- `tool_find_workflows_containing` (line ~2913): same pattern.

### 6. Remove `ProjectScopeRequiredError` import

Remove from the import block at the top of `unified_mcp.py`:

```python
# DELETE this import:
from tools.common.project_registry import ProjectScopeRequiredError
```

Keep `ProjectNotRegisteredError` if still needed, or remove if no longer
referenced after the rewrite.

### 7. Update deprecation notice

`tool_activate_project_removed` (line ~660): remove `search_full` from
the deprecation message:

```python
# BEFORE
"project-scoped call. Callers must pass ``project_id`` (or "
"``search_full=true`` for cross-project queries) on every "

# AFTER
"project-scoped call. Callers must pass ``project_id`` to scope "
"to one project; omit it for cross-project queries. "
```

### 8. `explore_graph` call (line 1879)

Already calls `_resolve_graph_database(db=db)` without `search_full` —
no change needed.

## Verification

- `grep -n "search_full" code-tiny/mcp/unified_mcp.py` → 0 matches.
- `grep -n "ProjectScopeRequiredError" code-tiny/mcp/unified_mcp.py` → 0 matches.
- `_resolve_graph_database(db="", project_id=None)` returns a non-empty
  string without raising.
- `_resolve_graph_database(db="my_graph", project_id=None)` returns
  `"my_graph"`.
- `_resolve_graph_database(db="", project_id="cortext")` returns
  `"cortext"` (or the registry-resolved graph name).
