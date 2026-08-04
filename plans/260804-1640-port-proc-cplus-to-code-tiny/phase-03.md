# Phase 03: Update MCP, constraints, driver, routing

## Context

The comprehensive patch adds MCP aliases, DB constraints, driver label lookups, and MCP flow relationship types. code-tiny's structure differs from the patch's assumptions — adapt accordingly.

## Files and changes

### `code-tiny/mcp/framework_registry.py`

**Structure**: Uses `CAPABILITIES` dict with `_generic_profile()`, NOT `GENERIC_PRIMARY_ALIASES`.

1. Add `"proc"`, `"pro*c"`, `"pro-c"` to cplus aliases set (~line 435).
2. Replace `CPLUS_LABELS = GENERIC_LABELS | frozenset({"CplusSqlStatement"})` with:
   ```
   CPLUS_LABELS = GENERIC_LABELS | frozenset({
       "SqlStatement", "SqlDirective", "SqlCursor",
       "SqlHostVariable", "DatabaseTable",
   })
   ```
3. Replace `CPLUS_RELATIONSHIPS` `DEFINES` with the 9 comprehensive types:
   ```
   DECLARES_STATEMENT, DECLARES_DIRECTIVE, BINDS_PARAMETER,
   DECLARES_CURSOR, REFERENCES_CURSOR, REFERENCES_STATEMENT,
   READS_FROM, WRITES_TO, REFERENCES_TABLE
   ```
4. Add database support to cplus profile (`support["database"] = "full"` or similar).

### `code-tiny/mcp/cplus/cplus_mcp.py`

- `PARSER_ALIASES_CPLUS` and `DEFAULT_FLOW_REL_TYPES_CPLUS` are **derived** from the registry. After Phase 03's framework_registry changes, they auto-update. Verify no hardcoded overrides remain.

### `code-tiny/mcp/tool_metadata.py`

- Update `parser_type` description to include `proc/pro*c/pro-c`.

### `code-tiny/scripts/setup_constraints.py`

- Add `PROC_LABELS` tuple and constraint/index loop (from patch):
  ```python
  PROC_LABELS = ("SqlStatement", "SqlDirective", "SqlCursor",
                  "SqlHostVariable", "DatabaseTable")
  ```
- Each label gets uniqueness constraint + file lookup index.

### `code-tiny/tools/graph/driver/neo4j_driver.py`

- Add 5 Pro*C labels to `_FALLBACK_ID_LOOKUP_LABELS`.

### `code-tiny/tools/sync/incremental_sync.py` + `owner_manifest.py`

- **Already handle `.pc`/`.pcc`** — no change needed.

## Success criteria

- `python -m py_compile` passes for all modified files.
- `grep -r "CplusSqlStatement" code-tiny/` returns 0 matches.
- `grep -r "SqlStatement" code-tiny/mcp/framework_registry.py` returns matches.
- `grep "proc" code-tiny/mcp/framework_registry.py` shows alias entries.
