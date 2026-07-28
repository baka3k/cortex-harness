# Phase 04 — mind_mcp project_id Introduction

## Goal

Give `doc-tiny` a first-class `project_id` concept so it can participate in the
unified contract. Every Document, Paragraph, and Entity node and every Qdrant
payload carries `project_id` + `project_id_normalized`. Entities merge
per-project rather than globally.

## Deliverables

### Ingest changes

- `doc-tiny/graphrag_ingest_langextract.py`:
  - Add `--project-id` CLI arg (required for the unified contract; optional
    with a deprecation warning when absent, defaulting to `source_id`).
  - Write `project_id` and `project_id_normalized` on:
    - `Document` nodes (alongside `id`)
    - `Paragraph` nodes (alongside `source_id`, `paragraph_id`)
    - `Entity` nodes (alongside `id`, `type`, `name`)
    - `HAS_ENTITY` and `HAS_PARAGRAPH` relationships (as relationship properties
      where supported by the provider)
  - Change entity ID from `uuid5(ent_type::name_norm)` to
    `uuid5(project_id_normalized::ent_type::name_norm)` so entities from two
    projects sharing one doc graph stay distinct.
  - Derive `--collection` default from
    `resolve_project_targets(project_id).doc_qdrant_collection` instead of the
    `"graphrag_entities"` literal.
  - Qdrant payload adds `project_id` and `project_id_normalized` fields.
- `doc-tiny/graph_store.py`:
  - Add a `project_id` param to the write path so the store can stamp nodes.
  - Default graph name resolution via the registry; keep env override as escape
    hatch.

### Backfill strategy — Drop + Re-ingest

Per Validation Interview: existing doc graph data is **dropped and re-ingested**
rather than migrated in place. This avoids the complex entity-split algorithm
(relinking HAS_ENTITY/RELATED edges across projects) that an in-place backfill
would require.

- `doc-tiny/0_reset_all.py` wipes the old doc graph + doc Qdrant collection.
- Source docs are re-ingested via `graphrag_ingest_langextract.py --project-id
  <id>` which stamps `project_id` + `project_id_normalized` on every node and
  payload.
- Requirement: source docs must still be available on disk. If they are not,
  the old data is lost — document this in the migration playbook.

### Reset

- `doc-tiny/0_reset_all.py`:
  - Add `--project-id` arg.
  - When set: delete only nodes with matching `project_id_normalized` (graph)
    and only the project's Qdrant collection. When absent: preserve current
    whole-graph behavior with a deprecation warning.

### Tests

- Ingest two distinct projects' docs into a shared graph; assert Entity nodes
  for a same-named entity stay distinct per project.
- Qdrant payload schema test: every point has `project_id` and
  `project_id_normalized`.
- `0_reset_all.py --project-id A` after ingesting A and B leaves B's nodes and
  points intact.

## Out of Scope

- `mcp_graph_rag.py` query path wiring (Phase 05).
- Changing embedding models or entity extractors.

## Acceptance

- After ingest, `MATCH (p:Paragraph) WHERE p.project_id_normalized IS NULL
  RETURN count(p)` returns 0.
- Two projects with a same-named entity produce two Entity nodes with distinct
  IDs.
- `0_reset_all.py --project-id A` deletes only A's data.
