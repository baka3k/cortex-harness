# Phase 03 — Test hardening and fan-out acceptance

**Goal:** update the 8 existing fan-out tests to the new contract and add
coverage for breadth, dedup, recall, and admission math.

## 3.1 Update existing tests (`tests/test_unified_mcp_input_coercion.py`)

| Test (line) | Change |
|---|---|
| `test_fanout_dispatch_runs_each_parser_and_merges_results` (272) | patches `parser_aliases` -> patch the new backend-representative resolver; assert 2 dispatches, `query_engine=="graph_fanout"`, tagging, `dedup_removed` present |
| `test_fanout_dispatch_does_not_trigger_when_parser_explicit` (337) | unchanged expectation; extend to all 13 tools parametrized |
| `test_fanout_dispatch_records_per_parser_errors` (372) | keep partial-failure semantics (decision 3) |
| `test_fanout_dispatch_returns_top_level_error_when_all_parsers_fail` (406) | unchanged |
| `test_resolve_fanout_parsers_uses_project_parser_when_registered` (433) | unchanged |
| `test_resolve_fanout_parsers_falls_back_to_all_when_project_unregistered` (447) | assert fallback == backend representatives (2), not aliases |
| `test_resolve_fanout_parsers_emits_error_when_no_parsers_registered` (463) | unchanged |
| `test_capability_summary_no_warning_when_parser_omitted` (250) | unchanged |

## 3.2 New tests

1. **Breadth bound:** one parser-less fan-out call issues exactly
   `len(BACKENDS)` backend invocations (mock backend modules, count calls).
   Regression name: `test_fanout_breadth_is_per_backend_not_per_alias`.
2. **Admission math:** with a mocked driver lane, a parser-less call
   enqueues at most 2 operations; snapshot shows `queued_items <= 2`. Guards
   the 88-vs-32 overflow class permanently.
3. **Dedup correctness:** parametrized over node keys — duplicate `id`
   across "parsers" collapses; `edges` dedup on
   `(start_id, type, end_id)`; `ids` string dedup; items without `id` are
   never dropped.
4. **Recall guard (D3):** fake registry with two cplus-backend profiles
   whose labels are disjoint from the legacy hardcoded list (e.g. `Class`,
   `Service`); parser-agnostic fan-out predicate must include both labels.
   Catches reintroduction of the legacy-fallback recall hole.
5. **Schema end-to-end:** all 13 fan-out tools accept `parser_type`
   (schema property present + dispatch skips fan-out when set) — covers
   Phase 01 at behavior level.
6. **Explicit-parser regression:** existing direct-payload tests
   (`tests/test_framework_mcp_search.py:27,44`) stay green — explicit
   `parser_type` results byte-identical.

## 3.3 Untouched guards to keep green

- `tests/test_framework_mcp_routing.py:71` (`len(aliases) ==
  len(parser_aliases())`) — alias registry unchanged.
- `tests/test_cobol_mcp_routing.py:17`, `tests/test_aspnet_integration.py:58`
  — alias membership unchanged.
- `tests/test_unified_mcp_wrapper_signatures.py:40` schema guard — extended
  in Phase 01, must stay green here.

## Verification

- `pytest tests/test_unified_mcp_input_coercion.py
  tests/test_unified_mcp_wrapper_signatures.py
  tests/test_framework_mcp_routing.py tests/test_framework_mcp_search.py -q`
- Full suite: `pytest tests/ -q` (or repo-standard runner).
