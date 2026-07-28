---
title: "Remove `db` parameter — unify on `project_id` for graph targeting"
status: pending
created: 2026-07-28
mode: hi-plan --full
scope: code-tiny MCP tool signatures (5 server files), resolution helpers, JSON metadata, tests
blockedBy: []
relatedPlans:
  - 260728-0000-unified-ingest-query-contract
  - 260728-0900-simplify-search-full-removal
  - 260725-1703-project-topology-context-tools
reviewed: 2026-07-28
---

# Remove `db` parameter — unify on `project_id` for graph targeting

## Overview

Every project-scoped MCP tool currently accepts **two** parameters that
compete for the same job: `db` and `project_id`. Since the convention is
`db == project_id` (each project's graph is named after the project), `db`
is pure redundancy. Worse, when both are passed, `db` wins precedence in
`_resolve_graph_database` and shadows `project_id`, but the two parameters
flow through different downstream code paths (capability gate, routing,
payload construction) — producing **different results** for identical
intent:

```json
{"project_id": "digital_key", "db": "digital_key", ...}  // path A
{"db": "digital_key", ...}                                 // path B — different result
```

This is the same class of ambiguity that `search_full` removal fixed: two
params doing one job. The fix is identical in spirit — **hard remove `db`**
from every MCP tool signature. The single key a client passes is
`project_id`.

### Resolution contract (confirmed)

```
project_id present  → resolve via ProjectRegistry → targets.code_graph
project_id absent   → env-default graph (FALKORDB_GRAPH → NEO4J_DB →
                      DEFAULT_GRAPH_DB → "hyper_graph")
                      = implicit full search across all projects
```

No error is raised for missing `project_id`. This is the same contract
shipped by the `search_full` removal (commit `a706610`).

### Critical scope note: internal plumbing stays

`db` as a **tool-input parameter** is removed. `db`/`database` as an
**internal variable name** (the resolved graph name string passed to
`_run_cypher`, `driver.execute_query`, `driver.find_node_by_id`, graph
HTTP payloads) is **unchanged**. The refactor boundary is the tool
function body: input `project_id` → resolve to graph name → pass that
name downstream as before.

## Scope Challenge Decisions

### 1. Removal scope: all tools, all servers

**Selected: hard remove from ALL MCP tools.** The user confirmed: every
tool signature loses `db`; any tool that needs a graph target gets
`project_id` instead. This spans all 5 server files
(`unified_mcp.py`, `fastmcp_server.py`, `android_mcp.py`,
`cplus_mcp.py`, `java_mcp.py`). Internal helpers (`_run_cypher`,
`graph_service._normalise_db`, driver calls) keep the resolved name.

### 2. Fallback for tools without project_id

**Selected: env-default full search.** When `project_id` is absent, the
resolver falls through to env defaults — same as the post-`search_full`
contract. No `ProjectScopeRequiredError`, no error. Empty `project_id` =
"search everywhere in the env-default graph".

### 3. Relationship to the unified-contract plan

**Selected: standalone, run now.** This plan is independent of
`260728-0000-unified-ingest-query-contract` (which is broader and still
pending). The bidirectional dependency is noted in both plans. This is
the same pattern used by `260728-0900-simplify-search-full-removal`.

## Verified Current Behavior

### Central resolver (`unified_mcp.py:1956`)

```python
def _resolve_graph_database(db=None, project_id=None) -> str:
    # 1. db explicit → return db          (the bug: shadows project_id)
    # 2. project_id    → registry resolve → targets.code_graph
    # 3. neither       → env defaults
```

Called from 6 sites in `unified_mcp.py`:
- `_run_project_context_tool` (line 2056) — passes both `db` and `project_id`
- `explore_graph` (line 1878) — `_resolve_graph_database(db=db) if db else None`
- `find_callers_of_endpoint` (line 2366)
- `get_api_call_chain` (line 2492)
- `analyze_workflow_impact` (line 2722)
- `find_workflows_containing` (line 2889)

### Per-backend resolvers (4 copies)

`fastmcp_server.py:239`, `android_mcp.py:232`, `cplus_mcp.py:237`,
`java_mcp.py:188` each define:

```python
def _resolve_db_candidates(db: Optional[str]) -> List[str]:
    candidates = []
    if db and str(db).strip():
        candidates.append(_normalize_db_name(str(db).strip()))
    default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
    if default_db and default_db not in candidates:
        candidates.append(default_db)
    return candidates
```

These are **not** registry-aware — they ignore `project_id` entirely.
They return a list (for sequential fallback), but after resolution the
list always collapses to a single resolved name.

### Tool signature counts

| File | `db:` signatures | Category A (db+project_id) | Category B (db only) | Notes |
|------|-----------------|---------------------------|---------------------|-------|
| `unified_mcp.py` | 33 | 28 | 5 | + `_register_payload_tool` closure (verify if dead) |
| `fastmcp_server.py` | 18 | 14 | 4 | |
| `android_mcp.py` | 15 | 14 | 1 | `semantic_search` has no `db` (payload-only) |
| `cplus_mcp.py` | 16 | 16 | 0 | |
| `java_mcp.py` | 16 | 16 | 0 | `query_subgraph`/`find_paths` have required-style `db` |
| **Total** | **98** | **88** | **10** | |

### Payload downstream (`"db": db` keys)

~30 payload dicts in `unified_mcp.py` pass `{"db": db, ...}` to
`_dispatch_planner_tool`, `_dispatch_query_tool`, capability gates, etc.
These internal payload keys stay — the VALUE changes from raw `db` input
to the resolved graph name.

### JSON metadata + docs

- **18 JSON files** in `code-tiny/testtool/input_exam/*.json` contain a
  `"db"` key (e.g. `"db": "hyper_graph"`). `get_public_apis.json`
  already uses only `"project_id"` (no `"db"`) — confirming the target
  contract.
- **`code-tiny/testtool/tool_defaults.py`** — `TOOL_DEFAULTS` dict with
  `"db"` key for ~17 tools (in-code fallback for tool payloads).
- **`code-tiny/mcp/Readme.md`** (~2600 lines) — documents `db` param
  for 40+ tools in param tables and examples. Line 2562 explicitly
  instructs callers to pass `db: "hyper_graph"` plus `project_id`.
- **`code-tiny/testtool/README.md:75`** — example payload with `"db"`.
- **`code-tiny/Design.md:401`** — `_run_cypher` example (internal call,
  likely no change needed).

### Test encoding the bug

`tests/test_unified_mcp_input_coercion.py:296` —
`test_project_context_tool_explicit_db_overrides_project_id` explicitly
asserts `db="shared_graph"` overrides `project_id="digital_key"`. This
test encodes the exact divergence the user reported and is **deleted**.

The sibling test `test_project_context_tool_scopes_database_to_project_id`
(line 258) — asserting `project_id="digital_key"` targets the
`"digital_key"` graph — becomes the sole contract.

Other affected tests: `test_framework_mcp_search.py` (4 calls with
`db=...`), `test_framework_mcp_flows.py` (1 call with `db=...`),
`test_unified_mcp_wrapper_signatures.py:83` (AST check for `db` arg).

## Phases

1. [Phase 01 — unified_mcp.py: resolver + 34 signatures + payloads](phase-01-unified-mcp.md)
2. [Phase 02 — Per-backend servers: 4 resolvers + ~73 signatures](phase-02-backend-servers.md)
3. [Phase 03 — Metadata, JSON examples, tests, verification](phase-03-metadata-tests.md)

## Cross-Plan Dependencies

- **Related `260728-0000-unified-ingest-query-contract`** — that plan
  introduces a full `ProjectRegistry` and naming contract. This plan
  reuses the **already-shipped** registry subset
  (`resolve_project_targets`, `project_id_lookup_key`,
  `ProjectNotRegisteredError` in `tools/common/project_registry.py`).
  The bidirectional note is applied to both plans. No blocking dependency.
- **Follows `260728-0900-simplify-search-full-removal`** (merged in
  commit `a706610`) — same contract pattern, same removal style. That
  plan removed `search_full`; this plan removes `db`. Together they
  leave `project_id` as the sole scoping key.
- **Reuses `260725-1703-project-topology-context-tools`** (completed) —
  the `_run_project_context_tool` fix that introduced
  `db or project_id` resolution; this plan removes the `db` half.

## Scope Boundaries

### Included

- Remove `db` parameter from every MCP tool signature in all 5 server
  files.
- Simplify `_resolve_graph_database(db, project_id)` → single-arg
  `(project_id)`.
- Make the 4 per-backend `_resolve_db_candidates(db)` functions
  `project_id`-aware via the registry.
- Add `project_id: str = ""` to Category B tools (graph tools) that
  currently have only `db`.
- Update 18 JSON input examples.
- Update/delete affected tests.
- Grep-verify zero `db` tool-signature references remain.

### Excluded

- Changing internal variable names (`db`, `database`, `db_candidate`)
  inside function bodies — they hold the resolved graph name.
- Changing graph driver interfaces (`_run_cypher`, `execute_query`,
  `find_node_by_id`).
- Changing graph HTTP service layer (`graph_service._normalise_db`,
  HTTP payloads).
- Changing the `ProjectRegistry` resolution logic or naming contract.
- Changing actual Cypher query templates.
- doc-tiny (already clean — no `db` parameter).

## Success Criteria

- `grep -rn 'db:.*str.*=.*""' code-tiny/mcp/*.py code-tiny/mcp/**/*.py`
  returns **0 matches** in tool signatures (internal helpers exempt).
- `grep -rn '"db"' code-tiny/testtool/input_exam/*.json` returns **0
  matches**.
- Every MCP tool that targets a graph accepts `project_id: str = ""` and
  nothing else for graph targeting.
- `_resolve_graph_database(project_id)` never raises for missing
  `project_id` — falls through to env defaults.
- The 4 per-backend `_resolve_db_candidates(project_id)` resolve via
  the registry when `project_id` is present.
- Passing `db=...` to any tool returns "unknown parameter" (hard
  removal, no silent ignore).
- `test_project_context_tool_explicit_db_overrides_project_id` is
  deleted; all other tests pass after assertion updates.
- Live smoke: `get_public_apis(project_id="digital_key")` returns the
  same result regardless of whether the caller previously would have
  passed `db`.

## Risks

| Risk | Mitigation |
| --- | --- |
| External clients passing `db=...` break | By design — hard removal, same as `activate_project` and `search_full`. Document in CHANGELOG. |
| Category B tools (graph tools) lose explicit graph override | Replaced by `project_id` → registry resolution. Unregistered `project_id` falls through to env default — graceful, same as unified_mcp. |
| Per-backend `_resolve_db_candidates` registry import creates circular dependency | Registry lives in `tools/common/project_registry.py` (no MCP imports) — no circular risk. Already imported by `unified_mcp.py`. |
| 107 signatures = high miss rate | Phase-by-file with grep verification after each. The researcher site-map (background) cross-checks completeness. |
| `_resolve_db_candidates` return-type change (list → ?) | Keep returning `List[str]` — downstream iteration (`for db_candidate in candidates`) stays valid with a 1-element list. |

## Verification Strategy

- `grep -rn 'db:\s*str\s*=' code-tiny/mcp/` → 0 in tool `async def`
  signatures (allow internal helpers like `_run_cypher`).
- `grep -rn '"db"' code-tiny/testtool/input_exam/` → 0.
- Unit tests: `test_unified_mcp_input_coercion.py` updated and passes.
- Unit tests: `test_framework_mcp_search.py`, `test_framework_mcp_flows.py`
  updated and pass.
- `test_unified_mcp_wrapper_signatures.py` AST check updated (no longer
  asserts `db` arg exists).
- Live smoke: two calls that previously diverged now match —
  `get_public_apis(project_id="digital_key")` == previously
  `get_public_apis(project_id="digital_key", db="digital_key")`.

## Delivery Command

After approval, execute the plan with:

```text
/hi-craft plans/260728-1400-remove-db-param-unify-project-id/plan.md
```
