# Remove search_full — Simplify to project_id-only Scoping — 2026-07-28

## Context
The unified ingest/query contract plan (`plans/260728-0000-unified-ingest-query-contract/`)
introduced two parameters for project scoping on every MCP tool: `project_id`
(to scope to one project's shard) and `search_full=true` (to span every
project). In practice `$search_full` was set on the Cypher params dict by
`prepare_project_scope_parameters` but **no Cypher query template ever read
it** — the real filter was always `$project_id_normalized`, which queries
applied conditionally. The duplicate knob added noise to every signature
and forced `ProjectScopeRequiredError` to gate the wrong thing.

The plan `plans/260728-0900-simplify-search-full-removal/plan.md` made the
removal explicit: only `project_id` remains. Omitting `project_id` (or
passing an empty/blank value) is the implicit full-search path. No
backward-compat shim, no deprecated no-op.

## Change
**Phase 01 — core helpers (code-tiny):**
- `code-tiny/tools/common/project_scope.py:52-80` — `prepare_project_scope_parameters`
  no longer injects `search_full=False` on the returned dict; docstring
  rewritten.
- `code-tiny/tools/common/project_scope.py:83-101` — `qdrant_project_filter`
  signature reduced to `(project_id)`; returns `None` only when
  `project_id` is falsy.
- `code-tiny/tools/common/project_scope.py:104-122` — `matches_project_scope`
  signature reduced to `(candidate, project_id)`; the `search_full=True`
  early exit is gone.
- `code-tiny/tools/common/project_registry.py:64-84` — `ProjectScopeRequiredError`
  class deleted. `ProjectNotRegisteredError` stays.

**Phase 02 — unified_mcp.py:**
- `code-tiny/mcp/unified_mcp.py:57-63` — import of `ProjectScopeRequiredError`
  removed.
- `code-tiny/mcp/unified_mcp.py:1957-1987` — `_resolve_graph_database`
  rewritten to 2-way logic: `db` → `project_id` (registry, falls through to
  env on `ProjectNotRegisteredError`) → env defaults
  (`FALKORDB_GRAPH`/`NEO4J_DB`/`DEFAULT_GRAPH_DB`/`"hyper_graph"`). Never
  raises for missing `project_id`.
- `code-tiny/mcp/unified_mcp.py:659-683` — `tool_activate_project_removed`
  deprecation message rewritten (no `search_full` mention).
- 10 tool signatures cleaned: `tool_get_project_modules`,
  `tool_get_public_apis`, `tool_get_endpoints`,
  `tool_get_module_architecture_summary`, `tool_get_project_special_files`,
  `tool_get_framework_context`, `tool_find_callers_of_endpoint`,
  `tool_get_api_call_chain`, `tool_analyze_workflow_impact`,
  `tool_find_workflows_containing`. `_run_project_context_tool` no longer
  takes `search_full`.
- `code-tiny/mcp/unified_mcp.py:440` — stale "surface as
  ProjectScopeRequiredError" comment replaced with "env-default full-search
  path."

**Phase 03 — doc-tiny:**
- `doc-tiny/project_contract.py:7-12` — module docstring rewritten.
- `doc-tiny/project_contract.py:87-100` — `ProjectScopeRequiredError` class
  deleted.
- `doc-tiny/project_contract.py:215-228` — `qdrant_project_filter` reduced
  to single argument.
- `doc-tiny/mcp_graph_rag.py:13-18` — `ProjectScopeRequiredError` import
  removed; `qdrant_search_entity_payload` no longer takes `search_full` and
  resolves unregistered `project_id` to `QDRANT_COLLECTION` (graceful
  fallback) rather than raising.
- `doc-tiny/mcp_graph_rag.py:397-441` — `semantic_search` loses the
  `search_full` parameter and the
  `if collection is None and not project_id and not search_full: raise`
  guard.
- `doc-tiny/mcp_graph_rag.py:518-520` — `query_graph_rag_langextract`'s
  call to `qdrant_search_entity_payload` updated to the new signature.

**Phase 04 — tests, metadata, documentation:**
- `code-tiny/tools/common/test_project_scope.py` (new, replaces
  `test_project_scope_search_full.py`) — rewritten for the simplified
  contract: `QdrantFilterTests`, `MatchesProjectScopeTests`,
  `PrepareParametersTests` assert the new behavior. The old file is
  deleted.
- `code-tiny/tests/test_unified_ingest_query_contract.py:151-184` —
  `test_search_full_filter_suppresses_project_predicate` →
  `test_no_project_id_filter_is_none`;
  `test_prepare_project_scope_parameters_always_set_search_full` →
  `test_prepare_project_scope_parameters_drops_search_full`;
  `test_scoped_filter_predicate_pattern` docstring rewritten.
- `doc-tiny/tests/test_project_contract.py:150-160` — `test_search_full_returns_none`
  → `test_no_project_id_returns_none` (asserts `None` for `None`/`""`,
  non-`None` for `"cortext"`).
- `code-tiny/mcp/tool_metadata.py:32-38` — `activate_project_removed`
  description rewritten.
- `code-tiny/mcp/fastmcp_server.py:170-172` and the
  `tool_activate_project_removed` block at `:1260-1283` updated.
- Identical deprecation-notice rewrites applied to
  `code-tiny/mcp/cplus/cplus_mcp.py:1358-1378`,
  `code-tiny/mcp/android/android_mcp.py:1330-1352`,
  `code-tiny/mcp/java/java_mcp.py:1055-1082`.

## Impact
**Who/what is affected:** every project-scoped MCP tool in code-tiny (10
tools) and doc-tiny (`semantic_search`, `query_graph_rag_langextract`),
plus every helper that builds Qdrant filters or Cypher params. External
callers that pass `search_full=true` will receive "unknown parameter" —
identical to the hard removal of `activate_project`. Documented as
intentional in the plan's Risks section.

**Risk level:** medium (public MCP contract change). Mitigated by:
- No Cypher template ever read `$search_full`, so the Cypher plan cache is
  unaffected.
- The unified-registry resolution path is unchanged; only the precedence
  rule at the entry point is simpler.
- Existing tooling already had to pass `project_id` to be useful — the
  escape hatch (`search_full=true`) was never the canonical path.

**Test results:**
- `code-tiny.tools.common.test_project_scope` — 8/8 pass
- `code-tiny.tools.common.test_project_registry` — 20/20 pass
- `code-tiny.tests.test_unified_ingest_query_contract` — 7/7 pass
- `doc-tiny.tests.test_project_contract` — 8/8 pass
- `tests.test_unified_mcp_input_coercion` — 24/29 pass; **5 errors are
  pre-existing** (the test file references `unified_mcp.active_project`,
  an attribute already removed by `243d42d` and `775c8eb` on `develop`
  before this plan).

**Final grep:** `grep -rn "search_full" code-tiny/ doc-tiny/` returns 0
production-code matches (8 remaining matches are inside test docstrings/
assertions that intentionally verify the field is absent).
`grep -rn "ProjectScopeRequiredError" code-tiny/ doc-tiny/` returns 0.

## Decision
Hard removal rather than a transitional deprecation window. Justification
(mirroring the `activate_project` precedent):
- `$search_full` was dead code in the Cypher layer — never read by any
  query template. The dual-parameter shape was a vestigial scaffolding
  from when the plan anticipated needing it.
- A shim would keep the dead parameter alive in every signature,
  preserving the maintenance burden this plan removes.
- The 2-way resolution (`db` → `project_id` → env default) matches how
  callers actually used the system: pass `db` to override, pass
  `project_id` to scope, omit both to query everything.

**Alternatives considered:**
- *Deprecation window with a `search_full` shim.* Rejected — the parameter
  is dead weight, not a backward-compat gap.
- *Soft rename to `cross_project`.* Rejected — adds a synonym for "absent
  project_id" and obscures the real contract.

## References
- plan: `./plans/260728-0900-simplify-search-full-removal/plan.md`
- parent plan: `./plans/260728-0000-unified-ingest-query-contract/plan.md`
- predecessor (case-insensitive project_id):
  `./plans/260723-0908-case-insensitive-project-id/`
- commit: `9162c02` — Remove `search_full` parameter from project-scoped
  MCP tools and simplify project resolution logic
- prior: `243d42d` — tests for project-context behaviour (referenced the
  already-removed `active_project` attribute; pre-existing failures
  unrelated to this plan)