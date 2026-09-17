# Phase 05 — Acceptance, cross-plan sync, and rollout

**Goal:** end-to-end verification against a live server, cross-plan notes,
and session log.

## 5.1 Live acceptance (unified MCP against this repo's graph)

1. Restart unified MCP (`dev mcp start --force-restart` or repo standard).
2. `list_mcp_functions`: all 13 fan-out tools list `parser_type`.
3. Parser-less `search_functions(query='parser')`:
   - `parsers_searched == ["android", "cplus"]` (or the single registered
     project parser when `project_id` given);
   - no `OVERLOADED` in `parser_errors`;
   - zero duplicate `id`s in `results`; `dedup_removed >= 0` present;
   - wall time ~ single query, not N-serial.
4. Recall spot-check: query a symbol whose label is outside the legacy
   fallback list (e.g. a `Class` or `Service` node from a python/spring
   ingest) — must appear in parser-less fan-out results (D3 guard, live).
5. Explicit `parser_type='python'` call: identical shape to pre-change.
6. Unregistered `project_id='typo-test'`: still fans out (decision 3 —
   keep current behavior), now at engine breadth.

## 5.2 Cross-plan sync (bidirectional, already noted in plan.md)

- `plans/260807-0929-mcp-ingest-query-concurrency/plan.md`: add note —
  fan-out admission pressure reduced 88 -> <=2 per parser-less call by this
  plan; lane policy ownership unchanged; no phase conflicts (its phases
  touch gateway/ingest, not unified_mcp dispatch).
- This plan's `relatedPlans` already references it.

## 5.3 Session log

- Append implementation log per repo convention (`docs/logs/` or
  `plans/reports/` as used by sibling plans).

## 5.4 Rollout

- Single commit series; no data migration, no config change, no restart
  coordination beyond MCP process restart.
- Feature is backward compatible: new optional param, narrower fan-out,
  deduped merges. Consumers observed today: skill guidance (Phase 04
  updated in same series).

## Exit criteria

- All Phase 03 + 05 checks green; full `pytest tests/ -q` green.
- plan.md `status: complete` + `reviewed` date set.
