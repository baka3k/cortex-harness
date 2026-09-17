# Phase 04: Stale Cleanup & Verification

## Context

Two stale artifacts predate the current tool surface and reference no real tool.
Removing them keeps `TOOL_DEFAULTS`, `input_exam/`, and the live server in sync,
which is the whole point of the coverage work in Phases 01–02.

## Requirements

- Remove `get_id_by_name` from `TOOL_DEFAULTS` in `tool_defaults.py`. It is not
  a registered MCP tool (grep across `code-tiny/mcp` returns no registration);
  `search_functions` already returns IDs.
- Remove `testtool/input_exam/test_find_path.json`. `test_find_path` is not a
  tool name; it is a scratch file with placeholder `xxx`/`yyy` modules.
- Remove `testtool/input_exam/get_id_by_name.json` if present (none today, but
  guard against it).
- After removal, assert a 1:1 correspondence: every key in `TOOL_DEFAULTS` has
  an `input_exam/{key}.json`, and every `input_exam/*.json` filename (except
  the documented overrides) matches a `TOOL_DEFAULTS` key.

## Verification (manual smoke)

The tester is a manual helper with no pytest suite of its own; verification is a
live smoke run against a running MCP server.

1. Start the unified MCP server (`dev mcp start` or equivalent) on the default
   endpoint.
2. Run `python testtool/mcp_tester.py` and confirm:
   - The grouped menu renders all categories and ~40 tools.
   - `/api` filter surfaces description matches (`find_callers_of_endpoint`,
     `get_api_call_chain`, `get_endpoints`).
   - `c` → `Planning & Dependency` scopes the list to the 5 planning tools.
   - Selecting `compute_scc` loads the Phase 02 example and runs against the
     server without a payload-shape error.
   - `--tool explore_graph` jumps directly and loads its default.
3. Confirm no `get_id_by_name` appears anywhere in the menu or defaults.

## Automated checks (cheap, no server)

- `python -c "import json; from pathlib import Path; ..."` script that:
  - Imports `TOOL_DEFAULTS` and `TOOL_CATEGORIES`.
  - Asserts `set(TOOL_DEFAULTS) == set(tool names appearing in categories)`.
  - Asserts every `input_exam/*.json` parses and its stem is a `TOOL_DEFAULTS`
    key.
- Run after Phases 01–03 land; treats the assertion failure as a build break.

## Related Files

- `code-tiny/testtool/tool_defaults.py` (remove `get_id_by_name`)
- `code-tiny/testtool/input_exam/test_find_path.json` (delete)
- `code-tiny/testtool/README.md` (remove any `get_id_by_name` mention if present)

## Todo

- [ ] Remove `get_id_by_name` default and any matching `input_exam` file.
- [ ] Delete `test_find_path.json`.
- [ ] Add the 1:1 correspondence check script under `testtool/` (e.g.
      `testtool/_check_coverage.py` or a `python -c` one-liner documented in
      README) — keep it dependency-free.
- [ ] Run the manual smoke list above and record results in this file.
- [ ] Update README "Default payloads" note to reflect 40-tool coverage.

## Risks

- If anyone relied on `get_id_by_name` as a local shortcut, removal breaks them.
  Mitigation: it never dispatched (the server has no such tool), so it could
  only ever return an MCP error; removing it loses nothing functional. Note the
  removal in the commit message.

## Success Criteria

- `TOOL_DEFAULTS`, `TOOL_CATEGORIES`, and `input_exam/*.json` all describe the
  same set of real tools.
- Live smoke run completes the six checks without error.
- README no longer references removed entries.
