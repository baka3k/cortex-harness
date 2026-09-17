# Phase 07 — End-to-End Validation and Acceptance

## Goal

Prove the unified contract end-to-end: ingest two distinct projects, query each
through both servers, reset one, and verify zero cross-project leak and zero
skew between servers.

## Deliverables

### Fixture projects

- Two minimal fixture projects under `tests/fixtures/unified_contract/`:
  - `proj_alpha/`: a few source files (code side) + a short doc (doc side).
  - `proj_beta/`: same shape, distinct content, one intentionally shared entity
    name to prove per-project isolation.

### End-to-end test suite

- `tests/test_unified_ingest_query_contract.py`:
  - **Setup (once)**: ingest `proj_alpha` and `proj_beta` through both
    `dev sync code` and `dev sync doc`. Targets resolved via the registry.
  - **graph_mcp queries**: for each project, call `get_project_modules`,
    `semantic_search`, `explore_graph`, and the 4 bridge tools; assert every
    returned node/edge carries the expected `project_id_normalized` and no
    node from the other project appears.
  - **search_full queries (graph_mcp)**: call the same tools with
    `search_full=true` and no `project_id`; assert rows from both `proj_alpha`
    and `proj_beta` appear; assert the `$search_full` Cypher param is `true`.
  - **mind_mcp queries**: for each project, call
    `query_graph_rag_langextract`, `semantic_search`, `list_source_ids`,
    `get_paragraph_text`; assert the same isolation property.
  - **search_full queries (mind_mcp)**: call the same tools with
    `search_full=true` and no `project_id`; assert docs from both projects
    appear in the shared graph.
  - **Shared entity isolation**: the shared entity name appears as two
    distinct Entity nodes (different IDs) in the doc graph; querying one
    project returns only its own entity.
  - **Launcher parity**: ingest via either launcher; query via either
    launcher; results identical.
  - **Scoped reset**: `0_reset_all.py --project-id proj_alpha` and the code
    equivalent delete only alpha's graph nodes and Qdrant points; beta
    unchanged.
  - **Capability gates**: gates consult registry-resolved graph; unsupported
    dimensions return `capability_unavailable` honestly.

### Live smoke

- A `scripts/smoke_unified_contract.py` that runs against live FalkorDB +
  Qdrant for two real projects (e.g. `cortext` and a second registered
  project). Prints a pass/fail matrix. Skips gracefully when services are
  unavailable.

### Documentation

- `docs/unified_ingest_query_contract.md`:
  - Naming contract table.
  - Registry input format (reuses dev.json).
  - Per-server query/ingest flow diagrams.
  - Launcher env contract.
  - Migration / backfill playbook for existing data.

### Regression

- Full `code-tiny` and `doc-tiny` test suites pass with no new failures.
- Live FalkorDB + Qdrant smoke passes for two distinct projects.

## Out of Scope

- Performance benchmarking (separate concern).
- Federated cross-project queries (explicit non-goal).

## Acceptance

- All tests in the new suite pass.
- Existing repository-wide suite has no new failures attributable to this
  plan.
- Live smoke for two real projects returns all-green.
- Documentation reviewed and accurate.
