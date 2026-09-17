# Phase 06 — Unified Launcher and Config Loader

## Goal

Eliminate the launcher env skew between `dev.py mcp start` and
`scripts/mcp-lifecycle.py`. Make the config loader provider-neutral and
registry-aware so both servers boot with identical targeting semantics.

## Deliverables

### Launcher alignment

- `cortex_harness/dev.py` `_doc_env_for_process`:
  - Set `QDRANT_COLLECTION_DOC` from
    `resolve_project_targets(project_id).doc_qdrant_collection` (i.e.
    `"{project_id}_doc"`).
  - Set `QDRANT_COLLECTION` from `targets.code_qdrant_collection` on the code
    server env.
  - Set `FALKORDB_GRAPH` / `NEO4J_DB` from `targets.code_graph` on the code
    server env.
  - Set `FALKORDB_GRAPH` / `NEO4J_DB` from `targets.doc_graph` (i.e.
    `"{project_id}_doc"`) on the doc server env — **separate graph from code**
    per Validation Interview.
  - Set `PROJECT_ID` on both server envs.
- Update `.cortext-harness/config/dev.json`: change `doc.env.FALKORDB_GRAPH`
  from `"cortext"` to `"cortext_doc"` to match the naming rule.
- `scripts/mcp-lifecycle.py`:
  - Already sets most of these correctly; align any remaining differences and
    become the canonical implementation. `dev.py mcp start` should produce
    identical env for the same project.
- Add a launcher env-equivalence test: `dev.py mcp start --dry-run --project
  cortex` and `mcp-lifecycle.py start --dry-run --project cortex` produce
  identical env dicts for both servers.

### Config loader unification

- `code-tiny/tools/common/harness_config.py` `load_harness_config()`:
  - Read both `code` and `doc` sections (currently only reads `code`).
  - Set `FALKORDB_GRAPH`/`FALKORDB_DATABASE` when provider is FalkorDB (not
    only `NEO4J_*`).
  - Expose `load_harness_config(project_id) -> HarnessConfig` that consults the
    registry for graph/collection names.
- `doc-tiny/enviroment_loader.py` (legacy):
  - Either deprecate in favor of the shared loader, or align env-var names
    (`NEO4J_USERNAME` → `NEO4J_USER`, `QDRANT_KEY` → `QDRANT_API_KEY`) and add
    FalkorDB support.
  - Remove credential stdout logging.
- Document the canonical env-var contract in `docs/`:
  - `PROJECT_ID`, `GRAPH_PROVIDER`, `FALKORDB_GRAPH`, `FALKORDB_DATABASE`,
    `NEO4J_DB` (alias), `QDRANT_COLLECTION` (code), `QDRANT_COLLECTION_DOC`
    (doc).

### dev.py doc collection override

- `dev.py:833` currently overrides doc collection to `project["name"]`. Change
  to use `targets.doc_qdrant_collection` (i.e. `"{project_id}_doc"`). This
  removes the triple-mismatch between ingest, config, and MCP server defaults.

### Tests

- Launcher env-equivalence test (above).
- `load_harness_config("cortex")` returns both code and doc sections with
  registry-derived names.
- Doc ingest via `dev sync doc --project cortex` writes to collection
  `cortext_doc`, matching what `mind_mcp` will query.

## Out of Scope

- Deprecating `dev.py mcp start` entirely (keep both launchers, aligned).
- Changing provider selection logic (owned by the migration plan).

## Acceptance

- `dev.py mcp start --project cortex` and `scripts/mcp-lifecycle.py start
  --project cortex` produce byte-identical env dicts for both servers.
- Doc ingest via `dev sync doc` and doc query via `mind_mcp` target the same
  Qdrant collection for a given project.
- No env-var name conflict remains between code and doc config loaders.
