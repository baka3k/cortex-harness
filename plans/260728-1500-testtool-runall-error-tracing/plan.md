---
title: "Testtool: Run-All Batch Mode, Error Tracing & Contract Sync"
status: pending
created: 2026-07-28
mode: hi-plan --full
scope: code-tiny/testtool — run-all batch runner, error/exception tracing, hybrid live-discover sync, contract alignment to post-db-removal MCP
blockedBy: []
supersedes:
  - 260727-testtool-mcp-coverage-and-ux
relatedPlans:
  - 260728-1400-remove-db-param-unify-project-id
  - 260728-0900-simplify-search-full-removal
  - 260728-0000-unified-ingest-query-contract
reviewed: 2026-07-28
---

# Testtool: Run-All Batch Mode, Error Tracing & Contract Sync

## Overview

The interactive MCP tester (`code-tiny/testtool`) is the fastest way to call
MCP tools without an agent, but it has fallen behind on three fronts:

1. **Contract drift.** The MCP server removed `db` from every tool signature
   (working tree, plan `260728-1400`) and removed `search_full` (merged
   `a706610`). The tester's static data is mostly clean, but the coverage
   check (`_check_coverage.py`) **fails**: `input_exam/activate_project.json`
   is a stale filename that should be `activate_project_removed.json`, and a
   few default payloads are missing newer parameters. The README still shows
   a `"db"` example.
2. **No batch runner.** Every tool must be invoked one at a time by hand.
   When verifying that a server change did not break the tool surface, there
   is no "run everything and tell me what broke" command.
3. **Shallow error reporting.** `_run_tool` prints only the exception
   message — no stack trace, no request payload echo, no timing on failure.
   Tracing an MCP error means re-reading the server logs instead of seeing
   the failure inline.

This plan closes all three gaps and makes the tester self-healing against
future server-side tool additions via a **hybrid live-discover** sync.

## Scope Challenge Decisions

### 1. Run-all semantics

**Selected: default payload, continue on error.** Run every tool with its
default payload (`get_default(tool)` priority chain: `input_exam/{tool}.json`
→ `TOOL_DEFAULTS` → `{}`). On error, record the failure (stack trace +
request payload + elapsed) and **continue** to the next tool. Print a
summary table at the end: `[tool] [PASS/FAIL] [elapsed]`. Failures get full
detail inline; a complete log is saved to `outputs/`. This is the fastest
path to "trace MCP" — one command surfaces every broken tool.

### 2. Tool surface sync mechanism

**Selected: hybrid live-discover + defaults fallback.** The tester already
calls `tools/list` at startup. Enhance it to compare the live tool surface
against `TOOL_DEFAULTS`:

- Tool on server **and** in defaults → normal (payload from defaults).
- Tool on server, **missing** from defaults → render in the `Other` bucket,
  payload `{}`, flag with a `⚠ no default` hint in the menu.
- Tool in defaults, **not** on server → stale; warn at startup, hide from
  the run-all set (calling it would error anyway).

This makes the tester resilient: when the server adds a tool, it appears
immediately without a code edit; when the server removes a tool, the tester
does not waste a run-all cycle on it.

### 3. Error reporting depth

**Selected: summary table + full detail on failures.**

- **Summary table** (always, run-all mode): one row per tool —
  `tool_name | PASS/FAIL | elapsed | note`.
- **Full detail** (failures only): stack trace, request payload, response
  snippet (first 500 chars), elapsed time.
- **Log file**: the complete run (summary + every failure's full detail)
  is saved to `outputs/testtool-runall-{timestamp}.json` for post-run
  review and diffing across runs.

Single-tool runs get the same enhanced error detail inline.

## Verified Current Behavior

- `testtool/tool_defaults.py` — `TOOL_DEFAULTS` has **40 entries** matching
  the 40 registered tools in `unified_mcp.py` (working tree, post-db-removal).
  `TOOL_CATEGORIES` covers all 40 across 7 buckets. No `"db"` or
  `"search_full"` keys remain in any default.
- `testtool/_check_coverage.py` — **FAILS**:
  `input_exam/activate_project.json` (old name) vs
  `TOOL_DEFAULTS["activate_project_removed"]` (new key). One stale filename
  breaks the 1:1 invariant.
- `testtool/input_exam/*.json` — 41 files, all clean of `"db"` keys.
  `explore_graph.json` is missing the newer `project_id`/`collection`/
  `debug`/`parser_type` params (optional, so not a break, but stale).
- `testtool/mcp_tester.py::_run_tool` — catches `MCPError` and generic
  `Exception`, prints only `str(exc)`. No traceback, no payload echo, no
  timing on the error path. No batch runner exists.
- `testtool/mcp_client.py` — protocol layer is stable and correct
  (initialize → notifications/initialized → tools/list → tools/call). No
  change needed; the `call_tool` unwrapping handles `content`/`isError`.
- `testtool/README.md:75` — example payload still shows `"db": "hyper_graph"`.
- `mcp/unified_mcp.py` (working tree) — `db` parameter is **gone** from all
  40 tool signatures; `_resolve_graph_database(project_id)` is the sole
  resolver (line 1848). `search_full` is gone (merged `a706610`). Confirmed
  by grep: 0 matches for `db:\s*str\s*=` in signatures.

## Phases

1. [Phase 01 — Contract sync & coverage fix](phase-01-contract-sync.md)
2. [Phase 02 — Hybrid live-discover sync](phase-02-hybrid-discover.md)
3. [Phase 03 — Run-all batch mode](phase-03-run-all.md)
4. [Phase 04 — Error reporting & tracing](phase-04-error-tracing.md)

## Contract Decisions

- `project_id` is the **sole** scoping key in every default payload. No
  default carries `"db"` or `"search_full"`. Verified clean; this phase
  enforces it going forward via the coverage check.
- The coverage check (`_check_coverage.py`) is the gatekeeper: it must pass
  before any phase is marked done. It asserts 1:1 lockstep across
  `TOOL_DEFAULTS`, `TOOL_CATEGORIES`, and `input_exam/*.json`.
- Run-all uses the **default payload** only (no inline editing). Tools that
  require a real `project_id`/`node_id` (placeholders `"YOUR_PROJECT_ID"`,
  `"YOUR_NODE_ID"`) will return an error or empty result by design — the
  run-all report flags these as `FAIL/EMPTY` so the operator knows which
  tools need live data vs. which are genuinely broken.
- The log file is the durable artifact: `outputs/testtool-runall-{timestamp}.json`.
  It contains the summary table + full per-tool detail, structured for both
  human reading and programmatic diffing.

## Dependencies

- **Follows `260728-1400-remove-db-param-unify-project-id`** (working tree):
  the `db` removal from tool signatures is already in the working tree
  (uncommitted). Phase 01 verifies the tester reflects this; no blocking
  dependency since the changes coexist.
- **Follows `260728-0900-simplify-search-full-removal`** (merged `a706610`):
  `search_full` is already gone from the tester. No action needed.
- **Supersedes `260727-testtool-mcp-coverage-and-ux`**: that plan added the
  19 missing defaults + categorized UX (implemented). This plan builds on
  that foundation with run-all + error tracing + hybrid sync, and fixes the
  one stale artifact (`activate_project.json`) that plan's Phase 04 missed.
- Does **not** modify the MCP server. Read-only consumer of `tools/list`
  and `tools/call`.

## Out of Scope

- No changes to `mcp_client.py` protocol handling (stable and correct).
- No new MCP tools, no server-side behavior changes.
- No automated pytest suite for the tester itself (the tester is a manual
  helper; `_check_coverage.py` is the cheap automated gate). Verification
  is a smoke run against a live server.
- No CSV batch-run or HTTP-retry features (future ideas per README).
- No schema-validation pass before calling a tool (the server validates;
  run-all reports the server's validation error if the payload is wrong).

## Server-side observations flagged (not fixed by this plan)

Research surfaced two server-side defects that affect the tester but are out
of scope here. The tester will **surface** them (via hybrid discover and
run-all), making diagnosis easier, but the fix belongs in the server layer:

1. **Duplicate entries in `tool_metadata.py::_FULL_CATALOG`.** `trace_flow_between_module`
   and `activate_project_removed` each appear **twice** in the list. Because
   `build_catalog()` appends every matching entry, `list_mcp_functions`
   emits duplicated rows for both. `_CATALOG_BY_NAME` (dict comprehension)
   masks it for dict lookups. The hybrid discover (Phase 02) dedupes by tool
   name, so the tester is unaffected — but the server's own catalog output is
   polluted. Fix: dedupe `_FULL_CATALOG` (server-side, separate change).
2. **Stale docstrings mentioning `db:`.** Five tools (`explore_graph`,
   `find_callers_of_endpoint`, `get_api_call_chain`, `analyze_workflow_impact`,
   `find_workflows_containing`) still document a `db:` arg in their docstring
   even though the parameter no longer exists in the signature. Harmless to
   execution but misleading. Fix: scrub docstrings (server-side, separate change).

## Risks

| Risk | Mitigation |
| --- | --- |
| Run-all calls tools with placeholder payloads (`YOUR_PROJECT_ID`) → mass FAIL noise | Expected by design. The report distinguishes `FAIL` (exception/error) from `EMPTY` (valid call, no data) so the operator sees which tools need real inputs vs. which are broken. Documented in README. |
| Hybrid discover flags many tools as "no default" if the server registers tools the tester never heard of | That is the feature working as intended — the operator sees the drift immediately. Adding a default is a one-line edit in `tool_defaults.py`. |
| A long run-all (40 tools × network round-trip) is slow | Print progress inline (`[12/40] search_functions … PASS 0.3s`) so the operator sees live progress, not a blank wait. Default timeout per call stays at 120s. |
| Log file accumulates in `outputs/` | Use timestamped filenames; do not overwrite. Operator cleans up manually. |
| Run-all against a server with a broken graph returns errors that look like tester bugs | The failure detail includes the server's error message verbatim, making the root cause visible without server log diving. |

## Success Criteria

- `python code-tiny/testtool/_check_coverage.py` exits 0.
- `python code-tiny/testtool/mcp_tester.py` against a live server shows all
  40 tools grouped by category with no sync warnings (server and defaults
  agree).
- Selecting the run-all command executes every tool with its default
  payload, continues past errors, and prints a summary table + full detail
  for each failure.
- A broken tool (e.g., one that raises) produces an inline stack trace +
  request payload + elapsed time in single-run mode, and a structured entry
  in the saved log file in run-all mode.
- No default payload contains `"db"` or `"search_full"` (grep-verified).

## Delivery Command

After approval, execute the plan with:

```text
/hi-craft plans/260728-1500-testtool-runall-error-tracing/plan.md
```
