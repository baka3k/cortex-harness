# Phase 03: Categorized Selection UX

## Context

`_render_tool_list` prints a single flat numbered list and the `/text` filter
matches tool names only. At ~40 tools this is hard to scan and there is no way
to jump to "just the planning tools" or "just project-context tools". This phase
adds category grouping and a description-aware filter while preserving every
existing keybinding.

## Requirements

- Render the tool list grouped by category, using `TOOL_CATEGORIES` from
  Phase 01. Tools absent from the map fall into an `Other` group so future tools
  render without code changes.
- Keep flat-number selection working: numbers are assigned in display order
  (category by category). Selecting `N` still picks the Nth visible tool.
- Preserve existing inputs in the main prompt:
  - `<number>` select tool
  - `/text` filter (now matches name **and** first-line description)
  - `q`/`quit`/`exit` exit
  - empty input clears the filter
- Add category shortcuts:
  - `c` lists categories with a one-line summary (`Session & Discovery (6)`,
    `Planning & Dependency (5)`, …) and lets the user pick a category to scope
    the tool view.
  - Selecting a category narrows the list to that category; `c` again or
    `*` returns to "all categories".
- Filter and category combine: when both are active, the filter applies within
    the selected category.
- Improve the filter:
  - Match case-insensitively against `tool["name"]` and the first line of
    `tool["description"]`.
  - Show match count and active scope in the footer line (replaces the current
    `shown/total` line).
- No new dependencies; stay within the stdlib + existing `httpx`/`readline`.
- Keep the jump-to-tool `--tool` CLI flag and `start_tool` path unchanged.

## Proposed rendering

```
═══════════════════════════════════════════════════════════
  MCP Tool Tester   scope: all   filter: ""   (40 tools)
═══════════════════════════════════════════════════════════

  ▸ Session & Discovery (6)
    1. activate_project      Set default parser_type and database ...
    2. inspect_parser_capabilities  Compare a parser profile's support ...
    ...

  ▸ Search (6)
    7. explore_graph         Intent-aware multi-strategy graph search ...
    ...

  ▸ Planning & Dependency (5)
   ...

═══════════════════════════════════════════════════════════
  40 tools | /filter  c category  <number> select  q quit
═══════════════════════════════════════════════════════════
```

When a category is selected the header reads `scope: Planning & Dependency` and
only that block is shown. Numbers re-index within the visible set.

## Implementation notes

- Refactor `_render_tool_list` to:
  1. Compute the visible subset (category scope × filter).
  2. Bucket the subset by category (preserve `TOOL_CATEGORIES` order; append
     `Other` last).
  3. Print each bucket with a `▸ {Category} (n)` header and renumbered rows.
  4. Return the visible list so the caller's existing index/`int(raw)` logic
     still works unchanged.
- Add `_CATEGORY_OF: Dict[str, str]` (inverse of `TOOL_CATEGORIES`) built once
  at import time so category lookup is O(1).
- Move the filter to a helper `_matches(tool, text)` that checks name + first
  description line, replacing the inline `filter_text.lower() in name` check.
- The category menu (`c`) is a nested prompt: print categories, accept a number
  or name, set the scope, then re-render. `*` or empty resets scope to all.
- Keep `_USE_COLOUR` behaviour; category headers use `BOLD`, counts use `DIM`.

## Backward compatibility

- A user who ignores categories sees the same tools, just grouped. Number
  selection still works (indices shift to grouped order, but the README never
  promised stable indices).
- `/text` still filters; it now also matches descriptions, which is strictly
  more permissive — no existing workflow breaks.
- `--tool`, `--endpoint`, `--timeout`, the payload editor loop, and result
  saving are untouched.

## Related Files

- `code-tiny/testtool/mcp_tester.py` (edit `_render_tool_list`, `interactive`)
- `code-tiny/testtool/tool_defaults.py` (consumes `TOOL_CATEGORIES`)
- `code-tiny/testtool/README.md` (update the "Menu workflow" section)

## Todo

- [ ] Add `TOOL_CATEGORIES`/`_CATEGORY_OF` plumbing (depends on Phase 01).
- [ ] Rewrite `_render_tool_list` for grouped rendering + description-aware filter.
- [ ] Add the `c` category-browse prompt and scope state in `interactive`.
- [ ] Update README "Menu workflow" to document `c` and the grouped layout.
- [ ] Smoke-test with a live server: filter by `/api`, scope to `Project
      Context`, select by number, run one tool end-to-end.

## Risks

- Re-indexing numbers per render could surprise a user who memorized an index.
  Mitigation: numbers were never stable across filter states already, and the
  README tells users to select by number or name; name selection still works.
- Adding a `c` command could collide with a future tool named `c`. Mitigation:
  tool selection by bare name already requires the name to exist in the visible
  list; `c` is interpreted as the category command only when it is the entire
  input and no visible tool is named `c` (none today). Documented in code.

## Success Criteria

- With ~40 tools loaded, the default view shows grouped buckets and a footer
  listing the `/` and `c` commands.
- `/login` surfaces `explore_graph` and `semantic_search` (description match)
  even though neither name contains "login".
- Selecting category `Planning & Dependency` then `/plan` shows the three
  `plan_*` tools and nothing else.
