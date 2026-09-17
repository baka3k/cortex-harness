---
title: "MCP Fan-Out Contract Hardening: parser_type exposure, backend-level fan-out, node-id dedup"
status: complete
created: 2026-08-18
mode: hi-plan --full
scope: unified_mcp fan-out dispatch contract, backend tool schemas, tool catalog, consumer-side guardrails
priority: P0
blockedBy: []
blocks: []
relatedPlans:
  - 260807-0929-mcp-ingest-query-concurrency
  - 260719-0100-mcp-query-capability-hardening
  - 260719-2150-parser-mcp-runtime-alignment
  - 260728-0000-unified-ingest-query-contract
reviewed: 2026-08-18
---

# MCP Fan-Out Contract Hardening

## Problem (verified against code)

A parser-less call to any of the 13 `_FANOUT_SEARCH_TOOLS` fans out across
**all 88 parser aliases** (27 canonical profiles, 2 physical backends), which:

1. **Cannot be avoided by clients** — 8 of the 13 tools do not expose
   `parser_type` in the public schema derived from the cplus backend
   signatures (`_register_proxy_tools`, `unified_mcp.py:834-860`), so
   schema-validated callers can never scope the call. The other 5
   (`query_subgraph`, `find_paths`, `find_path_between_module`,
   `trace_flow`, `trace_flow_between_module`) already expose it.
2. **Guarantee admission-queue overflow** — one call issues 88 concurrent
   lane admissions against `BoundedLane("falkordb-query",
   LaneLimits(concurrency=1, max_queue_items=32))`
   (`falkordb_driver.py:239-241`), so ~56+ dispatches are rejected
   `OVERLOADED` per call; survivors run serially (latency = N x query).
3. **Duplicate results** — `_merge_fanout_results`
   (`unified_mcp.py:958-1032`) concatenates without dedup; the same node is
   returned up to 85x (once per cplus-backend alias), each tagged with a
   different `parser_type`.
4. **Catalog lies** — `tool_metadata.py` documents `parser_type` for none of
   the 13 fan-out tools (`list_mcp_functions` output), so agents cannot
   discover the scoping parameter.

### Review of the original assessment

- "Fan-out sinh kết quả trùng và làm đầy admission queue" — confirmed, with
  exact numbers above (88 aliases vs 32 queue slots).
- "Fast-fail nếu `parsers_failed` xuất hiện" — **rejected as a hard rule**:
  under the current 88-way fan-out `parsers_failed` is always populated
  (OVERLOADED + `tool_not_in_backend`); hard fast-fail would fail every
  parser-less call. Downgraded to an advisory consumer-side signal.
- "Chỉ tin kết quả theo canonical parser" — superseded by the
  backend-level fan-out decision (canonical-profile fan-out would still
  issue 27 queries and duplicate label-overlapping nodes).

## Scope challenge decisions (2026-08-18)

1. **Fix locus: server + skill guardrails.** Root cause is fixed server-side;
   consumer-side guardrails cover the transition and any residual risk.
2. **Fan-out granularity: backend-level with node-id dedup.** Parser-less
   calls dispatch once per physical backend (2 today: `cplus` -> graph_generic,
   `android` -> android_graph) using a parser-agnostic query. "Omit
   parser_type = search all" semantics preserved at ~2 queries, not 88.
3. **Failure semantics: keep current behavior.** Unregistered `project_id`
   still falls back to fan-out (no `project_not_registered` hard error);
   partial per-parser failures remain quiet (`ok=true` with `parsers_failed`
   listed). Documented as accepted behavior, not changed here.

## Design requirements

- **D1 — Schema truth.** All 13 fan-out tools expose optional `parser_type`
  in the public schema (backend signatures) and in `tool_metadata.py`.
- **D2 — Backend-level fan-out.** `_resolve_fanout_parsers` returns one
  representative canonical parser per distinct backend
  (`CAPABILITIES` entries whose key == backend name: `cplus`, `android`),
  not `sorted(parser_aliases())`.
- **D3 — Parser-agnostic correctness.** When fan-out dispatches a backend
  without a meaningful profile, label/property predicates must use the
  **union of all profile labels/properties mapped to that backend** — NOT
  the legacy hardcoded fallback in `tool_search_functions`
  (`cplus_mcp.py:2700-2712`), which misses `Class`, `Method`, `Interface`,
  `Package`, `Module`, `Enum`, and all framework labels. Verified: a naive
  parser-less backend query would silently drop most non-C++ nodes. The
  existing `_fanout` payload flag (`unified_mcp.py:1051`) is the signal for
  backends to select the union predicate.
- **D4 — Dedup by node identity.** Merged list keys dedup on: node-shaped
  keys (`results`, `ids`, `nodes`, `symbols`, `classes`, `functions`) ->
  `id` (or the string itself for `ids`); `edges` -> composite
  `(start_id, type, end_id)`. `paths`, `endpoint_paths`, `workflows`,
  `matches` are dead keys for the current fan-out set (verified: no backend
  returns them for these 13 tools) — leave unmerged behavior unchanged.
  First-seen item wins; provenance accumulates as
  `parser_type` (first) — do not lose the tag.
- **D5 — No semantic changes to failure handling** (decision 3).
- **D6 — Backwards compatibility.** Explicit `parser_type` calls are
  unchanged. `parsers_searched` now lists backend representatives
  (e.g. `["android", "cplus"]`); consumers relying on alias lists must be
  identified (tests only, per research).

## Non-goals

- No change to admission lane limits or policy — owned by
  `260807-0929-mcp-ingest-query-concurrency`.
- No change to unregistered-project fallback or partial-failure tolerance.
- No standalone `fastmcp_server.py --mode fast` mirror of the new param
  (deferred; separate deployment path, see Phase 04 backlog note).
- No new MCP server, no parser registry restructuring.

## Phases

1. [Phase 01 — Expose parser_type on the 8 unscoped tools](phase-01-parser-type-schema-exposure.md)
2. [Phase 02 — Backend-level fan-out with node-id dedup](phase-02-backend-fanout-dedup.md)
3. [Phase 03 — Test hardening and fan-out acceptance](phase-03-test-hardening.md)
4. [Phase 04 — Consumer guardrails and documentation](phase-04-consumer-guardrails-docs.md)
5. [Phase 05 — Acceptance, cross-plan sync, and rollout](phase-05-acceptance-rollout.md)

## Key files

| File | Change |
|---|---|
| `code-tiny/mcp/cplus/cplus_mcp.py` | +`parser_type` on 8 signatures (1696, 1743, 1803, 2169, 2232, 2294, 2648, 2786) + `_merge_payload`; union label predicate for fan-out mode |
| `code-tiny/mcp/android/android_mcp.py` | same 8 (1618, 1665, 1724, 2210, 2271, 2333, 2663, 2737) |
| `code-tiny/mcp/unified_mcp.py` | `_resolve_fanout_parsers` 896-934; `_merge_fanout_results` 958-1032; `_fanout_dispatch` 1035-1121; INSTRUCTIONS 210-236; comments 111-115 |
| `code-tiny/mcp/tool_metadata.py` | +`parser_type` input on 13 catalog entries |
| `code-tiny/mcp/framework_registry.py` | per-backend label/property union helper |
| `tests/test_unified_mcp_input_coercion.py` | fan-out tests 250-470 |
| `tests/test_unified_mcp_wrapper_signatures.py` | extend schema guard (line 40) |
| Skill refs (consumer) | `hi-repository-search/references/code_graph.md`, `hi-reverse/references/MCP-TOOLS.md`, retrieval protocols |

## Acceptance summary

- A parser-less `search_functions` call produces at most 2 backend
  dispatches and at most 2 lane admissions (assert via lane snapshot /
  mocked driver).
- Merged fan-out results contain no duplicate `id` / composite edge key.
- All 13 fan-out tools list `parser_type` in `list_mcp_functions` output and
  accept it end-to-end (schema -> dispatch -> backend).
- Explicit-`parser_type` behavior byte-identical to pre-change (regression
  tests).
- Full MCP test suite green.

## Notes

- `docs/development-rules.md` does not exist in this repo (checked
  2026-08-18); nothing to comply with beyond existing plan conventions.
- Cross-plan note added to `260807-0929-mcp-ingest-query-concurrency`
  (bidirectional): this plan reduces per-call admission pressure from 88 to
  <=2; lane policy ownership unchanged.
