---

# Unified Ingest / Query Contract

This document is the authoritative reference for the unified contract
between `graph_mcp` (code-tiny, port 8788) and `mind_mcp` (doc-tiny, port
8789). It supersedes the per-server defaults that lived in scattered env
vars and module-level constants before this plan landed.

---

## Goals

1. **`project_id` is the only key a caller needs to pass per call.**
2. **Each project owns its graph shard and its Qdrant collection(s) by
   convention.**
3. **A query against project X returns X's data and only X's data — on
   both servers, regardless of launcher.**
4. **Ingest and reset are scoped per project and never leak across
   projects.**
5. **The contract is the same on both servers; only the node labels and
   vector payloads differ.**

---

## Naming Contract

| Concept | Rule |
| --- | --- |
| `project_id` raw | Preserved as identity/display. Canonicalised to the registered form when a config entry matches, so case variants of the same project produce identical `ProjectTargets`. |
| `project_id_normalized` | `str(value).strip().casefold()`. Comparison key everywhere. |
| Code graph | `== project_id` |
| Code Qdrant collection | `== project_id` |
| Doc graph | `== f"{project_id}_doc"` (separate graph; disjoint labels) |
| Doc Qdrant collection | `== f"{project_id}_doc"` |
| Point IDs / symbol IDs | Unchanged — raw `project_id` stays inside identity. |

The naming rule is applied when a config entry omits the field. Config-file
values always win.

---

## Project Registry

The `ProjectRegistry` is the single source of truth. It lives in
`code-tiny/tools/common/project_registry.py` (the code side) and is
mirrored as `doc-tiny/project_contract.py` (the doc side — kept separate
because doc-tiny is laid out as flat scripts).

### Input format

`.cortext-harness/config/*.json`, one file per project. The loader reuses
the existing `dev.json` shape:

```json
{
  "active": true,
  "project": {"code": "cortext", "name": "cortext"},
  "code": {"env": {"FALKORDB_GRAPH": "cortext", "QDRANT_COLLECTION": "cortext"}},
  "doc":  {"env": {"FALKORDB_GRAPH": "cortext_doc", "QDRANT_COLLECTION": "cortext_doc"}}
}
```

* `project.code` is the registry key. It becomes the canonical raw
  `project_id` returned by `resolve_project_targets`.
* `code.env.FALKORDB_GRAPH` and `doc.env.FALKORDB_GRAPH` map directly to
  the graph fields. Omitting them triggers the naming rule.
* `code.env.QDRANT_COLLECTION` and `doc.env.QDRANT_COLLECTION` map
  directly to the Qdrant collection fields.
* `code.env.GRAPH_PROVIDER` selects the backend (`"falkordb"` or `"neo4j"`).

The registry is re-read on every call. There is no in-process cache
(accepted trade-off; revisit only if profiling shows a real bottleneck).

### Public API

```python
from tools.common.project_registry import (
    DuplicateProjectRegistrationError,
    ProjectTargets,
    ProjectNotRegisteredError,
    list_registered_projects,
    resolve_project_targets,
    with_overrides,
)

targets = resolve_project_targets("cortex")
# ProjectTargets(
#     project_id='cortex', project_id_normalized='cortex',
#     code_graph='cortex', code_qdrant_collection='cortex',
#     doc_graph='cortex_doc', doc_qdrant_collection='cortex_doc',
#     parser_type=None, provider='falkordb',
#     source='registry'
# )
```

---

## Query Precedence (both servers)

Every project-scoped tool follows this amended precedence:

1. **`project_id` present** → resolve the shard via the registry and
   filter on `project_id_normalized`.
2. **`project_id` absent** → query the env-default graph/collection with no
   `project_id_normalized` filter (implicit full search).

## `parser_type` Precedence (unified MCP, fan-out tools)

Fan-out search tools (`search_functions`, `search_by_code`, `get_symbol`,
`get_node_details`, `query_subgraph`, `find_paths`,
`find_path_between_module`, `listup_symbols_matching_file_path`,
`listup_class_matching_path`, `list_up_entrypoint`, `trace_flow`,
`trace_flow_between_module`, `list_possible_calls`) follow this
precedence:

1. **`parser_type` present** → dispatch to the single matching query
   engine / profile (no fan-out merge).
2. **`parser_type` absent** → engine-level fan-out (one dispatch per
   physical backend, capped at `len(BACKENDS)`); per-engine payloads
   carry no `parser_type` key and the backend selects the union of
   every profile label mapped to that engine. Merged results
   deduplicate by node id, edge composite key, and raw id list
   membership; `parsers_searched` lists the engine representatives.
3. **Per-engine `limit` is preserved** — the merge does not re-slice
   merged lists, so a parser-less `search_functions(limit=50)` may
   return up to 100 pre-dedup items.

Code-side query builders include the normalized predicate only for scoped
calls:

```cypher
AND n.project_id_normalized = $project_id_normalized
```

For Qdrant filters, `qdrant_project_filter(project_id)` returns `None` when
the id is absent and the normalized project predicate otherwise. Filters
compose with `source_id` filters via AND.

---

## Ingest Flow

### Code ingest (`dev sync code --project <id>`)

```
code-tiny/tools/sync/incremental_sync.py
  → resolve_project_targets(args.project_id)
  → args.falkordb_graph    = targets.code_graph
  → args.qdrant_collection = targets.code_qdrant_collection
  → driver writes:
      Function / Field / Alias / Template / FunctionType / Namespace / Package / File
      with project_id + project_id_normalized on every node
      CALLS edges carry project_id + project_id_normalized
```

### Doc ingest (`dev sync doc --project <id>`)

```
doc-tiny/graphrag_ingest_langextract.py --project-id <id>
  → project_id_normalized = (project_id or source_id).casefold()
  → entity merge key = "{project_id_normalized}::{ent_type}::{name}"
  → driver writes:
      Document + Paragraph + Entity nodes
      HAS_PARAGRAPH + HAS_ENTITY + RELATED edges
      all stamped with project_id + project_id_normalized
```

When `--project-id` is absent the loader falls back to `--source-id` and
emits a deprecation warning so callers know to migrate.

---

## Backfill / Reset

Per the plan's risk row: existing doc graph data is **dropped and
re-ingested** rather than migrated in place. This avoids the entity-split
algorithm that an in-place backfill would require.

```
# Wipe everything (legacy behavior, kept with deprecation warning)
python doc-tiny/0_reset_all.py

# Per-project reset
python doc-tiny/0_reset_all.py --project-id cortex
```

The per-project form deletes only nodes with matching
`project_id_normalized` from the shared graph and only the project's
Qdrant collection. Other projects remain intact.

The same per-project reset applies to the code side via
`code-tiny/tools/sync/incremental_sync.py` (its reset path is unchanged;
the new behavior lives at the ingest level).

---

## Launcher Contract

Both `cortex_harness/dev.py mcp start` and `scripts/mcp-lifecycle.py start`
produce byte-identical env dicts for the same project:

```python
{
    "FALKORDB_URI": "...",
    "FALKORDB_GRAPH": "<code_graph for project>",
    "FALKORDB_GRAPH_DOC": "<doc_graph for project>",
    "QDRANT_COLLECTION": "<code_qdrant_collection for project>",
    "QDRANT_COLLECTION_DOC": "<doc_qdrant_collection for project>",
    "NEO4J_DB": "<alias of FALKORDB_GRAPH>",
    "GRAPH_PROVIDER": "falkordb",
    "PROJECT_ID": "<project_id>",
}
```

`PROJECT_ID` is set on both server envs so that the MCP servers can stamp
every node with the right `project_id_normalized` even before the first
call.

---

## Removed / Deprecated

| Before | After |
| --- | --- |
| `activate_project` tool | Removed entirely. Callers must pass `project_id` and `parser_type` explicitly on every project-scoped call. |
| `active_project` module-level dict | Removed. No stateful default. |
| Module-level `DEFAULT_GRAPH_DB` env reads | Replaced by registry resolution. |
| `QDRANT_COLLECTION_DOC` not set by `dev.py mcp start` | Now set from `targets.doc_qdrant_collection`. |
| `doc.env.FALKORDB_GRAPH == project_id` (sharing code graph) | Renamed to `{project_id}_doc` per the naming rule. |
| Entity merge key `uuid5(ent_type::name)` | `uuid5(project_id_normalized::ent_type::name)` so two projects sharing one doc graph stay distinct. |

---

## Smoke Test

`scripts/smoke_unified_contract.py` is a deterministic registry preflight: it
fails when either project is unregistered or their graph/collection targets
collide. The automated Phase 07 fixture suite covers project-scoped document
payloads, queries, resets, and launcher configuration without external
services.

```bash
python scripts/smoke_unified_contract.py --project-a cortext --project-b proj_beta
```

The expected output for two registered projects is:

```
--- Two-project isolation ---
         default  PASS
```

The registry refuses to resolve an unknown project:

```
SKIP (registry: project_id 'proj_beta' is not registered. ...)
```

---

## Migration / Backfill Playbook

1. **Inspect, then drop + re-ingest doc data.** Existing doc data was written
   without `project_id`; run `python doc-tiny/0_reset_all.py --project-id <id>
   --dry-run`, then repeat with `--force` to delete only that project's data.
2. **Re-ingest with the new ingest path.** Each registered project must
   be re-ingested via
   `python doc-tiny/graphrag_ingest_langextract.py --project-id <id>`
   so that every node and Qdrant payload gets the
   `project_id_normalized` stamp.
3. **Run `setup_constraints.py` with the registry-resolved graph.**
   When the migration plan lands the FalkorDB branch, the command is
   `python code-tiny/scripts/setup_constraints.py --project-id <id>
   --provider falkordb`. Per-project indexes replace the global ones.
4. **Update external callers.** Every harness script or skill that previously
   called `activate_project(...)` passes `project_id` to scope one project, or
   omits it for an intentional cross-project query.

---

## References

* `docs/PROJECT_REGISTRY.md` — registry + naming contract details.
* `plans/260728-0000-unified-ingest-query-contract/plan.md` — full plan
  with research, scope, and risk analysis.
* `plans/neo4j-to-falkordb-migration/plan.md` — the parallel migration
  plan that owns the FalkorDB driver, schema setup, and provider-neutral
  contract.
