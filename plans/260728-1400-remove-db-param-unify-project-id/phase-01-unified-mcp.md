# Phase 01 — unified_mcp.py: resolver + signatures + payloads

**File:** `code-tiny/mcp/unified_mcp.py` (~3043 lines)
**Goal:** Remove `db` from all tool signatures and the central resolver;
`project_id` becomes the sole graph-targeting key.

## Step 1.1 — Simplify `_resolve_graph_database`

**Location:** line 1956

**Before:**
```python
def _resolve_graph_database(
    db: Optional[str] = None,
    project_id: Optional[str] = None,
) -> str:
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
                pass
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("NEO4J_DB")
        or str(cplus_backend.DEFAULT_GRAPH_DB)
        or "hyper_graph"
    )
```

**After:**
```python
def _resolve_graph_database(project_id: Optional[str] = None) -> str:
    """Resolve one provider-neutral graph name for direct MCP tools.

    project_id present → registry → targets.code_graph
    project_id absent  → env defaults (full search)
    """
    if project_id:
        normalized = project_id_lookup_key(project_id)
        if normalized:
            try:
                targets = resolve_project_targets(project_id)
                return targets.code_graph
            except ProjectNotRegisteredError:
                pass
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("NEO4J_DB")
        or str(cplus_backend.DEFAULT_GRAPH_DB)
        or "hyper_graph"
    )
```

Remove the `db` branch entirely. The docstring is updated to reflect
the single-arg contract.

## Step 1.2 — Update `_run_project_context_tool`

**Location:** line 2044

Remove `db: str` from the signature. The body already calls
`_resolve_graph_database(db=db, project_id=...)` — change to
`_resolve_graph_database(project_id=project_id or None)`.

## Step 1.3 — Update the 6 project-context tool signatures

These tools currently have both `db: str = ""` and `project_id: str = ""`:

- `tool_get_project_modules` (~line 2104)
- `tool_get_public_apis` (~line 2137)
- `tool_get_endpoints` (~line 2172)
- `tool_get_module_architecture_summary` (~line 2209)
- `tool_get_project_special_files` (~line 2240)
- `tool_get_framework_context` (~line 2281)

**Change:** remove `db: str = ""` from each signature. Remove `db=db`
from the `_run_project_context_tool(...)` call in each body.

## Step 1.4 — Update the 2 bridge tool signatures

- `tool_find_callers_of_endpoint` (~line 2328) — has `db: str = ""`
- `tool_get_api_call_chain` (~line 2459) — has `db: str = ""`

**Change:** remove `db: str = ""`. In the body, change
`_resolve_graph_database(db=db, project_id=...)` →
`_resolve_graph_database(project_id=...)`.

## Step 1.5 — Update the 2 workflow tool signatures

- `tool_analyze_workflow_impact` (~line 2686) — has `db: str = ""`
- `tool_find_workflows_containing` (~line 2865) — has `db: str = ""`

**Change:** same as Step 1.4.

## Step 1.6 — Update `explore_graph`

**Location:** line 1878

**Before:**
```python
active_db = _resolve_graph_database(db=db) if db else None
```

**After:**
```python
active_db = _resolve_graph_database(project_id=project_id or None) if project_id else None
```

Also remove `db: str = ""` from the `explore_graph` signature if present
and add `project_id: str = ""` if missing.

## Step 1.7 — Update Category B graph tools (no existing project_id)

These tools have `db: str = ""` but NO `project_id`:

- `tool_inspect_parser_capabilities` (line 717)
- `tool_list_qdrant_collections` (line 800)
- `tool_plan_dependency_order` (line 897)
- `tool_plan_file_dependency_order` (line 920)
- `tool_plan_function_dependency_order` (line 947)

**Change per tool:** replace `db: str = ""` with `project_id: str = ""`,
then in the body resolve `_db = _resolve_graph_database(project_id=project_id or None)`
and set the payload `"db": _db` (internal key stays).

## Step 1.7b — Verify `_register_payload_tool` closure

**Location:** line 971 — defines a generic `_tool` closure whose
signature includes `db` (line 975) and `project_id` (~line 1022).

**Action:** grep for call sites of `_register_payload_tool(`. The
researcher found only the `def` — no call site. If confirmed dead,
**delete the function** rather than editing it. If live, apply the same
`db` → `project_id` transformation as other tools.

## Step 1.7c — Dispatched-tool payload forwarding (Pattern 2)

**Critical mechanism (from research):** Category A tools that are
*dispatched* (not bridge tools) pack BOTH `db` and `project_id` into a
`merged` payload dict → `_dispatch_tool(tool_name, merged)` → the
per-language backend reads `payload.get("db")` and calls its own
`_resolve_db_candidates(db)`. This means `db` flows as a **payload key**
all the way into the backend resolver.

**Tools following Pattern 2** (~22 dispatched tools, Category A):
- `tool_annotate_node` (1085), `tool_semantic_search` (1111),
  `tool_trace_flow_between_module` (1162), `tool_trace_flow` (1212),
  `tool_find_screen_workflows` (1267), `tool_list_up_entrypoint` (1297),
  `tool_listup_class_matching_path` (1323),
  `tool_listup_symbols_matching_file_path` (1357),
  `tool_find_path_between_module` (1417), `tool_find_paths` (1460),
  `tool_query_subgraph` (1517), `tool_get_node_details` (1571),
  `tool_list_possible_calls` (1601), `tool_get_symbol` (1636),
  `tool_search_by_code` (1667), `tool_search_functions` (1706),
  `tool_get_ipc_message` (1749), `tool_explore_graph` (1790)

**Change per tool:**
1. Remove `db: str = ""` from signature.
2. In the body, STOP forwarding raw `db` into the payload. Instead
   resolve `_db = _resolve_graph_database(project_id=project_id or None)`
   and set `payload["db"] = _db` so the backend still receives a graph
   name via its existing `_resolve_db_candidates` path.
3. Phase 02 then makes the backend `_resolve_db_candidates` ignore the
   payload `db` and use `project_id` directly — but the payload key
   stays as a carrier for the resolved name (backward compat with the
   HTTP dispatch layer).

**Why both phases are needed:** Phase 01 stops the unified wrapper from
forwarding raw user `db`. Phase 02 makes the backend resolver
`project_id`-aware. Until both land, the backend still falls back to
the payload `db` value — which is now the resolved name, not raw input.

**Change per tool:**
1. Replace `db: str = ""` with `project_id: str = ""` in the signature.
2. In the body, resolve the graph name:
   ```python
   _db = _resolve_graph_database(project_id=project_id or None)
   ```
3. Replace all `"db": db` in the payload dict with `"db": _db` (the
   internal payload key stays `"db"` — downstream dispatch reads it;
   only the VALUE changes from raw input to resolved name).

**Example** (`tool_plan_dependency_order`, line 900):
```python
# Before
async def tool_plan_dependency_order(
    modules: str = "",
    parser_type: str = "",
    db: str = "",
    ...
) -> Dict[str, Any]:
    payload = {
        ...
        "db": db if db else None,
        ...
    }

# After
async def tool_plan_dependency_order(
    modules: str = "",
    parser_type: str = "",
    project_id: str = "",
    ...
) -> Dict[str, Any]:
    _db = _resolve_graph_database(project_id=project_id or None)
    payload = {
        ...
        "db": _db,
        ...
    }
```

## Step 1.8 — Update capability-gate calls that pass db

Several tool bodies pass `db` to `_resolve_direct_capability_context` or
include it in error payloads (lines 328, 337, 412, 728, 735, 772, 808).
Change each from raw `db` input to the resolved `_db` variable.

## Verification (after Phase 01)

```bash
# No tool signature should have db: str = ""
grep -n 'db:.*str.*=.*""' code-tiny/mcp/unified_mcp.py
# Should return 0 matches in async def tool_* signatures.

# The resolver is single-arg:
grep -A2 'def _resolve_graph_database' code-tiny/mcp/unified_mcp.py
# Should show only project_id parameter.

# Internal payload keys "db": are fine — they hold resolved names:
grep -c '"db":' code-tiny/mcp/unified_mcp.py
# Non-zero — expected (internal plumbing).
```
