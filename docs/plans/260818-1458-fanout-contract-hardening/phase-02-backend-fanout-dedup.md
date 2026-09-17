# Phase 02 — Backend-level fan-out with node-id dedup

**Goal:** a parser-less fan-out call dispatches once per physical backend
(2 today) with a parser-agnostic query, and merges without duplicates.

## 2.1 Backend-level dispatch (red-team corrected)

**Do NOT dispatch with `parser_type=<representative>`** — that keeps
`capability_for_parser()` non-None inside the backend, so the
representative profile's labels apply and the union predicate below never
triggers (recall hole survives). Instead:

- Restructure `_fanout_dispatch` (`unified_mcp.py:1035-1121`) to iterate
  **`BACKENDS` directly** (sorted by name → `["android", "cplus"]`),
  calling `backend.module.tool_{name}(payload=...)` with `parser_type`
  **stripped** from the per-engine payload and `_fanout: True` kept
  (already injected at `unified_mcp.py:1051`).
- `_resolve_fanout_parsers` (`unified_mcp.py:896-934`) shrinks to the
  project-registered case only: registered `project_id` -> single-parser
  dispatch via the existing non-fanout path (backend resolved by
  `_resolve_backend_name`); unregistered/omitted -> return the backend
  iteration marker. Keep the `no_parsers_registered`-style error envelope
  for the empty-`BACKENDS` case (rename type to
  `no_query_engines_registered` only if tests/consumers are updated in the
  same phase — otherwise keep the existing type string).
- Provenance tagging: `_tag_fanout_items` receives the backend's canonical
  representative name (`CAPABILITIES[backend].name`, i.e. `android` /
  `cplus`) so merged items still carry a `parser_type` tag.
- `parsers_searched` lists backend representatives (e.g.
  `["android", "cplus"]`); docstring updated: fan-out breadth is per query
  engine, not per alias.
- `PARSER_ALIASES_ANDROID`/`PARSER_ALIASES_CPLUS` (`unified_mcp.py:265-266`)
  remain unused — do not delete in this phase.

## 2.2 Parser-agnostic predicate (correctness-critical)

**Hazard (verified):** when `capability is None`, `tool_search_functions`
falls back to a hardcoded legacy label list
(`cplus_mcp.py:2700-2712`) covering only
`Function/Type/Namespace/File/Field/Alias/Template/FunctionType/Event/
Project/Resource/UIControl` + 3 framework values. It misses `Class`,
`Method`, `Interface`, `Package`, `Module`, `Enum`, `Procedure`, `Table`,
`Service`, `Controller`, `Route`, and all other framework labels used by 26
cplus-backend profiles. A backend-level parser-less query using this
fallback would silently drop most non-C++ nodes — a recall regression vs.
today's duplicated-but-complete fan-out.

Fix:

1. Add to `framework_registry.py`:
   `backend_label_union(backend) -> Tuple[str, ...]` and
   `backend_property_union(backend) -> Tuple[str, ...]` returning the
   deduped union of `labels` / `searchable_properties` across all
   `CAPABILITIES` entries with that backend.
2. In backend tools that build label predicates when `capability is None`
   and `payload.get("_fanout")` is truthy, use the union instead of the
   hardcoded list. Apply the same substitution for the fulltext
   `node_profile_predicate` path (`cplus_mcp.py:2723-2728`) — union
   predicate OR'd with legacy is fine; union alone is preferred.
3. Mirror in `android_mcp.py` where the same pattern exists (search tools
   around 2663-2800).
4. Keep non-fan-out parser-less direct calls (no `_fanout` flag) on the
   legacy predicate — zero behavior change outside fan-out.

## 2.2.1 Limit semantics under fan-out

`limit` remains **per engine**: a parser-less `search_functions(limit=50)`
may return up to 100 pre-dedup items (<=50 per engine), deduped after
merge. Document in the Phase 04 contract text; do not silently re-slice
merged lists (would break per-engine ordering guarantees callers may rely
on).

## 2.3 Dedup in `_merge_fanout_results`

`unified_mcp.py:958-1032`, after concatenation per list key:

- Node-shaped keys — `results`, `nodes`, `symbols`, `classes`,
  `functions`: dedup key = `item["id"]` (all produced by `_record_node`
  with shape `{"id", "labels", "properties"}`; missing `id` -> keep item,
  never drop).
- `ids`: dedup key = the string itself.
- `edges`: dedup key = `(start_id, type, end_id)` composite; conflicting
  `properties` -> first-seen wins.
- `paths`, `endpoint_paths`, `workflows`, `matches`: dead keys for this
  tool set (verified no backend returns them) — leave as-is.
- First-seen item wins (deterministic: iterate engines in sorted order, as
  today). The `parser_type` tag on the kept item is the first engine that
  produced it; no tag merging required.
- Add `dedup` diagnostics to the merged payload, e.g.
  `{"dedup_removed": <int>}` — cheap, aids acceptance asserts.

## 2.4 Instruction/comment truth

- `unified_mcp.py:111-115` comment and INSTRUCTIONS (210-236): describe
  omit-parser_type = fan-out across **query engines** (2), dedup by node id,
  per-engine `limit`.
- `_capability_summary` docstring (349-359): unchanged semantics.

## Verification

- Unit: fan-out with mocked backends returns `parsers_searched ==
  ["android", "cplus"]`, 2 dispatches total, and per-engine payloads
  contain NO `parser_type` key (guards the R1 regression: representative
  parser must not leak into fan-out payloads).
- Unit: injected duplicate ids across engines collapse; count in
  `dedup_removed`.
- Manual live: `search_functions` without parser_type on the harness repo
  itself — no `OVERLOADED` in `parser_errors`, latency ~1 query, no
  duplicate ids in `results`.

## Out of scope

- Admission lane limits (`260807-0929` owns).
- Unregistered-project fallback and partial-failure tolerance (decision 3:
  keep current behavior).
