# Phase 01: Contract Sync & Coverage Fix

## Context

The coverage check (`_check_coverage.py`) currently **fails** because
`input_exam/activate_project.json` is a stale filename — the tool was renamed
to `activate_project_removed` but the JSON file was never renamed to match.
Additionally, a few default payloads are missing newer optional parameters,
and the README still shows a `"db"` example that no longer matches the
contract (`project_id` is the sole scoping key).

This phase makes the coverage check pass and aligns all static data with the
post-`db`-removal, post-`search_full`-removal MCP contract.

## Requirements

- Rename `input_exam/activate_project.json` →
  `input_exam/activate_project_removed.json` so the stem matches the
  `TOOL_DEFAULTS` key. Update its content to the deprecation-stub shape
  (`{}` or `{}`  with `parser_type`/`database_name` if desired — the stub
  accepts the same args but always returns a deprecation notice).
- Audit every `input_exam/*.json` and every `TOOL_DEFAULTS` entry against the
  current tool signatures in `unified_mcp.py` (working tree). Fix any payload
  that still carries a `"db"` key or a `"search_full"` key. (Verified clean
  today, but the audit is the gate.)
- Refresh stale payloads missing newer optional params. **Payload-content
  drift found in research** (params accepted by the signature that the default
  omits, or vice versa):
  - `explore_graph.json`/`TOOL_DEFAULTS` — missing `project_id`, `collection`,
    `debug`, `parser_type`. Add them.
  - `search_functions`, `search_by_code`, `list_up_entrypoint`,
    `list_possible_calls` defaults still pass `top_k`, but the signatures now
    use `limit` (`top_k` is accepted as an alias by the coercion layer, but
    `limit` is the canonical name per `tool_metadata.py`). Migrate to `limit`.
  - `find_screen_workflows` default missing `include_entry_function`,
    `include_api_calls` (both optional, default false). Add for completeness.
  - Spot-check `semantic_search`, `trace_flow` for param completeness against
    `tool_metadata.py::_FULL_CATALOG`.
- Note: 2 tools (`analyze_workflow_impact`, `find_workflows_containing`) have
  a **required** `function_id` (no default). Their defaults use the
  `"YOUR_NODE_ID"` placeholder, so run-all will FAIL them by design — the
  operator must edit the payload before running these two. This is expected
  and documented; not a bug to fix.
- Update `testtool/README.md`:
  - Line ~75: replace the `"db": "hyper_graph"` example with
    `"project_id": "hyper_graph"`.
  - Update the "Default payloads" note to reflect 40-tool coverage and the
    `project_id`-only contract.
- Run `python code-tiny/testtool/_check_coverage.py` — must exit 0.
- Grep-verify: `grep -rn '"db"' code-tiny/testtool/input_exam/` → 0 matches.
  `grep -rn 'search_full' code-tiny/testtool/` → 0 matches.

## Implementation notes

- The rename is a `git mv` (or delete + create) to preserve history where
  possible.
- `_check_coverage.py` already enforces the 1:1 invariant across
  `TOOL_DEFAULTS`, `TOOL_CATEGORIES`, and `input_exam/*.json`. After the
  rename it passes without logic changes.
- Do **not** add `"db"` back to any payload as a "convenience" — the server
  no longer accepts it as a tool-input parameter (it returns
  "unknown parameter"). `project_id` is the only scoping key.
- The audit against `tool_metadata.py::_FULL_CATALOG` is the source of truth
  for input shapes. If the catalog and the actual signature disagree, trust
  the actual signature (the catalog is documentation; the signature is the
  contract).

## Related Files

- `code-tiny/testtool/input_exam/activate_project.json` → rename to
  `activate_project_removed.json`
- `code-tiny/testtool/input_exam/explore_graph.json` (refresh)
- `code-tiny/testtool/tool_defaults.py` (`explore_graph` entry refresh)
- `code-tiny/testtool/README.md` (example + notes)
- `code-tiny/testtool/_check_coverage.py` (no change — just must pass)

## Todo

- [ ] Rename `activate_project.json` → `activate_project_removed.json`.
- [ ] Refresh `explore_graph` default + JSON with newer params.
- [ ] Spot-check 3-5 other payloads against `_FULL_CATALOG`.
- [ ] Fix README example (`"db"` → `"project_id"`).
- [ ] Run `_check_coverage.py` → must exit 0.
- [ ] Grep-verify zero `"db"` / `search_full` in testtool data.

## Success Criteria

- `_check_coverage.py` exits 0.
- No payload contains `"db"` or `"search_full"`.
- README example uses `"project_id"`.
