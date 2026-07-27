# Phase 02: Test Data Files (input_exam)

## Context

`input_exam/` holds ready-to-load JSON payloads so testers can try a tool with
one keystroke. The 19 new tools have no such files. We add one example file per
new tool, using realistic-but-generic values that match the Phase 01 defaults
and the documented examples in `tool_metadata.py`.

## Requirements

- Add one `{tool_name}.json` file under `testtool/input_exam/` for each of the
  19 new tools listed in Phase 01.
- Each file must be valid JSON and conform to the tool's input shape (verified
  against the `unified_mcp.py` signature).
- Use the same placeholder convention (`YOUR_PROJECT_ID`, `YOUR_NODE_ID`) where
  a real id is required, so files are safe to load without leaking project data.
- Keep example values small and self-documenting (e.g., `nodes: ["auth",
  "payment"]` for planning tools, not abstract `["A","B"]`).
- For `reconstruct_flow`, embed the JSON-string fields (`entry_context_json`,
  `paths_json`) exactly as documented in `tool_metadata.py`'s example.

## File list (19)

```
input_exam/inspect_parser_capabilities.json
input_exam/compute_scc.json
input_exam/topological_sort.json
input_exam/plan_dependency_order.json
input_exam/plan_file_dependency_order.json
input_exam/plan_function_dependency_order.json
input_exam/find_screen_workflows.json
input_exam/explore_graph.json
input_exam/reconstruct_flow.json
input_exam/get_project_modules.json
input_exam/get_public_apis.json
input_exam/get_endpoints.json
input_exam/get_module_architecture_summary.json
input_exam/get_project_special_files.json
input_exam/get_framework_context.json
input_exam/find_callers_of_endpoint.json
input_exam/get_api_call_chain.json
input_exam/analyze_workflow_impact.json
input_exam/find_workflows_containing.json
```

## Content guidelines

- Project-context tools: `"project_id": "YOUR_PROJECT_ID"` plus one realistic
  filter (e.g., `get_endpoints` → `"protocol": "http"`; `get_framework_context`
  → `"framework": "spring"`).
- Planning tools: 2–3 node tokens drawn from a plausible module layout
  (`"auth"`, `"payment"`, `"user"`) and 2 edges, so the example produces a
  non-empty wave/SCC result on a real graph.
- `find_screen_workflows`: pair mode example (`HomeScreen` → `DetailScreen`)
  plus a commented single-mode alternative in the file is not possible (JSON has
  no comments) — keep pair mode only.
- `reconstruct_flow`: copy the spec example verbatim — it is the canonical
  shape and doubles as documentation.

## Related Files

- `code-tiny/testtool/input_exam/*.json` (create 19)
- `code-tiny/mcp/tool_metadata.py` (source of `example` fields)

## Todo

- [ ] Create 19 JSON files matching the Phase 01 defaults.
- [ ] Validate each parses (`python -c "import json; json.load(open(...))"`).
- [ ] Spot-check 3 files against `unified_mcp.py` signatures for field-name parity.

## Risks

- Example values that look real but reference a non-existent project can confuse
  a newcomer. Mitigation: every such field uses the `YOUR_*` placeholder, and
  the README already explains placeholders via the `get_symbol` example.

## Success Criteria

- `glob input_exam/*.json` returns one file per tool in `TOOL_DEFAULTS` (after
  Phase 04 removes stale entries), all valid JSON.
