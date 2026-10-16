# Research digest: optional `repo` scope parameter for code-side MCP query tools

- **Created:** 2026-10-16
- **Scope:** code-tiny MCP layer (`code-tiny/mcp/`, `code-tiny/tools/`), NOT doc-tiny
- **Method:** direct code reading + greps + live LadybugDB Cypher probe (embedded `.venv` ladybug 0.20.4)
- **Question folded in from coordinator (item 9):** `repo` in Qdrant payloads; `ENDS WITH` support evidence.

---

## Findings

### 1. Entry points and tool inventory

**Entry points**

- Production server is the unified server: `cortex_harness/dev.py:199` launches `mcp/unified_mcp.py`. Server object at `code-tiny/mcp/unified_mcp.py:406-411` (`FastMCP(name=MCP_NAME, ...)`).
- `unified_mcp.py` loads three backend modules as callables at `unified_mcp.py:63-65`: `android/android_mcp.py`, `cplus/cplus_mcp.py`, and `fastmcp_server.py` (as `fast_backend`). Backends are registered in `BACKENDS` at `unified_mcp.py:447-450` (android, cplus only; fast_backend is used for planner dispatch).
- `fastmcp_server.py` is also a standalone server (dev launcher `code-tiny/run_mcp.sh:91`) and carries its own tool set; `java/java_mcp.py` is a dev-only launcher variant (`run_mcp.sh:94`) and is NOT in `BACKENDS` — but it duplicates the same Cypher predicates, so any pattern change has a fourth copy there.
- Proxy registration: `_PROXIED_TOOL_NAMES` (`unified_mcp.py:918-947`, 20 tools) + `_PLANNER_TOOL_NAMES` (`:904-912`, 5 tools). Proxied tools are dynamically registered from the backend callable's signature via `Tool.from_function` (`unified_mcp.py:1048-1076`), routed by `_ProxyMiddleware` → `_dispatch_tool` (`:986-1010`, `:1442-1527`).
- `_UNIFIED_TOOL_NAMES` catalog of 39 tools: `unified_mcp.py:100-142`. Fan-out set `_FANOUT_SEARCH_TOOLS` of 13: `unified_mcp.py:152-168`.

**Classification of tools on the unified surface**

- **(a) Graph Cypher query tools (~28):**
  - 13 fan-out search tools (`unified_mcp.py:152-168`): search_functions, search_by_code, get_symbol, get_node_details, query_subgraph, find_paths, find_path_between_module, listup_symbols_matching_file_path, listup_class_matching_path, list_up_entrypoint, trace_flow, trace_flow_between_module, list_possible_calls.
  - Plus get_ipc_message, find_screen_workflows (proxied, `unified_mcp.py:918-947`).
  - 5 planner tools (compute_scc, topological_sort, plan_dependency_order, plan_file_dependency_order, plan_function_dependency_order) — Cypher via `_build_prerequisite_graph` (`fastmcp_server.py:867`), dispatched through `_dispatch_planner_tool` (`unified_mcp.py:1079-1113`).
  - Unified direct Cypher tools: reconstruct_flow (`:1826`), find_callers_of_endpoint (`:2409`), get_api_call_chain (`:2530`), analyze_workflow_impact (`:2759`), find_workflows_containing (`:2950`).
  - fastmcp-only (not on unified): list_workflows (`fastmcp_server.py:2485`), get_workflow_steps (`:2536`), search_workflows (`:2589`) — these also carry inline `project_id_normalized` predicates (`fastmcp_server.py:2507,2604`).
- **(b) Vector/Qdrant tools (2, +1 hybrid):** semantic_search (cplus backend `cplus_mcp.py:1617`; fastmcp copy `fastmcp_server.py:1199`) and explore_graph (hybrid semantic+graph, `unified_mcp.py:1688` → `services/explore_service.py`). list_qdrant_collections is metadata.
- **(c) Metadata/contract tools (5):** list_databases (`fastmcp_server.py:1125`), list_qdrant_collections (`:1382`), list_parsers (`unified_mcp.py:1539`), inspect_parser_capabilities (`:1581`), list_mcp_functions (`:1535`).
- **Write tool (1):** annotate_node (`fastmcp_server.py:2437`) — mutation, not a scope candidate.
- **Project-context tools (6, Cypher but special):** get_project_modules, get_public_apis, get_endpoints, get_module_architecture_summary, get_project_special_files, get_framework_context (`unified_mcp.py:2203-2370`) — all read through `services/project_context_service.py`, whose queries filter `project_id_normalized` only (`project_context_service.py:74-79,94,133,144`).

### 2. Shared scope-predicate pattern and choke points

- **One shared helper module exists for params, not for WHERE clauses:** `code-tiny/tools/common/project_scope.py`. Key functions:
  - `prepare_project_scope_parameters(query, parameters)` (`project_scope.py:107-132`) — injects `<key>_normalized` sibling params for every recognized project-scope param key (`_PROJECT_SCOPE_PARAMETER_KEYS` `:21-30`). A blank/omitted `project_id` simply does not get a normalized key, which makes the inline Cypher guard `($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)` a no-op — this is the exact mechanism a `repo` param would mirror.
  - `qdrant_project_filter(project_id)` (`:135-159`) — canonical Qdrant payload filter (`match any` on `project_id_normalized`), suppressed (None) when unscoped.
  - `matches_project_scope(candidate, project_id)` (`:162-180`) — Python post-filter for payloads not filtered server-side.
  - Contract docstring (`:1-13`): omitted → unscoped; case-insensitive; prefix/LIKE semantics on read paths only.
- **WHERE-clause predicates are per-tool duplication, ~94 occurrences** of `STARTS WITH $project_id_normalized` (grep -cF): cplus_mcp.py 16, android_mcp.py 27, java_mcp.py 23, fastmcp_server.py 13, semantic_graph_expansion.py 4, services/project_context_service.py 11. Representative inline predicate: `cplus_mcp.py:3233` and `fastmcp_server.py:2320` — `AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)`.
- **Param-injection choke points (where `$repo` could be added mechanically):**
  - Backend `_run_cypher_first` wrappers: `cplus_mcp.py:1281-1282`, `android_mcp.py:1005`, `java_mcp.py:824`, and `_run_cypher` in `fastmcp_server.py:1047-1054` — all call `prepare_project_scope_parameters(query, params)`.
  - Graph drivers (second, idempotent layer): `tools/graph/driver/neo4j_driver.py:222`, `falkordb_driver.py:119`, `ladybug_driver.py:868`.
  - Other direct callers: `mcp/semantic_graph_expansion.py:197,271`, `tools/common/graph_expander.py:413,578`, `tools/common/intelligent_retrieval.py:321`, `tools/ts/workflow_finder.py:104`.
- **Inferred (marked):** there is NO single WHERE-clause builder; every query hand-writes the predicate. So a `repo` predicate must either be hand-added to each query (same duplication cost as project_id) or the helper `prepare_project_scope_parameters` must be extended to also normalize `repo`, with predicates edited per query. The helper extension alone changes nothing without the Cypher edits.

### 3. Tool parameter conventions

- **Typed Python signature is the contract.** Representative example — `search_functions` on the cplus backend (`cplus_mcp.py:3150-3166`): every param annotated (`Optional[str]`, `int`, `bool`, `List[str]`), plus a `payload: Optional[Dict[str, Any]]` escape hatch merged over typed values via `_merge_payload` (`cplus_mcp.py:3167-3181`; helper at `unified_mcp.py:472-475`). The fastmcp twin has the same shape without the escape hatch (`fastmcp_server.py:2290-2300`).
- Coercion helpers live in `unified_mcp.py`: `_coerce_payload` `:464-469`, `_merge_payload` `:472-475`, `_normalize_string_list` `:478-493`, `_coerce_list_fields` `:496-522` (alias pairs like module→modules), `_apply_unified_defaults` `:687-702` (documents `db` as alias of `project_id` — precedent for alias handling). There is **no `_coerce_bool` in code-tiny** — that is the doc-side pattern (`doc-tiny mcp_graph_rag.py:116-128` per sibling plan); code side uses real `bool` annotations.
- JSON schema is auto-derived from the backend signature by FastMCP `Tool.from_function` (`unified_mcp.py:1048-1076`); catalog `required` flags are mirrored onto the wire schema by `_apply_catalog_required_to_schema` (`:1016-1045`), and FastMCP validates arguments against that schema before `_ProxyMiddleware` runs (`:962-968` docstring: "any field accepted by the backend's signature is also accepted here") — so an undeclared top-level `repo` arg would NOT reliably reach tools; it must be added to signatures (or ride the existing `payload` dict).
- Documentation: `tool_metadata.py` `_FULL_CATALOG` (`:29+`) entries carry `description/use_cases/inputs/output/example`; 34 entries document a `project_id` input (e.g. `:39`). `list_mcp_functions` inputs are re-derived from the live signatures at startup by `_inputs_from_signature`/`_sync_catalog_inputs_with_registered_tools` (`unified_mcp.py:283-355`) with fallback descriptions from `_CATALOG_PARAM_DESCRIPTIONS` (`:244-254`).
- Output contract mandates real annotations: `docs/mcp-output-contract.md:259-274`.

### 4. Response envelope contract and sibling doc-side plan

- `docs/mcp-output-contract.md` v2 single-channel envelope (`:19-104`): everything in `content[0].text` JSON; `structuredContent` MUST NOT be emitted; `data` holds the whole business payload; envelope fields are fixed.
- Immutability rules: envelope shape must not change; business field names MAY differ per tool (`:81-89`); no duplicate `ok`/`error` inside `data`; diagnostics catalogs must not be dumped into responses (`:148-153`); breaking field changes require migration notes (`:374-384`).
- **Scope block precedent (mind server, `semantic_graph_search`)** — `docs/mcp-output-contract.md:297-322`: additions go **inside `data`** as a `scope` object plus a top-of-data `warnings: list[str]` mirror. Fields: `mode` ("scoped"|"collection"|"unscoped"|"fallback_all"), `requested_project_id`, `resolved_project_id`, `registry_hit`, `collections_searched` (kept meaning), `collections_present`, `per_collection_hits`, `discovered_collections`, `targets_total/targets_searched` (cap `SCOPE_MAX_TARGETS` default 12), `graph_present`, `warnings`. `strict_scope=true` + unknown id → `ok:false` with `project_not_registered`; clean unscoped returns `mode:"unscoped"`, `warnings: []`.
- Sibling plan `docs/plans/261012-project-id-scope-mechanism/plan.md:47-63` ("Shared conventions — follow verbatim"): response additions inside `data`; envelope immutable (cites `mcp-output-contract.md:81-84`); keep existing field meanings; new facts go in new scope subfields; typed signature params; test invocation via the single pytest root with the merge-gate set (`docs/mcp-output-contract.md:399-406`).

### 5. Tests and how to add a repo-scope test

- Pytest config: `pyproject.toml:40-42` — `pythonpath = ["code-tiny", "doc-tiny"]`, `addopts = "--import-mode=importlib"`; one pytest root covers both trees. Run from repo root: `.venv/bin/pytest -q tests/...`.
- Relevant existing suites:
  - Helper-level unit tests: `code-tiny/tools/common/test_project_scope.py:41-158` (filters_by_project, prefix expansion, enrich adds normalized key `:146`, empty params `:158`) — the natural home for a repo-helper unit test.
  - Backend query tests: `code-tiny/tests/mcp/cplus/test_cplus_mcp.py` — `AsyncMock` on `_run_cypher` / `_list_databases` (`:142-184`) asserts which db candidates ran; the same fixture style can capture `(query, params)` and assert the repo predicate + `$repo` param.
  - Service-level: `tests/test_project_context_service.py` — `RecordingRunner` (`:28`) asserts `call[1]["project_id_normalized"] == "shop"` (`:142`) — exact template for asserting a `$repo` param.
  - Unified dispatch: `tests/test_unified_mcp_input_coercion.py` (`IsolatedAsyncioTestCase`, patched dispatch/fanout `:376-537`) and `tests/test_unified_mcp_wrapper_signatures.py` — the latter asserts live registry schema properties for fan-out tools (`:90-111`) and catalog inputs for `parser_type` (`:127-145`): a new `repo` param on fan-out tools must be added to backend signatures AND catalog inputs consistently or these guards fail.
  - Contract gate set (merge gate): `docs/mcp-output-contract.md:399-406`.
- Fixtures with `repo` set: `code-tiny/tests/test_language_writer_project_scope.py:84` (`"repo": "git@x"` in writer rows), `tests/test_primary_vector_sync.py` (vector payload repo), plus sync fixtures (`tests/test_incremental_sync_cobol.py`, `tests/test_graph_write_journal_runtime.py`). No MCP-level fixture seeds `repo` today.

### 6. Tools where repo scoping does NOT make sense (safe read-only subset)

- **Exclude — metadata/contract:** list_databases, list_qdrant_collections, list_parsers, inspect_parser_capabilities, list_mcp_functions (refs in Finding 1c).
- **Exclude — write:** annotate_node (`fastmcp_server.py:2437`) addresses nodes by id.
- **Exclude — node labels lack `repo` entirely:** the 6 project-context tools read `ProjectModule`/`BuildDescriptor`/`FrameworkInstance`/`ApiEndpoint` labels written by `tools/graph/writer/project_topology_writer.py`, which has **zero** `repo` writes (grep count 0; it only sets `project_id_normalized`, `project_topology_writer.py:20,125,133,162,196,224,241`). A repo filter there would always return empty. (Bridge endpoints written by `unified_mcp.py:2468,2643,2650,2678,2683` likewise set only `project_id_normalized`.)
- **Candidate safe read-only subset:** the 13 fan-out tools + get_ipc_message + find_screen_workflows + 5 planner tools + semantic_search + explore_graph + the 5 unified Cypher flow/bridge tools + 3 fastmcp-only workflow tools — i.e., everything whose Cypher matches code nodes (File/Function/Class/Type/Namespace/Package carry `repo`, see Finding 7).

### 7. Existing repo / repo-like filtering

- **No MCP tool accepts or filters on a `repo` parameter today** (grep of `code-tiny/mcp/**` for `\brepo\b` excluding "repository" only hits a Cypher loop variable named `repo` bound to `:Repository` label nodes in `unified_mcp.py:2665-2702` — a naming collision to avoid in new query text, though `$repo` as a *parameter* is safe).
- The `repo` property is already written on every code node by the language writer: `tools/graph/writer/language_writer.py:579` (File), `:1036,1074,1113,1153,1195,1234,1281,1327,1386,1432,1477,1518,1562,1606,1652,1699,1753` (packages, namespaces, functions, classes, types, endpoints, imports, views, components, db nodes — 18 write sites). cplus analyzer rows also carry it (`tools/cplus/cplus_analyzer.py:4455-4579, 5062-5252`).
- `Repository` nodes are MERGE'd with `name = repo value` and a uniqueness constraint (`tools/sync/incremental_sync.py:1410-1439`), and `write_repo_file_edges` links `(Repository)-[:HAS_FILE]->(File)` by exact name match against the file's `repo` field (`language_writer.py:599-614`). Exact-match equality against `Repository.name` is therefore already an operating invariant on the write side.

### 8. Matching-semantics risk: heterogeneous repo values

- Two stored flavors coexist:
  - Absolute path default: `python_analyzer.py:2061` (`repo = args.repo or os.path.abspath(args.root)`), same in `js/js_analyzer.py:1931`, `java/java_analyzer.py:2501`; `--repo` env default `PROJECT_REPO` (`python_analyzer.py:1942`). When run via incremental_sync, `--repo` is only passed for cplus (`incremental_sync.py:1357-1358`), so python/js/java keep the abspath default.
  - `{project_name}/{basename(root)}`: `_repository_name` (`incremental_sync.py:1284-1287`) for cplus; go self-derives the same shape (`go_analyzer.py:1010-1012`, `:1204,1250`).
- Consequence: exact equality `n.repo = $repo` only matches one flavor. Suffix matching (user-observed `ENDS WITH "s3"`) spans both, ideally with a leading slash (`ENDS WITH '/s3'`) to avoid basename-prefix accidents; but users may pass either form, so the helper should accept a value and match `repo = $repo OR repo ENDS WITH '/'+$repo`-style (exact decision left to plan).
- `ENDS WITH` usage today: **zero occurrences in code-tiny** (grep). Live verification performed for the embedded default-class provider: LadybugDB 0.20.4 in `.venv` accepts `MATCH (n:Item) WHERE n.repo ENDS WITH '/s3' RETURN count(n)` and returns correct counts (probe run 2026-10-16). Neo4j supports `ENDS WITH` per openCypher (well-known, not probed here). falkordblite is not installed in this environment — **unverified** (marked unknown); the FalkorDB dialect generally supports ENDS WITH but a smoke query should be added if it becomes load-bearing.
- A derived normalized property (like `project_id_normalized`) plus backfill is the alternative that restores plain equality matching; backfill precedent: `code-tiny/scripts/backfill_project_scope_keys.py` (dry-run default, `--apply`, never touches ids/vectors, `:1-6`).

### 9. `repo` in Qdrant payloads (coordinator follow-up)

- **(a) Fields stored alongside `project_id_normalized` for code vectors** — `tools/common/primary_vector_sync.py:129-143` (`documents_from_payloads`, the shared vector contract): `node_type, symbol_id, project_id, project_id_normalized (:133), project_name, language, repo (:136), file_path, name, qualified_name, parser, root_scope, text` (+ optional start_line/end_line). The legacy analyzer-embedded Qdrant cache path stores `repo` too: `python_analyzer.py:1748` (function points), `:1773` (class points), and file points.
- **(b) `repo` IS among them** — `primary_vector_sync.py:136`: `"repo": str(source.get("repo") or "")`. Note it stores the raw value (no `repo_normalized`, no casefolding).
- **(c) Adding repo filtering to semantic_search:** no filter exists — the vector read path only builds the project filter: `tools/common/qdrant_query_support.py:87-91` (`query_filter = qdrant_project_filter(project_id)` when no explicit filter) fed from `cplus_mcp.py:954,1046`. Two options:
  - Server-side Qdrant filter: extend `qdrant_project_filter` (`project_scope.py:135-159`) with a `repo` `match` clause — but Qdrant payload match is exact/any only; it cannot express suffix matching, so it only works when the caller's value equals the stored flavor.
  - Python post-filter after retrieval: precedent `matches_project_scope` applied post-hoc in `intelligent_retrieval.py:270` — supports exact-or-suffix semantics and works over the merged hits in `merge_hits` (`qdrant_query_support.py:125-143`), at the cost of possibly trimmed top-k (filter after LIMIT).
  - Backfill of `repo_normalized` (if a normalized key is wanted) can follow `scripts/backfill_project_scope_keys.py`. Older collections may carry `repo: ""` or no field — post-filter must decide explicitly whether empty-repo points are dropped or kept (marked open decision).
- explore_graph scoping: `services/explore_service.py` resolves targets from the project registry (`:411-559`) and never looks at `repo`; a repo scope there would be a Python-side filter over `matched_nodes` payloads (semantic hits carry the Qdrant payload; graph-expanded neighbors come from graph nodes that have `repo`).

### 10. Dispatch/fan-out pass-through (how `repo` reaches backends)

- `_dispatch_tool` merges all arguments into one payload and calls backends as `fn(payload=merged)` (`unified_mcp.py:1515`); `_fanout_dispatch` copies `base_payload` per engine unchanged (`unified_mcp.py:1335-1395`). So once `repo` is in the payload and each backend tool body reads `payload.get("repo")`, fan-out scoping works with no dispatcher changes.
- Fan-out result merging: list keys merged per `_FANOUT_LIST_RESULT_KEYS` (`unified_mcp.py:172-186`); single-object keys surface only inside `parser_results` (`:190-196`); diagnostic keys kept per-parser only (`:201-218`). A `repo` echo/scope report would need explicit handling (it is not in any of these sets — setdefault-style additions at the top level like `capability` at `:1481,1526` are the precedent).

---

## Conventions to follow

1. Optional param = typed signature `repo: Optional[str] = None` on each supporting backend tool (fastmcp_server.py + cplus_mcp.py + android_mcp.py twins), documented in `tool_metadata.py` inputs, plus a `_CATALOG_PARAM_DESCRIPTIONS` entry (`unified_mcp.py:244-254`). Keep `required` unset.
2. Scope semantics mirror `project_id`: omitted/blank → no predicate (search everything); a value → predicate active. Implement via `tools/common/project_scope.py` (new sibling helpers, e.g. `repo_scope_match` / extend `prepare_project_scope_parameters` to pass `repo` through) rather than ad-hoc per-tool logic.
3. WHERE predicate shape follows the project_id guard: `AND ($repo IS NULL OR n.repo = $repo ...)` added next to the existing `project_id_normalized` predicate in each query (duplication is the established pattern; ~94 predicates exist today).
4. Response additions are additive inside `data` only (envelope immutable, `mcp-output-contract.md:81-89`); if a scope report is wanted, follow the `scope` block + `warnings` mirror shape from `mcp-output-contract.md:297-322`.
5. Tests go under the single pytest root (repo root or `code-tiny/tests/`), asserting captured `(query, params)` via AsyncMock/RecordingRunner, plus a live-registry schema test mirroring `tests/test_unified_mcp_wrapper_signatures.py:90-111`.
6. Do not touch metadata tools, annotate_node, or the 6 project-context tools (their labels have no `repo` property at all).

## Risks / open questions

1. **Repo value heterogeneity is the core semantic risk** (absolute path vs `{project_name}/{basename}`; Finding 8). Exact-match-only scoping will silently return empty for users quoting the other flavor. [evidence-backed]
2. **`ENDS WITH` support:** verified live on LadybugDB 0.20.4; assumed for Neo4j (openCypher standard); **unknown for falkordblite/FalkorDB remote** (not installed in this env — needs a smoke probe or a `RIGHT()`-based fallback decision). [partially inferred]
3. **No backfill for historical nodes:** nodes written before `repo` existed, or by analyzers that never set it (e.g., anything written outside language_writer), would be excluded from scoped results; whether scoped queries should also match `repo IS NULL` is an open product decision. [inferred]
4. **Qdrant suffix matching is impossible server-side** (Qdrant payload match is exact/any); post-filter after `LIMIT` can shrink below `top_k`. Open choice: accept trimmed results vs over-fetch-then-trim. [evidence-backed]
5. **Legacy Qdrant points with `repo: ""`** (predating the field) — drop or keep under scope? [open]
6. **Schema-drift guards will fail if surfaces diverge:** `repo` must land on backend signatures + live registry + catalog simultaneously (`tests/test_unified_mcp_wrapper_signatures.py`). [evidence-backed]
7. **Cypher identifier collision:** `repo` is already used as a node variable name in `get_api_call_chain` queries (`unified_mcp.py:2665-2702`); new query text must not shadow it (use `$repo` param only). [evidence-backed]
8. **java_mcp.py carries a fourth copy of the predicates** but is not a unified backend — decide whether it is updated for consistency or explicitly left behind (it is dev-launcher only). [inferred]
9. Whether the unified `scope`-style report (`mode/requested/resolved`) is wanted on code tools now, or only the bare filter (sibling plan deferred scope blocks on non-semantic tools). [open]
10. Planner tools (compute_scc etc.) traverse prerequisite graphs built from File nodes; repo filtering there changes dependency-ordering results per repo — presumably desired, but scope-report keys (`rel_types`, `direction`, …) in `_FANOUT_DIAGNOSTIC_KEYS` (`unified_mcp.py:201-218`) would not surface it. [inferred]

## Recommended approaches

1. **Graph side:** add `repo: Optional[str] = None` to the read-only query-tool signatures (fastmcp/cplus/android twins + catalog), extend `project_scope.py` with an exact-or-suffix `repo` match helper, and add `AND ($repo IS NULL OR n.repo = $repo OR n.repo ENDS WITH $repo_suffix)` predicates beside the existing project_id guards.
2. **Vector side:** post-filter merged semantic_search/explore_graph hits in Python with the same helper (Qdrant-side exact match only if/when a normalized repo key exists), keeping `qdrant_project_filter` untouched.
3. **Optionally** introduce a derived `repo` match key (basename-normalized) written by analyzers + `backfill_project_scope_keys.py`-style script to make query-time matching plain equality — do this only if exact/suffix semantics prove too ambiguous in practice.
