# Phase 03 — graph_mcp Ingest Normalization and Provider-Neutral Schema

## Goal

Route code ingest through `resolve_project_targets` so the ingest path and
query path can never disagree on the target graph or collection. Close the
`project_id_normalized` coverage gap on `language_writer.py`. Make
`setup_constraints.py` provider-neutral and registry-aware.

## Deliverables

### Ingest routing

- `code-tiny/tools/sync/incremental_sync.py`:
  - Resolve `targets = resolve_project_targets(args.project_id)` at startup.
  - Derive `--falkordb-graph` default from `targets.code_graph` (CLI override
    still wins).
  - Derive `--qdrant-collection` default from `targets.code_qdrant_collection`.
  - Remove the `_code_collection_name` heuristic branch when a registered
    project is detected; keep it only as a fallback for ad-hoc projects.
- `cortex_harness/dev.py` `sync_code`: pass `--project-id` through to
  `incremental_sync.py` and let the registry resolve the rest, instead of
  overriding collection names ad hoc.
- `code-tiny/tools/graph/writer/language_writer.py` and
  `project_topology_writer.py`: receive `project_id` via row payloads (already
  true for topology); no signature changes needed.

### project_id_normalized coverage fix

- `language_writer.py`: add `project_id_normalized` to every MERGE that
  currently sets only `project_id`. Concretely these writers (per research):
  - `write_function_types` (~440)
  - `write_fields` (~480)
  - `write_aliases` (~520)
  - `write_templates` (~560)
- `write_calls` (~390): add both `project_id` and `project_id_normalized` to
  the CALLS edge properties (currently neither is set). The value comes from
  the caller's row payload; if missing, log a warning and skip the normalized
  field rather than crashing.
- Backfill script: `code-tiny/scripts/backfill_project_scope_keys.py` (already
  exists per research) extended to also backfill Field/Alias/Template/
  FunctionType nodes and CALLS edges. Add a `--dry-run` mode reporting counts.

### Provider-neutral schema setup

- `code-tiny/scripts/setup_constraints.py`:
  - Add FalkorDB branch: when provider is FalkorDB, use `FalkorDBDriver` and
    the FalkorDB-compatible `CREATE CONSTRAINT`/range-index syntax.
  - Default `--graph` (new arg name) to `targets.code_graph` resolved via
    registry from `--project-id`. Keep `--neo4j-db` as a deprecated alias.
  - Report asynchronous constraint failures with a re-check polling step
    (contract owned by the migration plan).
  - Idempotent under both providers.

### Tests

- Recording-driver tests asserting every writer call site emits
  `project_id_normalized` on nodes and edges.
- `backfill_project_scope_keys.py --dry-run` against a fixture graph reports
  the expected missing-field counts; `--apply` fills them.
- `setup_constraints.py` runs against a recording driver for both providers;
  the FalkorDB branch issues the documented FalkorDB DDL.
- Incremental ingest of two fixture projects routes to two distinct graphs /
  collections via the registry.

## Out of Scope

- Doc ingest (Phase 04).
- Query path wiring (Phase 02).

## Acceptance

- After ingest, a query filtering on `project_id_normalized` returns the same
  node count as a query filtering on raw `project_id`, for every node label
  and edge type written by `language_writer.py`.
- `setup_constraints.py --project-id cortex --provider falkordb` targets the
  `cortext` graph and succeeds.
- No existing incremental-sync or writer test regresses.
