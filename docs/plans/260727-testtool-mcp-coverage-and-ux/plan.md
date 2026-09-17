---
title: "Testtool: Cover New MCP Tools + Categorized Selection UX"
status: implemented
created: 2026-07-27
mode: hi-plan --fast
scope: code-tiny/testtool default payloads, input_exam data, interactive menu UX
blockedBy: []
supersededBy:
  - 260728-1500-testtool-runall-error-tracing
relatedPlans:
  - 260719-2150-parser-mcp-runtime-alignment
  - 260715-2200-mcp-capability-routing
reviewed: 2026-07-28
---

> **SUPERSEDED (2026-07-28):** Phases 01–04 of this plan (19 new defaults,
> categorized UX, stale cleanup, coverage check) are implemented in the
> working tree. Plan `260728-1500-testtool-runall-error-tracing` supersedes
> it: it fixes the one stale artifact this plan's Phase 04 missed
> (`activate_project.json` filename), adds run-all batch mode, error/exception
> tracing, and hybrid live-discover sync. This plan is marked `implemented`;
> all new testtool work continues under the superseding plan.

# Testtool: Cover New MCP Tools + Categorized Selection UX

## Overview

The interactive MCP tester (`code-tiny/testtool`) has fallen behind the unified
MCP server. It ships default payloads and example data for 22 tools, but the
server now registers ~40. Nineteen newer tools — planning, project-context,
fullstack/workflow, parser-capability, and graph-exploration tools — have no
defaults and no example data, so testers must hand-write payloads each run.

At the same time the flat numbered tool list no longer scales: with ~40 entries
it is hard to scan, the `/text` filter matches tool names only (not
descriptions), and there is no grouping by domain.

This plan closes the coverage gap and redesigns tool selection to be category
driven with a description-aware filter.

## Verified Findings

- `testtool/tool_defaults.py` defines `TOOL_DEFAULTS` for 22 entries. One of
  them, `get_id_by_name`, is **not** a registered MCP tool — it is a stale
  leftover (`search_functions` already returns IDs). Confirmed by grep across
  `code-tiny/mcp`: no `get_id_by_name` registration exists.
- `testtool/input_exam/` holds 22 JSON files, including `test_find_path.json`,
  which is not a tool name — a leftover scratch file.
- `mcp/unified_mcp.py` registers 40 tools. Diff against `TOOL_DEFAULTS` leaves
  **19 tools without defaults or example data** (listed in Phase 01).
- `mcp/tool_metadata.py` is the authoritative catalog (`_FULL_CATALOG`) with
  descriptions, `use_cases`, and per-input metadata for every tool — a reliable
  source for both default payloads and category derivation.
- `mcp_tester.py::_render_tool_list` renders a single flat numbered list and
  filters by `t["name"]` substring only. No category concept, no description
  matching, no recent/favorites.
- No existing plan overlaps. `260719-2150-parser-mcp-runtime-alignment`
  (completed) added `inspect_parser_capabilities` to the server but did not
  extend the tester.

## Phases

1. [Phase 01 — Defaults & categorization](phase-01-new-tool-defaults.md)
2. [Phase 02 — Test data files](phase-02-input-exam-data.md)
3. [Phase 03 — Categorized selection UX](phase-03-categorized-menu-ux.md)
4. [Phase 04 — Stale cleanup & verification](phase-04-cleanup-and-verify.md)

## Contract Decisions

- `tool_defaults.py` is the single in-code source of default payloads; the
  file-based override in `input/` (via `get_default`) remains the higher-priority
  source. We add defaults for all 19 new tools and keep the priority chain
  intact.
- A new `TOOL_CATEGORIES` map (category → list of tool names) lives in
  `tool_defaults.py` next to `TOOL_DEFAULTS`. Categories are derived from the
  tool domains already documented in `tool_metadata.py`.
- The interactive menu preserves all existing keybindings (number, `/filter`,
  `q`) and adds category browsing on top. A tool not present in
  `TOOL_CATEGORIES` falls back to an `Other` bucket so future tools render
  without code changes.
- Stale entries (`get_id_by_name`, `test_find_path.json`) are removed, not kept
  as "compatibility" shims — they reference no real tool.

## Dependencies

- None blocking. Reads the current `unified_mcp.py` tool surface; does not
  modify the MCP server.
- Mirrors input shapes documented in `mcp/tool_metadata.py::_FULL_CATALOG` so
  defaults stay accurate if the catalog changes (re-sync is a manual follow-up).

## Out of Scope

- No changes to `mcp_client.py` protocol handling.
- No new MCP tools, no server-side behavior changes.
- No automated pytest suite for the tester itself (the tester is a manual
  helper; `tests/mcp/**` covers server logic). Verification is a smoke run
  against a live server in Phase 04.
- No CSV batch-run or HTTP-retry features (mentioned as future ideas in the
  README).

## Risks

- Default payloads for project-context tools (`get_project_modules`, etc.)
  require a real `project_id`. Defaults use a placeholder (`"YOUR_PROJECT_ID"`)
  so callers must edit before running — same convention already used for
  `node_id` placeholders. Documented in each phase.
- Category assignment is judgment-based; a tool could plausibly sit in two
  buckets. We assign each tool to exactly one primary category to keep the menu
  unambiguous; the filter remains the escape hatch.
