# Plan: Remove `activate_project_removed` deprecation stub

**Status:** planning
**Created:** 2026-07-28
**Mode:** fast

## Context

`activate_project_removed` is a deprecation stub introduced when the original
`activate_project` tool was removed per the unified ingest/query contract.
It simply returns `{"deprecated": True, "message": "..."}` — no real logic.

The deprecation period has served its purpose. The user wants to remove the
stub entirely, along with all related metadata, tests, and testtool entries.

The stub currently exists in **5 MCP server files**, plus metadata, tests,
and testtool configuration. There is also WIP in `fastmcp_server.py` where
the `@mcp_server.tool(...)` decorator was already removed (function body
remains).

## Scope

### Files to modify

| # | File | Change |
|---|------|--------|
| 1 | `code-tiny/mcp/unified_mcp.py` | Remove `tool_activate_project_removed` function + decorator; remove entry from `_UNIFIED_TOOL_NAMES` frozenset |
| 2 | `code-tiny/mcp/fastmcp_server.py` | Remove orphaned `tool_activate_project_removed` function (decorator already removed in WIP) |
| 3 | `code-tiny/mcp/android/android_mcp.py` | Remove function + decorator; remove from `_ANDROID_TOOL_NAMES` |
| 4 | `code-tiny/mcp/cplus/cplus_mcp.py` | Remove function + decorator; remove from `_CPLUS_TOOL_NAMES` |
| 5 | `code-tiny/mcp/java/java_mcp.py` | Remove function + decorator; remove from `_JAVA_TOOL_NAMES` |
| 6 | `code-tiny/mcp/tool_metadata.py` | Remove 3 entries: `_FULL_CATALOG`, android catalog, overrides dict |
| 7 | `tests/test_unified_mcp_input_coercion.py` | Remove `test_activate_project_removed_returns_deprecation_notice` |
| 8 | `code-tiny/testtool/tool_defaults.py` | Remove from `TOOL_DEFAULTS` and `TOOL_CATEGORIES` |
| 9 | `code-tiny/testtool/input_exam/activate_project_removed.json` | **Delete file** |
| 10 | `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` | Update "Removed / Deprecated" table row |

### Files NOT to modify

- `docs/logs/2026-07-28-simplify-search-full-removal.md` — historical log, immutable record
- `plans/260728-*/**` — historical planning docs

## Phases

### Phase 01 — Remove from MCP server files

Remove `tool_activate_project_removed` (function + `@mcp_server.tool` decorator)
from all 5 MCP server files, and remove the name from each `_*_TOOL_NAMES`
frozenset.

- `unified_mcp.py` — lines ~66 (frozenset) and ~657-683 (function block)
- `fastmcp_server.py` — lines ~1269-1283 (orphaned function body)
- `android_mcp.py` — lines ~1338-1360 (function) and ~2869 (frozenset)
- `cplus_mcp.py` — lines ~1366-1388 (function) and ~2889 (frozenset)
- `java_mcp.py` — lines ~1064-1085 (function) and ~2030 (frozenset)

### Phase 02 — Remove from metadata and tests

- `tool_metadata.py`: remove `_FULL_CATALOG` entry (lines ~31-46), android
  catalog entry (lines ~831-838), and overrides dict entry (lines ~978-985)
- `tests/test_unified_mcp_input_coercion.py`: remove
  `test_activate_project_removed_returns_deprecation_notice` (lines ~407-415)

### Phase 03 — Remove from testtool and docs

- `tool_defaults.py`: remove `TOOL_DEFAULTS` entry (line ~19) and
  `TOOL_CATEGORIES` entry (line ~253)
- Delete `input_exam/activate_project_removed.json`
- Update `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` "Removed / Deprecated" table

### Phase 04 — Verify

- Run `python -m pytest tests/test_unified_mcp_input_coercion.py -q`
- Run `python -c "from code_tiny.mcp import unified_mcp"` import smoke test
- Grep for residual `activate_project_removed` references (should only remain
  in historical logs/plans)

## Verification criteria

- [ ] No `activate_project_removed` symbol in any `.py` file under `code-tiny/`
- [ ] No `activate_project_removed` key in `tool_defaults.py` or `tool_metadata.py`
- [ ] `input_exam/activate_project_removed.json` deleted
- [ ] Test suite passes without the removed test
- [ ] Module import smoke test passes for all 5 MCP server files
- [ ] Only historical references remain (logs, plans)
