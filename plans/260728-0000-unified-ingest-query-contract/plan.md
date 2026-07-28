---
title: "Unified Ingest/Query Contract for mind_mcp and graph_mcp"
status: pending
created: 2026-07-28
mode: hi-plan --full
scope: code-tiny MCP query/ingest, doc-tiny MCP query/ingest, shared registry, launchers, config loaders
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans:
  - 260723-0908-case-insensitive-project-id
  - 260725-1703-project-topology-context-tools
  - 260719-0100-mcp-query-capability-hardening
  - make-mcp-lifecycle
amendedBy: [260728-0900-simplify-search-full-removal]
reviewed: 2026-07-28
---

> **AMENDMENT NOTE (2026-07-28):** Plan `260728-0900-simplify-search-full-removal`
> supersedes the `search_full` flag and `ProjectScopeRequiredError` introduced in
> this plan's Phase 02 and Phase 05. After the amendment, every project-scoped
> tool takes only `project_id` — present means scoped, absent means full search.
> The `search_full` parameter, `ProjectScopeRequiredError` class, and all
> `$search_full` Cypher parameters are removed. Execute the amendment plan
> **after** this plan's Phase 01 (registry) and Phase 02/05 (query path) land,
> or merge the changes into those phases directly.
---

# Unified Ingest/Query Contract for mind_mcp and graph_mcp

## Overview

Today the two MCP servers — `graph_mcp` (code, port 8788) and `mind_mcp` (doc,
port 8789) — target their graph and vector stores through a patchwork of CLI
args, env vars, module-level defaults, and in-process state. There is **no
single source of truth** that maps a `project_id` to its graph name and Qdrant
collection(s). Research surfaced **12 concrete divergences** between the two
servers' ingest and query paths, ranging from three different defaults for the
same env var to a doc server that has no notion of `project_id` at all.

This plan establishes **one project registry**, **one naming contract**, and
**one stateless query contract** so that:

- `project_id` is the only key a client needs to pass per call.
- Each project owns its graph shard and its Qdrant collection(s) by convention.
- A query against project X returns X's data and only X's data — on both
  servers, regardless of launcher.
- Ingest and reset are scoped per project and never leak across projects.
- The contract is the same on both servers; only the node labels and vector
  payloads differ.

## Scope Challenge Decisions

### 1. Target data model: per-project sharding vs multi-tenant graph

**Selected: per-project sharding.** Each `project_id` owns its own graph and
its own Qdrant collection(s). The "one graph, many projects, filter by
`project_id_normalized`" predicate family is kept as a **defensive read-time
safety net** (it already exists in `project_scope.py` and survives accidental
collisions), but ingest deliberately targets one shard per project. This
matches the existing `dev.json` convention and gives cheap isolation, easy
per-project reset, and predictable performance.

### 2. Query contract: stateful `activate_project` vs stateless per-call id

**Selected: stateless per-call `project_id`.** Every MCP tool that is
project-scoped accepts a `project_id` argument and resolves the target shard
through the registry on every call. `activate_project` remains only as an
**optional default** for ergonomic single-project sessions — it must never be
the only way to pick a shard. A query with no `project_id` and no
`active_project` is an explicit error, not a silent fallback to an env-derived
default.

### 3. Unification scope: contract/registry only vs merged server

**Selected: unify contract and registry only.** `graph_mcp` keeps owning code
analysis; `mind_mcp` keeps owning document RAG. The two servers share: (a) one
`ProjectRegistry`, (b) one naming contract, (c) one launcher env semantics,
and (d) one provider-neutral config loader. No server is merged; no domain
expertise is moved across boundaries. This keeps blast radius small and
preserves the existing tool surface.

## Verified Current Behavior (research summary)

Full evidence in `research/repository-findings.md`. Top load-bearing facts:

- `_resolve_graph_database(db)` in `code-tiny/mcp/unified_mcp.py:1962` resolves
  through `db arg → active_project["database_name"] → FALKORDB_GRAPH →
  FALKORDB_DATABASE → DEFAULT_GRAPH_DB → "hyper_graph"`. **Only 1 of 5 call
  sites** (`_run_project_context_tool`, line 2050) was fixed to also consult
  `project_id`. The other 4 bridge tools (`find_callers_of_endpoint` 2351,
  `get_api_call_chain` 2466, `analyze_workflow_impact` 2690,
  `find_workflows_containing` 2850) pick the graph from active/env and only
  filter within it by `be/fe_project_normalized`.
- `active_project` (module-level dict, `unified_mcp.py:187`) has only
  `parser_type` and `database_name` fields — **no `project_id`**, not persisted,
  not shared across server instances.
- `doc-tiny` has **zero matches** for `project_id`. Its only scoping dimension
  is `source_id`. Doc entities are merged globally by
  `uuid5(ent_type::name_norm)` regardless of which document/project they came
  from — a shared doc graph cannot be queried per project today.
- **Three different defaults for `QDRANT_COLLECTION_DOC`**: `mcp_graph_rag.py`
  → `"documents"`, `graphrag_ingest_langextract.py` and `0_reset_all.py` →
  `"graphrag_entities"`, `Readme.md` → `"graph_rag_entities"`.
- `dev.py:833` overrides doc ingest collection to `project["name"]` (e.g.
  `"cortext"`), but the doc MCP server reads `QDRANT_COLLECTION_DOC` (which
  `dev.py mcp start` never sets) and falls back to `"documents"`. Three
  different names for the same logical store.
- **Two launchers disagree**: `dev.py mcp start` never exports
  `QDRANT_COLLECTION_DOC`; `scripts/mcp-lifecycle.py:494` correctly sets both
  `QDRANT_COLLECTION` (code) and `QDRANT_COLLECTION_DOC` (doc) plus
  `FALKORDB_GRAPH`/`NEO4J_DB`/`PROJECT_ID`. Same server, different runtime
  targeting depending on which launcher was used.
- `language_writer.py` writes `project_id_normalized` on File/Package/Namespace
  but **not** on Field/Alias/Template/FunctionType and writes **neither** on
  CALLS edges. A query using the standard predicate silently drops those node
  and edge types.
- `setup_constraints.py` is **Neo4j-driver-only** with `--neo4j-db` default
  `"neo4j"` and no FalkorDB branch. Under FalkorDB it relies on `NEO4J_DB` env
  being set to the graph name (dev.json masks this).
- `0_reset_all.py` wipes an entire graph (`MATCH (n) DETACH DELETE n`) and one
  Qdrant collection — no per-project reset primitive exists.
- `harness_config.py` (code) reads `NEO4J_USER`/`QDRANT_COLLECTION`;
  `enviroment_loader.py` (doc legacy) reads `NEO4J_USERNAME`/`QDRANT_KEY`. They
  share **no env-var names** for overlapping concepts.
- No formal `project_id → {graph, collection, parser}` registry exists. The
  de-facto registry is spread across `dev.json`, `mcp-lifecycle.py` CLI flag
  parsing, and undocumented convention.

## Target Architecture

### ProjectRegistry — single source of truth

A new module `code-tiny/tools/common/project_registry.py` exposes:

```python
@dataclass(frozen=True)
class ProjectTargets:
    project_id: str                 # raw, preserved for display/identity
    project_id_normalized: str      # casefold() key for comparisons
    code_graph: str                 # FalkorDB graph name / Neo4j db name
    code_qdrant_collection: str
    doc_graph: str
    doc_qdrant_collection: str
    parser_type: Optional[str]
    provider: str                   # "falkordb" | "neo4j"

def resolve_project_targets(project_id: str) -> ProjectTargets: ...
def list_registered_projects() -> list[str]: ...
```

- The registry reads from `.cortext-harness/config/*.json` (canonical project
  descriptors) plus optional env overrides for ad-hoc projects.
- Default naming contract (applied when config omits a field):
  - `code_graph == project_id`
  - `code_qdrant_collection == project_id`
  - `doc_graph == project_id` (code and doc may share one graph; they own
    disjoint label spaces)
  - `doc_qdrant_collection == f"{project_id}_doc"`
- Both MCP servers, both launchers, both ingest entrypoints, and both reset
  scripts call `resolve_project_targets(project_id)` instead of deriving names
  independently.

### Stateless query contract (both servers)

Every project-scoped MCP tool accepts `project_id` and resolves targets through
the registry on each call. `activate_project` and `active_project` state are
**removed** (see Validation Interview decisions) — every call must carry
`project_id` or `search_full=true`.

A `search_full` flag (default `false`) is added to every project-scoped tool as
an explicit escape hatch. The precedence is:

1. `project_id` arg present → scope to that project's shard via the registry.
2. `project_id` absent, `search_full=true` → query across **all** projects in
   their shared graph/collection with no `project_id_normalized` filter. The
   caller explicitly opts into cross-project scope and accepts the cost.
3. `project_id` absent, `search_full=false` (default) → explicit
   `ProjectScopeRequiredError` (never a silent env or state fallback).

The flag is named `search_full` on both servers for symmetry. It is the only
way to obtain an unscoped result set; the previous "missing id → env default"
silent path and the `activate_project` stateful default are both removed.

### Naming contract (single rule set)

| Concept | Rule |
| --- | --- |
| `project_id` raw | Preserved as identity/display. Source of truth for IDs. |
| `project_id_normalized` | `str(value).strip().casefold()`. Comparison key only. |
| Code graph | `== project_id` |
| Code Qdrant collection | `== project_id` |
| Doc graph | `== f"{project_id}_doc"` (separate from code; disjoint labels) |
| Doc Qdrant collection | `== f"{project_id}_doc"` |
| Point IDs / symbol IDs | Unchanged — raw `project_id` stays inside identity. |

### Cross-server scoping parity

- `graph_mcp` keeps filtering by `project_id_normalized` inside the graph as a
  defensive safety net (already implemented in `project_scope.py`).
- `mind_mcp` gains `project_id` + `project_id_normalized` on Document,
  Paragraph, and Entity nodes and on Qdrant payloads, and filters on it.
- Entity merge keys become per-project: `uuid5(project_id::ent_type::name_norm)`
  instead of global, so two projects sharing one doc graph do not collapse
  entities.

## Phases

1. [Phase 01 — ProjectRegistry and naming contract](phase-01-project-registry.md)
2. [Phase 02 — graph_mcp stateless query path](phase-02-graph-mcp-query.md)
3. [Phase 03 — graph_mcp ingest normalization and provider-neutral schema](phase-03-graph-mcp-ingest.md)
4. [Phase 04 — mind_mcp project_id introduction](phase-04-mind-mcp-project-id.md)
5. [Phase 05 — mind_mcp stateless query path](phase-05-mind-mcp-query.md)
6. [Phase 06 — Unified launcher and config loader](phase-06-launcher-config.md)
7. [Phase 07 — End-to-end validation and acceptance](phase-07-validation.md)

## Cross-Plan Dependencies

- `blockedBy: neo4j-to-falkordb-migration` — provider-neutral driver/writer
  contract, schema setup, and unified MCP traversal surface must stabilize
  first. Pure registry/contract/helper work may proceed, but live FalkorDB
  parity acceptance is gated by the migration. The migration plan lists this
  plan in its `blocks` metadata (bidirectional update applied).
- Reuses `260723-0908-case-insensitive-project-id` (completed): the
  `project_id_normalized` field, `qdrant_project_filter`, and
  `prepare_project_scope_parameters` helpers stay as the comparison-key
  primitives; this plan extends them to doc-tiny and to graph targeting.
- Reuses `260725-1703-project-topology-context-tools` (completed): topology
  writer already writes `project_id` + `project_id_normalized`; this plan
  tightens the same coverage on `language_writer.py`.
- Reuses `260719-0100-mcp-query-capability-hardening` (completed): capability
  gates and provider schema inspection stay; this plan ensures they consult the
  registry-resolved graph, not env defaults.
- Extends `make-mcp-lifecycle` (completed): `mcp-lifecycle.py` becomes the
  canonical launcher; `dev.py mcp start` env semantics are aligned to it.

## Expected File Areas

- New: `code-tiny/tools/common/project_registry.py`
- New: `code-tiny/tools/common/test_project_registry.py`
- `code-tiny/mcp/unified_mcp.py` — `_resolve_graph_database`, 5 call sites,
  `activate_project`, `active_project`
- `code-tiny/mcp/cplus/cplus_mcp.py`, `code-tiny/mcp/android/android_mcp.py`,
  `code-tiny/mcp/java/java_mcp.py`, `code-tiny/mcp/services/explore_service.py`
  — replace env defaults with registry resolution
- `code-tiny/tools/graph/writer/language_writer.py` — close
  `project_id_normalized` gaps
- `code-tiny/tools/sync/incremental_sync.py` — target via registry
- `code-tiny/scripts/setup_constraints.py` — provider-neutral + registry-aware
- `doc-tiny/graphrag_ingest_langextract.py` — accept `--project-id`, write
  project_id on nodes and payloads, per-project entity IDs
- `doc-tiny/mcp_graph_rag.py` — per-call `project_id`, registry-resolved
  graph + collection
- `doc-tiny/0_reset_all.py` — per-project scoped reset
- `doc-tiny/graph_store.py` — per-call graph target via registry
- `cortex_harness/dev.py` — `_doc_env_for_process` sets
  `QDRANT_COLLECTION_DOC`; collection override uses registry
- `code-tiny/tools/common/harness_config.py` — provider-neutral,
  reads `code` + `doc` sections
- `scripts/mcp-lifecycle.py` — canonical launcher env semantics
- `.cortext-harness/config/*.json` — registry input format documented
- `docs/` — unified ingest/query contract documentation

## Scope Boundaries

### Included

- One `ProjectRegistry` and one naming contract across both servers.
- Stateless per-call `project_id` resolution on every project-scoped MCP tool.
- `project_id` introduction into doc-tiny graph nodes and Qdrant payloads.
- Per-project entity merge keys in doc-tiny.
- Per-project scoped reset on both servers.
- Unified launcher env semantics and provider-neutral config loading.
- `project_id_normalized` coverage fix on code-tiny writers.
- Provider-neutral `setup_constraints.py`.
- End-to-end acceptance proving no cross-project leak and no skew between the
  two servers.

### Excluded

- Merging the two MCP servers into one.
- Changing raw `project_id` values, symbol IDs, or deterministic point IDs.
- Changing the case-insensitive comparison contract (already shipped).
- Replacing the FalkorDB/Neo4j provider abstraction (owned by the migration
  plan).
- Introducing a cross-server query planner or federated merge layer.
- Rewriting entity extraction models (GLiNER/LangExtract/spaCy).

## Success Criteria

- `resolve_project_targets("Cortex")`, `resolve_project_targets("cortex")`, and
  `resolve_project_targets("CORTEX")` return identical `ProjectTargets` on both
  servers.
- Every project-scoped MCP tool on both servers accepts `project_id` and returns
  the same shard's data regardless of which launcher started the server or
  which env vars are set. A `search_full=true` flag with no `project_id`
  returns data from all projects; the combination `search_full=false` + no
  `project_id` raises `ProjectScopeRequiredError`. `activate_project` is gone.
- Code and doc each have their own graph: `project_id` and
  `f"{project_id}_doc"` respectively.
- Ingesting project A then project B into the same shared doc graph leaves
  entity nodes distinct per project; querying project A never returns project
  B's entities, paragraphs, symbols, or modules.
- The 4 unfixed `_resolve_graph_database(db)` call sites in `unified_mcp.py`
  resolve the graph through the registry from `be_project_id`/`fe_project_id`.
- `language_writer.py` writes `project_id_normalized` on every node and edge
  type it creates; a query filtering on the normalized field returns complete
  results.
- `setup_constraints.py` runs under both FalkorDB and Neo4j, targeting the
  registry-resolved graph.
- `0_reset_all.py --project-id X` deletes only X's data from the shared graph
  and X's Qdrant collection, leaving every other project intact.
- `dev.py mcp start` and `scripts/mcp-lifecycle.py` produce identical env
  semantics for both servers (same `QDRANT_COLLECTION(_DOC)`, same
  `FALKORDB_GRAPH`, same `PROJECT_ID`).
- Focused regression suites on both servers pass; repository-wide suite has no
  new failures; live smoke checks against FalkorDB + Qdrant pass for two
  distinct projects.

## Risks

| Risk | Mitigation |
| --- | --- |
| Doc-tiny entity IDs change shape (now per-project) | **Drop + re-ingest** (per Validation Interview): existing doc graph data is wiped and re-ingested from source docs with `project_id`. No complex backfill/split algorithm. Requires source docs to be available. |
| Existing dev.json sets code+doc to the same graph | Naming rule now requires `doc_graph == "{project_id}_doc"` (separate). dev.json is updated as part of Phase 06. Existing shared-graph data must be re-ingested into the new doc graph. |
| `activate_project` removal breaks existing clients | Breaking change by design (per Validation Interview). Audit harness + external callers in Phase 02/05; migrate them to pass `project_id` or `search_full=true`. Document the contract change in README and CHANGELOG. |
| `setup_constraints.py` FalkorDB branch introduces async constraint failures | Polling + status reporting already specified by the migration plan; this plan reuses that contract. |
| Registry file format diverges from dev.json | Phase 01 documents the format and reuses dev.json as the primary input; no parallel registry file is introduced. |
| Two projects intentionally share an entity name | Per-project entity IDs keep them distinct; cross-project entity queries use `search_full=true`. |
| No registry cache → per-call file read latency | Per Validation Interview: accepted trade-off. Config files are small (single project per file). Revisit with caching only if profiling shows a real bottleneck. |

## Verification Strategy

- Unit tests for `resolve_project_targets`, naming rules, and case-insensitive
  lookup.
- Recording-driver tests proving complete `project_id_normalized` coverage on
  every writer code path.
- Fixture-based ingest of two distinct projects; assert no cross-project leak
  in graph or Qdrant on both servers.
- Contract tests asserting every project-scoped MCP tool resolves via the
  registry and rejects missing/blank `project_id`.
- Launcher env-equivalence test: `dev.py mcp start --dry-run` and
  `mcp-lifecycle.py --dry-run` produce identical env for both servers.
- Provider-neutral `setup_constraints.py` test against recording driver.
- Scoped-reset test: ingest A and B, reset A, assert B unchanged.
- Live FalkorDB + Qdrant smoke for two projects end-to-end.

## Delivery Command

After approval, execute the plan with:

```text
/hi-craft plans/260728-0000-unified-ingest-query-contract/plan.md
```
