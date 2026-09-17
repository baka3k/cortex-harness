# Phase 03: Run-All Batch Mode

## Context

Every tool must be invoked one at a time by hand. When verifying a server
change, there is no single command to "run everything and tell me what
broke." This phase adds a **run-all** command to the interactive menu that
batch-executes every tool with its default payload, continues past errors,
and feeds the results into the Phase 04 error reporter.

## Requirements

- Add a new command to the `interactive()` main loop: `a` (or `run-all`).
  It runs **every non-stale tool** (respecting the current filter/category
  scope if the operator wants a subset; `*` + `a` runs the full set).
- For each tool in the run set:
  1. Load the default payload via `get_default(tool_name)`.
  2. Call `client.call_tool(tool_name, payload)`.
  3. Capture: result or exception, elapsed time, status
     (`PASS` / `FAIL` / `EMPTY`).
  4. Print inline progress: `[12/40] search_functions … PASS 0.3s`
     (or `… FAIL 0.1s` / `… EMPTY 0.2s`).
  5. On exception, record full detail (Phase 04) and **continue**.
- Status definitions:
  - `PASS` — call returned a non-empty, non-error result.
  - `EMPTY` — call succeeded but returned an empty result (`[]`, `{}`,
    `{"functions": []}`, etc.). This is expected for tools whose default
    payload uses a placeholder `project_id`/`node_id` that matches no data.
  - `FAIL` — call raised an `MCPError` or a generic `Exception`.
- After the last tool, print the **summary table** (Phase 04) and save the
  full log to `outputs/testtool-runall-{timestamp}.json`.
- The run set excludes `stale` tools (Phase 02: defaults with no matching
  server tool — they would fail with "unknown tool").
- The run set excludes `activate_project_removed` (it always returns a
  deprecation notice — not useful in a regression sweep). Mark it
  `SKIP` in the log.
- A `Ctrl-C` mid-run stops the sweep gracefully: print the partial summary
  for tools already executed and mark the rest `ABORTED`.
- Respect the per-call timeout (`--timeout`, default 120s). A timeout counts
  as `FAIL` with the timeout detail.

## Proposed interaction

```
  40/40 tools  |  <number> select  /text filter  c category  * all  a run-all  q quit

  Select a

  Run-all: 39 tools (1 skipped)   scope: all

  [ 1/39] activate_project_removed … SKIP
  [ 2/39] list_databases … PASS 0.1s
  [ 3/39] list_parsers … PASS 0.1s
  [ 4/39] inspect_parser_capabilities … PASS 0.2s
  ...
  [17/39] get_symbol … FAIL 0.1s
  [18/39] query_subgraph … EMPTY 0.3s
  ...
  [39/39] find_workflows_containing … EMPTY 0.2s

  ──────────────────────────────────────────────────────────────
  SUMMARY                                            38.4s total
  ──────────────────────────────────────────────────────────────
   PASS   12   list_databases, list_parsers, compute_scc, ...
   EMPTY  18   search_functions, get_symbol (placeholder ids), ...
   FAIL    8   get_symbol, query_subgraph, find_paths, ...
   SKIP    1   activate_project_removed
  ──────────────────────────────────────────────────────────────

  Failures (8):
  ──────────────────────────────────────────────────────────────
  get_symbol
    payload: {"node_id": "YOUR_NODE_ID", "project_id": "hyper_graph", ...}
    elapsed: 0.12s
    error:   MCPError: HTTP 400: {"error":"node not found: YOUR_NODE_ID"}
    trace:   Traceback (most recent call last):
               File "mcp_client.py", line 112, in call_tool
                 ...
  ──────────────────────────────────────────────────────────────

  Log saved → outputs/testtool-runall-20260728-153000.json
```

## Implementation notes

- Add a `_run_all(client, tools, sync_report, scope_filter)` function that
  owns the loop, status capture, progress print, summary, and log save.
- Reuse `get_default(tool_name)` for payloads — do not duplicate the
  priority chain.
- The summary table groups by status (`PASS`/`EMPTY`/`FAIL`/`SKIP`) and
  lists tool names per group (truncated with `…` if long).
- The log file is a JSON object:
  ```json
  {
    "started_at": "2026-07-28T15:30:00",
    "finished_at": "2026-07-28T15:30:38",
    "total": 39,
    "summary": {"pass": 12, "empty": 18, "fail": 8, "skip": 1},
    "results": [
      {"tool": "get_symbol", "status": "fail", "elapsed": 0.12,
       "payload": {...}, "error": "...", "traceback": "..."},
      ...
    ]
  }
  ```
- The `EMPTY` detector: a result is empty if it is `[]`, `{}`, or a dict
  whose values are all empty lists (`{"functions": []}`, `{"messages": []}`).
  Keep the heuristic simple — false `EMPTY` is harmless (it still ran).
- Guard the `a` command the same way `c` is guarded: only treat bare `a` as
  run-all if no visible tool is literally named `a` (none today).

## Related Files

- `code-tiny/testtool/mcp_tester.py` (add `_run_all`, wire `a` command into
  `interactive()`, summary rendering, log save)

## Todo

- [ ] Add `_run_all(client, tools, sync_report, scope_filter)`.
- [ ] Implement status capture (PASS/EMPTY/FAIL/SKIP) + inline progress.
- [ ] Implement summary table rendering.
- [ ] Implement log-file save to `outputs/testtool-runall-{timestamp}.json`.
- [ ] Wire `a` command into `interactive()` main loop.
- [ ] Handle `Ctrl-C` graceful abort (partial summary).
- [ ] Smoke-test against a live server with a known-broken tool.

## Success Criteria

- `a` runs every non-stale tool, prints inline progress, and produces a
  summary table.
- Errors do not stop the sweep; each failure is captured with full detail.
- The log file is saved and re-loadable as JSON.
- `Ctrl-C` produces a partial summary without a traceback crash.
