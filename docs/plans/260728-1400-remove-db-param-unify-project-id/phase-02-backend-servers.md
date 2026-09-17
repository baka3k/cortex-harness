# Phase 02 — Per-backend servers: 4 resolvers + ~73 signatures

**Files:**
- `code-tiny/mcp/fastmcp_server.py` (20 `db:` sites)
- `code-tiny/mcp/android/android_mcp.py` (17 `db:` sites)
- `code-tiny/mcp/cplus/cplus_mcp.py` (18 `db:` sites)
- `code-tiny/mcp/java/java_mcp.py` (18 `db:` sites)

**Goal:** Remove `db` from tool signatures; make `_resolve_db_candidates`
`project_id`-aware via the registry.

## Step 2.1 — Add registry imports to all 4 files

Each file currently imports from `cplus_backend` and local helpers but
NOT from `tools.common.project_registry`. Add at the top (near existing
`tools.common` imports if present, or after the backend import):

```python
from tools.common.project_registry import (  # noqa: E402
    ProjectNotRegisteredError,
    resolve_project_targets,
)
```

**Files to update:** all 4. Check existing import block style in each
file and match it.

## Step 2.2 — Make `_resolve_db_candidates` project_id-aware

**4 identical copies** at:
- `fastmcp_server.py:239`
- `android_mcp.py:232`
- `cplus_mcp.py:237`
- `java_mcp.py:188`

**Before (all 4 identical):**
```python
def _resolve_db_candidates(db: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if db and str(db).strip():
        candidates.append(_normalize_db_name(str(db).strip()))
    default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
    if default_db and default_db not in candidates:
        candidates.append(default_db)
    return candidates
```

**After:**
```python
def _resolve_db_candidates(project_id: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if project_id and str(project_id).strip():
        try:
            targets = resolve_project_targets(project_id)
            graph_name = _normalize_db_name(targets.code_graph)
            if graph_name and graph_name not in candidates:
                candidates.append(graph_name)
        except ProjectNotRegisteredError:
            pass  # Fall through to default.
    default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
    if default_db and default_db not in candidates:
        candidates.append(default_db)
    return candidates
```

**Rationale:** The return type stays `List[str]` — all downstream
`for db_candidate in candidates:` loops remain valid. When `project_id`
is present and registered, the list is `[that_project_graph]`. When
absent or unregistered, it's `[DEFAULT_GRAPH_DB]`.

## Step 2.3 — Update tool signatures + bodies

Each of the 4 files has ~17–20 tool functions with this pattern:

```python
async def tool_get_symbol(
    node_id: Any = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    ...
) -> Dict[str, Any]:
    ...
    candidates = _resolve_db_candidates(db)
    ...
```

**Change per tool:**
1. Remove `db: Optional[str] = None` from the signature.
2. Ensure `project_id: Optional[str] = None` exists (add if missing).
3. Change `candidates = _resolve_db_candidates(db)` →
   `candidates = _resolve_db_candidates(project_id)`.
4. Remove `db` from any payload dict constructed for HTTP dispatch
   (e.g. `payload["db"]`), OR replace its value with
   `candidates[0] if candidates else None` so the HTTP service layer
   still receives a graph name.

**Tools to update in each file** (verify exact names by grep):

### fastmcp_server.py (~20 tools)
- `tool_search_functions`, `tool_search_by_code`, `tool_get_symbol`,
  `tool_get_node_details`, `tool_find_paths`, `tool_query_subgraph`,
  `tool_trace_flow`, `tool_trace_flow_between_module`,
  `tool_find_path_between_module`, `tool_list_up_entrypoint`,
  `tool_list_possible_calls`, `tool_listup_class_matching_path`,
  `tool_listup_symbols_matching_file_path`, `tool_annotate_node`,
  `tool_compute_scc`, `tool_topological_sort`,
  `tool_plan_dependency_order`, `tool_plan_file_dependency_order`,
  `tool_plan_function_dependency_order`, `tool_inspect_parser_capabilities`

### android_mcp.py (~17 tools)
Same tool names as fastmcp (android is a parallel backend).

### cplus_mcp.py (~18 tools)
Same tool names as fastmcp (cplus is a parallel backend).

### java_mcp.py (~18 tools)
Same tool names as fastmcp (java is a parallel backend).

### Special case: tools that already use project_id for node filtering

Some tools (e.g. `tool_get_symbol` in android_mcp.py) already have
`project_id: Optional[str] = None` and pass it to
`driver.find_node_by_id(project_id=project_id, database=db_candidate)`.
For these:
- Remove `db` from the signature (Step 3.1 above).
- Keep the `project_id=project_id` argument to `find_node_by_id` —
  unchanged.
- Only the `_resolve_db_candidates` call changes input from `db` to
  `project_id`.

### Special case: java_mcp.py required-style db params

`java_mcp.py` `query_subgraph` (line 1386) and `find_paths` (line 1464)
declare `db: Optional[str]` with **no default** (required-style). When
removing `db`, ensure `project_id: Optional[str] = None` is added with
a default — these become optional under the new contract.

### Special case: android `search_functions` (Category B)

`android_mcp.py` `search_functions` (line 2673, db at 2677) has `db`
but **no `project_id` param** — it reads `project_id` only from the
payload dict. Add `project_id: Optional[str] = None` to the signature.

### Special case: android `semantic_search` (no db param)

`android_mcp.py` `tool_semantic_search` (line 1412) does **not** declare
a `db` param (reads it only from payload). No signature change needed —
but verify the payload path doesn't forward a stale `db`.

## Step 2.4 — Payload key `"db"` stays (Pattern 2 compatibility)

**Critical:** the unified wrapper dispatches tools by packing args into
a `payload` dict and calling `_dispatch_tool(tool_name, payload)`. The
backend reads `payload.get("db")`. After Phase 01, the unified wrapper
sets `payload["db"] = _db` (the resolved graph name). The backend's
`_resolve_db_candidates(project_id)` then receives `project_id` from
the same payload and resolves it — but as a fallback, the payload
`"db"` key still carries the resolved name.

**Net effect:** even if the backend `_resolve_db_candidates` is called
with the payload `db` value (now resolved), it gets the correct graph.
After Phase 02 makes it `project_id`-aware, it resolves directly from
`project_id` and the payload `db` becomes a redundant carrier (harmless).

## Step 2.4 — Update `_run_cypher` calls (NO change)

`_run_cypher(query, params, db)` in each file takes `db` as an internal
positional arg (the resolved graph name). **Do not change** — it
receives `db_candidate` from the iteration, which is now the
registry-resolved name.

## Verification (after Phase 02)

```bash
# No tool signature in any backend file has db: Optional[str] or db: str
grep -n 'db:.*Optional\[str\]\|db:.*str.*=' \
  code-tiny/mcp/fastmcp_server.py \
  code-tiny/mcp/android/android_mcp.py \
  code-tiny/mcp/cplus/cplus_mcp.py \
  code-tiny/mcp/java/java_mcp.py
# Should return 0 matches in tool signatures.

# All 4 resolvers are project_id-aware:
grep -A1 'def _resolve_db_candidates' \
  code-tiny/mcp/fastmcp_server.py \
  code-tiny/mcp/android/android_mcp.py \
  code-tiny/mcp/cplus/cplus_mcp.py \
  code-tiny/mcp/java/java_mcp.py
# Should show project_id: Optional[str] parameter.
```
