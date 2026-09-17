# Phase 02: Hybrid Live-Discover Sync

## Context

The tester already calls `tools/list` at startup and uses the result as the
primary tool source, falling back to `TOOL_DEFAULTS` only when the server
returns nothing. But it does not **reconcile** the two: if the server
registers a tool the tester has no default for, that tool silently gets `{}`
as a payload with no hint. If the tester carries a default for a tool the
server no longer registers, that stale entry lingers in the menu.

**Concrete gap found in research:** `fastmcp_server.py` registers 3 workflow
tools — `list_workflows`, `get_workflow_steps`, `search_workflows` — that are
NOT in `unified_mcp.py` and NOT in `TOOL_DEFAULTS`. When the tester connects
to a fastmcp server, these appear with no default and no hint. The hybrid
discover surfaces them immediately. (The unified server at the default
endpoint does not register them, so this is only visible when testing
fastmcp/cplus/android backends — exactly the scenario the tester exists for.)

This phase adds explicit reconciliation so the tester is self-healing
against server-side tool additions and removals.

## Requirements

- After `client.list_tools()`, compute three sets:
  - `live_tools` — tool names from the server.
  - `default_tools` — keys of `TOOL_DEFAULTS`.
  - `known = live_tools ∩ default_tools`.
- Classify each tool:
  - **Known** (in both): render in its `TOOL_CATEGORIES` bucket, payload
    from `get_default(tool)`.
  - **Server-only** (live, no default): render in the `Other` bucket with a
    `⚠ no default` hint after the name. Payload `{}`. Still callable.
  - **Stale** (default, not live): warn once at startup
    (`⚠ N default(s) have no matching server tool: …`). Hide from the
    run-all set (calling them would error). Keep visible in the interactive
    menu but mark `✗ offline` so the operator can inspect/edit the default
    without a wasted call.
- Print a one-line sync summary at startup, after the tool count:
  ```
  40 tools available.  (40 known · 0 server-only · 0 stale)
  ```
  When drift exists:
  ```
  42 tools available.  (40 known · 2 server-only · 0 stale)
  ⚠ No default payload for: new_tool_x, new_tool_y
  ```
- The reconciliation is **display-only**. It does not mutate
  `TOOL_DEFAULTS` or write files. The operator adds a default by hand when
  they want to silence the warning (one-line edit in `tool_defaults.py`).
- `_render_tool_list` already buckets by category and appends `Other` last;
  server-only tools land there naturally. Add the `⚠ no default` suffix in
  the render loop (check: `tool_name not in TOOL_DEFAULTS`).

## Implementation notes

- Add a helper `_reconcile_tools(live_tools, default_tools)` that returns a
  tuple `(known, server_only, stale)` as three sets. Call it once in
  `main()` after `list_tools()` and pass the classification into
  `interactive()` (or attach it to a small `SyncReport` dataclass).
- The `⚠ no default` check in the render loop is cheap: build a `set` of
  `TOOL_DEFAULTS` keys once at import time (`_DEFAULT_NAMES`) and test
  membership.
- Do not block startup on reconciliation — it is advisory. If `list_tools()`
  fails entirely, the existing fallback (build a minimal list from
  `TOOL_DEFAULTS`) stays in place.
- The run-all set (Phase 03) excludes `stale` tools automatically.

## Related Files

- `code-tiny/testtool/mcp_tester.py` (add `_reconcile_tools`, startup summary,
  render-loop hint, pass classification to `interactive`)
- `code-tiny/testtool/tool_defaults.py` (no change — consumed read-only)

## Todo

- [ ] Add `_reconcile_tools(live, defaults)` → `(known, server_only, stale)`.
- [ ] Print sync summary at startup with drift warnings.
- [ ] Render `⚠ no default` next to server-only tools in the `Other` bucket.
- [ ] Mark stale tools `✗ offline` in the interactive menu.
- [ ] Smoke-test: temporarily add a fake tool name to confirm the warning
      appears, then remove it.

## Success Criteria

- Startup prints a sync line showing known/server-only/stale counts.
- A server-only tool renders in `Other` with the `⚠ no default` hint and is
  callable with `{}`.
- A stale default renders with `✗ offline` and is excluded from run-all.
