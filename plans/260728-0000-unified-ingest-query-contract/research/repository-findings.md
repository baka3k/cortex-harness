# Repository Findings — Unified Ingest/Query Contract Research

Source: background researcher (Explore agent) on 2026-07-28. All file:line refs
verified against the working tree at research time. This file is the evidence
backing the plan's "Verified Current Behavior" section.

## A. graph_mcp (port 8788) Query Path

### A1. `_resolve_graph_database` and call sites

- `code-tiny/mcp/unified_mcp.py:1962` — resolution chain:
  `db arg → active_project["database_name"] → FALKORDB_GRAPH →
  FALKORDB_DATABASE → cplus_backend.DEFAULT_GRAPH_DB → "hyper_graph"`.
  (`NEO4J_DB` was deliberately removed — see saved memory bug #1.)
- 5 call sites:
  - `_run_project_context_tool` (2050) — **FIXED**:
    `_resolve_graph_database(db or project_id_lookup_key(project_id))`.
  - `find_callers_of_endpoint` (2351) — **UNFIXED**: `_resolve_graph_database(db)`.
  - `get_api_call_chain` (2466) — **UNFIXED**.
  - `analyze_workflow_impact` (2690) — **UNFIXED**.
  - `find_workflows_containing` (2850) — **UNFIXED**.

### A2. `activate_project` / `active_project` state

- `unified_mcp.py:187` — module-level dict with only `parser_type`,
  `database_name`. No `project_id`. Not persisted, not shared across server
  instances.
- Set by `activate_project` tool at `unified_mcp.py:656-690`.

### A3. project_id → Cypher propagation

- `code-tiny/tools/common/project_scope.py` —
  `prepare_project_scope_parameters`, `qdrant_project_filter`,
  `matches_project_scope`, `project_id_lookup_key`,
  `PROJECT_ID_NORMALIZED_FIELD = "project_id_normalized"`.
- Canonical predicate (60+ occurrences):
  `AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)`.
- Bridge tools use inline property literals:
  `project_id_normalized: $be_project_normalized` etc.

### A4. semantic_search / explore_graph Qdrant collection

- `unified_mcp.py:1116` (semantic_search), `1796` (explore_graph): both accept
  `collection` + `project_id` args.
- `code-tiny/mcp/services/explore_service.py:50`:
  `_DEFAULT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "")` (empty → auto).
- `code-tiny/mcp/cplus/cplus_mcp.py:99`:
  `DEFAULT_QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "kotlin_functions")`.
- Qdrant project filter applied via `qdrant_project_filter(project_id)` in
  android_mcp.py:791, java_mcp.py:617.
- Engine: `code-tiny/tools/common/intelligent_retrieval.py`.

## B. graph_mcp Ingest Path

### B1. Entry + CLI args

- Entry: `dev sync code` → `cortex_harness/dev.py:1955` →
  `code-tiny/tools/sync/incremental_sync.py`.
- Args: `--project-id`, `--project-name`, `--project-code`, `--neo4j-db`,
  `--falkordb-graph`, `--qdrant-collection`, `--message-qdrant-collection`.

### B2. Graph + collection derivation

- `incremental_sync.py:898,907`:
  `database = args.neo4j_db or args.falkordb_graph; graph_name = args.falkordb_graph or database`.
- `incremental_sync.py:1890`:
  `qdrant_collection = _code_collection_name(project_id, root, parser, project_code=...)`
  → names like `next-messages` when `HYPERPACK_COLLECTION_SCHEME=per_project`.
- `_message_collection_name` (377): `f"{safe_segment(project_id)}_mess"`.
- Convention `project_id == collection` is only the default fallback.

### B3. Topology writer

- `code-tiny/tools/graph/writer/project_topology_writer.py` writes via injected
  driver. Project keyed by `project_id`; ProjectModule by `id`. Both carry
  `project_id` + `project_id_normalized`. Already coexists safely in a shared
  graph.

### B4. Language writer coverage gaps

- `code-tiny/tools/graph/writer/language_writer.py`:
  - Writes `project_id_normalized` on: `write_files`, `write_packages_full`,
    `write_namespaces_full`, `write_files_with_imports`.
  - Missing `project_id_normalized` on: `write_function_types` (~440),
    `write_fields` (~480), `write_aliases` (~520), `write_templates` (~560).
  - `write_calls` (~390): neither `project_id` nor `project_id_normalized`.
- `write_projects` (~600): `MERGE (p:Project {project_id: row.id})` — global per
  graph.

### B5. setup_constraints.py

- `code-tiny/scripts/setup_constraints.py` — Neo4j-driver-only, default
  `--neo4j-db=neo4j`, no FalkorDB branch. Under FalkorDB relies on `NEO4J_DB`
  env being set to the graph name (dev.json masks this by setting
  `NEO4J_DB=cortext`).

## C. mind_mcp (port 8789) Query Path

### C1. query_graph_rag_langextract targeting

- `doc-tiny/mcp_graph_rag.py:35`:
  `QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION_DOC", "documents")`.
- Per-call `collection` override exists (line ~252). Graph has **no per-call
  override** — fixed at server boot via `create_graph_store_from_env()`.
- Qdrant filter by `source_id` only (line ~110).

### C2. list_source_ids / get_paragraph_text

- Scope by `source_id` only. No project concept.

### C3. project_id in doc-tiny

- **Zero matches** in grep across doc-tiny.

## D. mind_mcp Ingest Path

### D1. graphrag_ingest_langextract.py arg flow

- `--collection` default `os.getenv("QDRANT_COLLECTION_DOC", "graphrag_entities")` (line 927).
- `--source-id`; folder mode prefixes each file with `f"{source_id}__{file}"`.
- `--neo4j-*` + `add_graph_store_args` (`--falkordb-graph`).
- **No `--project-id`.**
- Entity ID: `uuid5(ent_type::name_norm)` — **global merge** across all docs.
- Qdrant payload: `{paragraph_id, source_id, text, entity_ids, entity_mentions}` — no project_id.

### D2. dev.py doc collection override

- `cortex_harness/dev.py:833`: `"--collection", project["name"]` (e.g.
  `"cortext"`), not the config's `QDRANT_COLLECTION=cortext_doc` and not the
  MCP server's default. **Triple mismatch.**

### D3. graph_store.py

- `doc-tiny/graph_store.py` supports FalkorDB (`FalkorDBGraphStore` wraps
  `FalkorDBDriver`) and Neo4j.
- `create_graph_store_from_env()` reads `DOC_GRAPH_PROVIDER`/`GRAPH_PROVIDER`;
  graph name from `FALKORDB_GRAPH`/`FALKORDB_DATABASE` with default **`"neo4j"`**
  (line 167) — different from code-tiny's `"hyper_graph"`.

### D4. neo4j_loader.py (legacy)

- `doc-tiny/neo4j_loader.py` uses `from neo4j import GraphDatabase` +
  `neo4j_graphrag.retrievers`. Uses `enviroment_loader.py` which reads
  `NEO4J_USERNAME` (not `NEO4J_USER`), `QDRANT_KEY`. Prints credentials. Not
  in the active ingest path.

### D5. 0_reset_all.py

- `doc-tiny/0_reset_all.py`: `MATCH (n) DETACH DELETE n` — whole-graph wipe.
  Deletes one Qdrant collection. No per-project reset.

## E. Divergences & Shared Infra

### E1. Env-var readers

- `FALKORDB_GRAPH`: cplus_mcp.py:122, android_mcp.py:114, fastmcp_server.py:128,
  explore_service.py:73, unified_mcp.py:1978, graph_store.py:117, cli.py:88,
  incremental_sync.py:983, dev.py:352/375/1731, mcp-lifecycle.py:439.
- `NEO4J_DB`: cplus_mcp.py:117, android_mcp.py (via DEFAULT_NEO4J_DB),
  fastmcp_server.py, explore_service.py:65, setup_constraints.py,
  incremental_sync.py:2348, harness_config.py:19, dev.py:385/1744,
  mcp-lifecycle.py:485.
- `QDRANT_COLLECTION` (code): cplus_mcp.py:99 (`"kotlin_functions"`),
  explore_service.py:50 (`""`), incremental_sync.py, harness_config.py:28,
  dev.py:407/1758, mcp-lifecycle.py:494.
- `QDRANT_COLLECTION_DOC` (doc): mcp_graph_rag.py:35 (`"documents"`),
  graphrag_ingest_langextract.py:927 (`"graphrag_entities"`),
  0_reset_all.py:45 (`"graphrag_entities"`), mcp-lifecycle.py:494,
  Readme.md:56 (`"graph_rag_entities"`). **Three+ different defaults.**
- `PROJECT_ID`: incremental_sync.py:2284, mcp-lifecycle.py:484. Not in doc-tiny.

### E2. Config loaders disagree

- `code-tiny/tools/common/harness_config.py` `load_harness_config()`: reads
  only `cfg["code"]["env"]`; sets `NEO4J_*`, `QDRANT_*`; **does NOT set
  `FALKORDB_GRAPH`**.
- `doc-tiny/enviroment_loader.py`: reads `QDRANT_KEY`, `NEO4J_USERNAME` (≠
  `NEO4J_USER`), `NEO4J_PASS`, `OPENAI_API_KEY`. No FalkorDB, no graph/collection.
- They share **no env-var names** for overlapping concepts.

### E3. Graph-name defaults

- code-tiny: `"hyper_graph"` (4 files).
- doc-tiny `graph_store.py:167`: `"neo4j"`.
- `mcp-lifecycle.py:439`: `"hyper_graph"`.
- `cplus_mcp.py:117`: `DEFAULT_NEO4J_DB = os.environ.get("NEO4J_DB") or "hyper_graph"`.

### E4. .cortext-harness/config/dev.json

- `project.code = "cortext"`, `project.name = "cortext"`.
- `code.env.FALKORDB_GRAPH = "cortext"`, `code.env.NEO4J_DB = "cortext"`,
  `code.env.QDRANT_COLLECTION = "cortext"`.
- `doc.env.FALKORDB_GRAPH = "cortext"` (**same graph as code**),
  `doc.env.QDRANT_COLLECTION = "cortext_doc"`.
- Both `NEO4J_URI = "redis://localhost:6379"` (FalkorDB via redis URI).
- `QDRANT_COLLECTION_DOC` is never set in this file.

### E5. No formal registry

- De-facto registry spread across: dev.json, `mcp-lifecycle.py` CLI flag
  parsing, undocumented convention. `dev.py:1592` documents
  `--project` help: "Project ID; also defaults database and vector collection
  names" — but broken for doc by `dev.py:833`.

## Top 12 Divergences

1. Doc collection triple-mismatch: ingest writes `project.name`, config declares
   `cortext_doc`, MCP server defaults to `documents`.
2. Two launchers with different env semantics (`dev.py mcp start` vs
   `mcp-lifecycle.py`).
3. Three different defaults for `QDRANT_COLLECTION_DOC` (`documents`,
   `graphrag_entities`, `graph_rag_entities`).
4. `mind_mcp` has no `project_id` concept; only `source_id`.
5. Graph-name default divergence: code `"hyper_graph"`, doc `"neo4j"`,
   mcp-lifecycle `"hyper_graph"`.
6. `mind_mcp` graph fixed at boot; `graph_mcp` graph per-call.
7. Doc entities merged globally across projects by `uuid5(ent_type::name_norm)`.
8. `language_writer.py` inconsistent `project_id_normalized` coverage (Field,
   Alias, Template, FunctionType, CALLS).
9. `setup_constraints.py` Neo4j-driver-only, default `--neo4j-db=neo4j`.
10. Config loaders share no env-var names (`NEO4J_USER` vs `NEO4J_USERNAME`).
11. `_resolve_graph_database` ignores `project_id` at 4 of 5 call sites.
12. `0_reset_all.py` whole-graph wipe, no per-project reset.

## Additional load-bearing notes

- "One graph, many projects, filter at query time" read model is **already
  built** on code side via `project_scope.py` and the `$project_id IS NULL OR
  ...` predicate family. `backfill_project_scope_keys.py` exists to retrofit
  `project_id_normalized` onto existing nodes.
- `dev.json` already demonstrates code+doc sharing one FalkorDB graph
  (`cortext`); graph-sharing across the two servers is configurally possible
  today. The skew is in Qdrant collection naming and the absence of project_id
  in doc-tiny.
- `mcp-lifecycle.py` is the closest thing to a canonical targeting contract
  and supports independent `--code-database`/`--doc-database`/
  `--code-collection`/`--doc-collection`. `tests/test_make_lifecycle.py:190`
  asserts `doc_env["QDRANT_COLLECTION_DOC"] == "doc_vectors"`.
