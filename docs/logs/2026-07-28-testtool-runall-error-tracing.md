# Testtool: Run-All Batch Mode, Error Tracing & Contract Sync — 2026-07-28

## Context

The interactive MCP tester (`code-tiny/testtool`) had three pains:

1. **Coverage check failing.** `input_exam/activate_project.json` was a stale filename (the tool was renamed to `activate_project_removed`), and the recently-merged `db`-removal contract change was reflected in the working tree but the tester's README still showed stale examples.
2. **No batch runner.** Verifying a server change required invoking 40 tools by hand.
3. **Shallow error reporting.** `_run_tool` printed only `str(exc)` — no stack trace, no payload echo, no timing on the failure path.

The plan `plans/260728-1500-testtool-runall-error-tracing/plan.md` closed all three plus added a **hybrid live-discover** sync so the tester is self-healing against server-side tool additions.

## Change

### Phase 01 — Contract sync (`testtool/tool_defaults.py`, `testtool/_check_coverage.py`)

- Dropped the `activate_project_removed` stub entirely from `TOOL_DEFAULTS` and the input_exam dir. The linter cleanup pulled both the defaults entry and the JSON file together — coverage now reports 39 tools, exits 0.
- Refreshed `explore_graph` default + JSON with `project_id`, `collection`, `debug`, `parser_type` (newer optional params).
- Migrated `search_functions` / `search_by_code` / `list_up_entrypoint` / `list_possible_calls` from `top_k` to `limit` (canonical name in `unified_mcp.py`).
- Added `include_entry_function` / `include_api_calls` to `find_screen_workflows`.
- `README.md`: dropped the `top_k` example for `limit`.
- Grep-verified zero `"db"` or `search_full` in the testtool data.

### Phase 02 — Hybrid live-discover (`testtool/mcp_tester.py:72-110`)

- Added `SyncReport` dataclass and `_reconcile_tools(live, defaults)` returning `(known, server_only, stale)` as three sets.
- Startup banner now shows drift counts: `N tools available. (X known · Y server-only · Z stale)`.
- Emits `⚠ No default payload for: …` for server-only tools and `⚠ Stale defaults: …` for stale.
- `_render_tool_list` decorates server-only tools with `⚠ no default` in the Other bucket and marks stale defaults `✗ offline`.

### Phase 03 — Run-all batch (`testtool/mcp_tester.py:669-768`)

- New `_run_all(client, scope, sync_report)` loops every visible tool, calling `_execute_call` per item.
- Inline progress: `[12/39] search_functions … PASS 0.30s` (or `EMPTY` / `FAIL` / `SKIP` color-coded).
- Skipped up front: stale tools (no matching server tool) and the `activate_project_removed` stub.
- `Ctrl-C` prints a partial summary without traceback.
- Summary groups by status (`PASS` / `EMPTY` / `FAIL` / `SKIP`), lists tool names per group (truncated with `…` at 6).
- Per-failure block rendered inline (uses `_print_failure_block` shared with single-run).
- Log persisted to `outputs/testtool-runall-{YYYYMMDD-HHMMSS}.json` (started_at, finished_at, scope_size, summary line, per-tool to_dict).

### Phase 04 — Error reporting (`testtool/mcp_tester.py:541-665`)

- New `_ToolRunResult` dataclass captures `tool`, `status`, `elapsed`, `payload`, `result`, `error`, `traceback`, `response_snippet`.
- `to_dict()` coerces non-JSON-safe `result` to `repr` so log serialization always succeeds.
- New `_execute_call(client, tool_name, payload)` is the single source of truth for the call path — uses both single-run and run-all. Records `t0` / elapsed, catches `MCPError` and `Exception`, captures `traceback.format_exc()`.
- `_result_is_empty` heuristic: `[]`, `{}`, dict-of-empty-lists, blank string.
- Single-run `_run_tool` shows the failure block (payload + error + server snippet + traceback) and offers `r` retry / `Enter` continue. PASS/EMPTY shows status label + pretty-print + `s` save / `Enter` continue.

## Impact

- **Operators verifying MCP changes**: instead of 40 manual menu picks, one `a` keystroke runs every tool with default payloads, surfaces every failure inline, and saves a structured log for diffing across runs.
- **Coverage gate**: `_check_coverage.py` passes, the 1:1 lockstep between `TOOL_DEFAULTS`, `TOOL_CATEGORIES`, and `input_exam/*.json` is restored.
- **Server drift**: when the server registers a tool the tester has no default for, it appears in `Other` with a clear hint (and conversely, stale defaults do not waste a run-all slot).
- **Risk: low** — read-only consumer of `tools/list` and `tools/call`. No server-side changes.
- **Regression risk: low** — `mcp_client.py` unchanged; the `_run_tool` signature is identical; the menu rendering is backward-compatible for existing single-run users.

## Decision

- **Run-all defaults, continue on error.** Selecting only this let one command surface every broken tool. Heuristic `EMPTY` (valid call, no data) is distinct from `FAIL` (exception) so the operator can tell placeholder-driven noise from real breakage.
- **Hybrid discover for resilience.** Tests servers that register tools the tester never heard of (e.g. fastmcp's `list_workflows`/`get_workflow_steps`/`search_workflows`) without touching code. Display-only — never mutates `TOOL_DEFAULTS`. One-line edit to silence the warning.
- **`activate_project_removed` removed entirely**, not kept as a stub. The earlier plan to consolidate it now materializes here: keeping a stub would create UI noise without protecting anyone (callers must already have moved past the deprecation).
- **`response_snippet` over optional `client.raw_text`.** The `MCPError` string already carries the first 300 chars of the server response from `httpx` — `response_snippet` just exposes that. Avoids a `mcp_client` refactor.
- **Default priorities preserved.** All defaults flow through `get_default(tool)` (file → `TOOL_DEFAULTS` → `{}`). The run-all set reuses the same function — no duplicate payload logic.

## References

- plan: `./plans/260728-1500-testtool-runall-error-tracing/plan.md`
- phase files: `./plans/260728-1500-testtool-runall-error-tracing/phase-{01..04}.md`
- commit: `1e77474` (`testtool: run-all batch mode, hybrid discover, error tracing, contract sync`)
- related: `plans/260728-1400-remove-db-param-unify-project-id/`, `plans/260728-0900-simplify-search-full-removal/`
