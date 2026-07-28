# Phase 03 — Metadata, JSON examples, tests, verification

**Goal:** Update all non-Python artifacts and tests; grep-verify zero
`db` tool-input references remain.

## Step 3.1 — Update 18 JSON input examples

**Directory:** `code-tiny/testtool/input_exam/`

**Files with `"db"` key** (18 total):
- `plan_dependency_order.json`
- `query_subgraph.json`
- `trace_flow_between_module.json`
- `find_path_between_module.json`
- `plan_function_dependency_order.json`
- `get_symbol.json`
- `annotate_node.json`
- `listup_class_matching_path.json`
- `plan_file_dependency_order.json`
- `find_paths.json`
- `inspect_parser_capabilities.json` (currently `"db": ""`)
- `search_functions.json`
- `list_up_entrypoint.json`
- `listup_symbols_matching_file_path.json`
- `search_by_code.json`
- `trace_flow.json`
- `list_possible_calls.json`
- `get_node_details.json`

**Change per file:** remove the `"db"` key. If the tool is
project-scoped, add `"project_id": "<example_value>"` instead. For
graph tools that now accept `project_id`, add
`"project_id": "hyper_graph"` (matching the old `"db": "hyper_graph"`
value — since `db == project_id` by convention).

**Example** (`get_symbol.json`):
```json
// Before
{
    "node_id": 123,
    "db": "hyper_graph"
}

// After
{
    "node_id": 123,
    "project_id": "hyper_graph"
}
```

**Already correct** (no change needed):
- `get_public_apis.json` — already uses `"project_id": "cortext"`, no
  `"db"`.

## Step 3.2 — Update `tool_defaults.py` (NEW — from research)

**File:** `code-tiny/testtool/tool_defaults.py`

Contains a `TOOL_DEFAULTS` dict with a `"db"` key for ~17 tools
(search_functions, search_by_code, get_symbol, get_node_details,
query_subgraph, find_paths, find_path_between_module,
listup_symbols_matching_file_path, listup_class_matching_path,
list_up_entrypoint, trace_flow, trace_flow_between_module,
annotate_node, list_possible_calls, inspect_parser_capabilities,
plan_dependency_order, plan_file_dependency_order,
plan_function_dependency_order).

**Change:** for each tool entry, remove the `"db"` key. If the tool is
project-scoped, add `"project_id"` with an appropriate default (e.g.
`"hyper_graph"` to match the old `"db"` value, or `""` for optional).

## Step 3.2b — Check tool_metadata.py

**File:** `code-tiny/mcp/tool_metadata.py`

Grep for `"db"` references in the tool catalog. If any tool's metadata
declares a `db` input parameter, remove it and ensure `project_id` is
declared instead.

```bash
grep -n '"db"' code-tiny/mcp/tool_metadata.py
```

## Step 3.3 — Delete the bug-encoding test

**File:** `tests/test_unified_mcp_input_coercion.py`

**Delete:** `test_project_context_tool_explicit_db_overrides_project_id`
(line ~296). This test asserts `db="shared_graph"` overrides
`project_id="digital_key"` — the exact divergence the user reported.
After `db` removal, this test has no meaning.

## Step 3.4 — Update surviving tests

### `tests/test_unified_mcp_input_coercion.py`

- **`test_project_context_tool_scopes_database_to_project_id`** (line 258):
  No change needed — already asserts `project_id="digital_key"` targets
  the `"digital_key"` graph. This becomes the sole contract test.
- **Line 309** (`await tool(project_id="digital_key", db="shared_graph", limit=50)`):
  Remove the `db="shared_graph"` kwarg from the call. If this is inside
  the deleted test (Step 3.3), it's gone with it. If elsewhere, drop
  `db=`.
- **Lines 538, 563, 575** (`db="graph"`): These test framework/explore
  tools. Replace `db="graph"` with `project_id="graph"` (or remove if
  the test doesn't care about graph targeting — use `project_id=""`).
- **Line 49** (`self.assertEqual(captured["db"], "cortext")`): The
  internal payload key `"db"` still exists (holds resolved name). This
  assertion may still be valid — verify the captured dict still has
  `"db"` key with the resolved value. If the test passes
  `project_id="cortext"`, the resolved name is `"cortext"` → assertion
  still holds.

### `tests/test_framework_mcp_search.py`

- **Lines 27, 43, 65, 82, 115**: Replace `db="graph"` / `db="neo4j"`
  with `project_id=...` in all `tool(...)` calls. Use the same value
  (since `db == project_id` by convention).

### `tests/test_framework_mcp_flows.py`

- **Line 70**: Replace `db="hyper_graph"` with
  `project_id="hyper_graph"`.

### `tests/test_unified_mcp_wrapper_signatures.py`

- **Line 83** (`if argument.arg != "db":`): This AST check iterates tool
  wrapper arguments. Update the logic to no longer special-case `db` —
  or invert it to assert `db` is NOT present in any wrapper signature.

## Step 3.6 — Update documentation files (NEW — from research)

### `code-tiny/mcp/Readme.md` (~2600 lines)

Large tool-reference doc documenting `db` param for 40+ tools. Param
tables at ~40 line numbers (491, 768, 844, 880, 929, 975, 1050, 1084,
1156, 1194, 1230, 1264, 1296, 1329, 1363, 1398, 1434, 1571, 1604,
1641, 1688, 1723, 1766, 1807, 1843, 1884, 1927, 1963, 1997, 2031,
2063, 2114, 2147, 2182, 2217) and examples at many more.

**Notable:** line 2562 explicitly instructs callers to pass
`db: "hyper_graph"` plus `project_id` — directly contradicts the new
contract.

**Change:** for each tool's param table, remove the `db` row. Update
examples to use `project_id` only. Given the scale (~2600 lines), use
`/batch` or scripted edits — search for `db` in param tables and
remove consistently.

### `code-tiny/testtool/README.md`

Line 75 has an example payload with `"db"`. Update to use `"project_id"`.

### `code-tiny/Design.md`

Line 401 has a `_run_cypher(query, params, db)` example. This is an
**internal** call (the resolved name) — no change needed, but verify
the surrounding context doesn't imply `db` is a tool input.

## Step 3.7 — Run full verification

```bash
# 1. No tool signature has db as an input parameter
grep -rn 'db:.*str.*=\|db:.*Optional\[str\]' \
  code-tiny/mcp/unified_mcp.py \
  code-tiny/mcp/fastmcp_server.py \
  code-tiny/mcp/android/android_mcp.py \
  code-tiny/mcp/cplus/cplus_mcp.py \
  code-tiny/mcp/java/java_mcp.py
# Expect: 0 matches in async def tool_* signatures.
# (Internal helpers like _run_cypher are allowed.)

# 2. No JSON input example has "db"
grep -rn '"db"' code-tiny/testtool/input_exam/
# Expect: 0 matches.

# 3. Resolver is single-arg
grep -A3 'def _resolve_graph_database' code-tiny/mcp/unified_mcp.py
# Expect: only project_id parameter.

# 4. All 4 backend resolvers are project_id-aware
grep -A1 'def _resolve_db_candidates' \
  code-tiny/mcp/fastmcp_server.py \
  code-tiny/mcp/android/android_mcp.py \
  code-tiny/mcp/cplus/cplus_mcp.py \
  code-tiny/mcp/java/java_mcp.py
# Expect: project_id: Optional[str] in all 4.

# 5. Tests pass
cd /Users/hieplq1.rpm/AI/cortex-harness
python -m pytest tests/test_unified_mcp_input_coercion.py -x -q
python -m pytest tests/test_framework_mcp_search.py -x -q
python -m pytest tests/test_framework_mcp_flows.py -x -q
python -m pytest tests/test_unified_mcp_wrapper_signatures.py -x -q

# 6. Live smoke (if MCP server running):
# get_public_apis(project_id="digital_key") should return 9 modules.
```
