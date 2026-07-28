# Phase 01 — ProjectRegistry and Naming Contract

## Goal

Introduce one source of truth that maps `project_id` to all storage targets
(code graph, code Qdrant collection, doc graph, doc Qdrant collection, parser
type, provider). Both MCP servers, both launchers, both ingest entrypoints,
and both reset scripts call this resolver instead of deriving names
independently.

## Deliverables

- New `code-tiny/tools/common/project_registry.py` with:
  - `ProjectTargets` frozen dataclass (fields listed in plan.md).
  - `resolve_project_targets(project_id) -> ProjectTargets`.
  - `list_registered_projects() -> list[str]`.
  - Default naming rules applied when config omits a field.
  - Case-insensitive lookup via `project_id_lookup_key` (reuses
    `project_scope.py`).
- Registry input: `.cortext-harness/config/*.json`. The loader reads the
  `project.code`, `code.env`, and `doc.env` sections already present in
  `dev.json`. No new parallel registry file format.
- `resolve_project_targets` also accepts an optional `provider` override and
  per-field overrides for ad-hoc projects not present in config.
- **No caching**: the resolver reads `.cortext-harness/config/*.json` on every
  call. Accepted trade-off (per Validation Interview) — config files are small
  and this avoids stale-state bugs. Revisit with caching only if profiling
  shows a real bottleneck.
- Default naming rules applied when config omits a field:
  - `code_graph == project_id`
  - `code_qdrant_collection == project_id`
  - `doc_graph == f"{project_id}_doc"` (separate from code)
  - `doc_qdrant_collection == f"{project_id}_doc"`
- New `code-tiny/tools/common/test_project_registry.py`:
  - Case-insensitive lookup (`HIEP`, `hiep`, `hiEp` → same targets).
  - Default naming rule applied for missing fields.
  - Explicit config values override defaults.
  - Unknown project without any config + no env → `ProjectNotRegisteredError`
    with a helpful message listing registered projects.
  - Two distinct projects return distinct graph and collection names.
- Documentation section in `docs/` describing the naming contract table from
  `plan.md`.

## Out of Scope

- Changing `project_scope.py` comparison primitives (already shipped).
- Calling the registry from anywhere outside Phase 01 — wiring happens in
  Phases 02-06.

## Acceptance

- `resolve_project_targets("cortex")` against the current `dev.json` returns
  `code_graph="cortext"`, `code_qdrant_collection="cortext"`,
  `doc_graph="cortext_doc"`, `doc_qdrant_collection="cortext_doc"`.
  (Note: current `dev.json` has `doc.env.FALKORDB_GRAPH="cortext"` — Phase 06
  will update this to `"cortext_doc"`. The registry applies the naming rule
  regardless of stale config.)
- All registry unit tests pass.
- No existing tests regress.
