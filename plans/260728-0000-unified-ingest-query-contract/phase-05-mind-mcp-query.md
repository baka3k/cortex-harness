# Phase 05 — mind_mcp Stateless Query Path

## Goal

Make every `mind_mcp` tool accept `project_id` per call and resolve graph +
Qdrant collection through the registry, mirroring Phase 02 on the doc side.
Add the same `search_full` escape hatch so callers can opt into a
cross-project doc query.

## Query precedence

Same 3-step precedence as Phase 02 (no `active_project` state):

1. `project_id` present → resolve via registry, apply
   `qdrant_project_filter(project_id)` and graph predicate.
2. `project_id` absent + `search_full=true` → no project filter; query the
   active graph/collection as-is.
3. `project_id` absent + `search_full=false` (default) →
   `ProjectScopeRequiredError`.

## Deliverables

- `doc-tiny/mcp_graph_rag.py`:
  - Remove the module-level `QDRANT_COLLECTION = os.getenv(...)` default; the
    collection is resolved per call via the registry.
  - `query_graph_rag_langextract`, `semantic_search`, `list_source_ids`,
    `get_paragraph_text`, `list_qdrant_collections` all accept `project_id`
    **and** `search_full` (default `false`).
  - The Qdrant filter becomes
    `qdrant_project_filter(project_id, search_full=search_full)` (reuses the
    extended `project_scope.py` from Phase 02): when `search_full=True` the
    filter returns `None`; otherwise it is the project_id_normalized match
    combined with the existing `source_id` filter.
  - The graph target is resolved via
    `resolve_project_targets(project_id).doc_graph` on each call instead of
    being fixed at server boot. When `search_full=True` and no `project_id`,
    fall back to the server's boot graph (same as today) so a cross-project
    query against a shared doc graph still works.
  - Explicit `collection` arg still wins as escape hatch.
  - A missing `project_id` (with `search_full=false`) raises
    `ProjectScopeRequiredError`, mirroring Phase 02. No `active_project` state.
- `doc-tiny/graph_store.py`:
  - `create_graph_store_for_project(project_id)` factory that builds a store
    pointing at the registry-resolved graph. The MCP server calls this per
    request (driver pooling is fine; the graph name is per call).
- Tests:
  - Contract test: every tool raises `ProjectScopeRequiredError` when
    `search_full=false` and `project_id` is absent (no stateful default).
  - `search_full=true` + no `project_id` returns docs from both `projA` and
    `projB` sharing one doc graph.
  - Two-project test: ingest docs for A and B into separate doc graphs; query A
    (`search_full=false`) returns only A's paragraphs/entities; query B
    returns only B's.
  - `source_id` filter still works in combination with `project_id` and with
    `search_full`.

## Out of Scope

- Launcher env semantics (Phase 06).
- Doc ingest path changes (Phase 04).

## Acceptance

- `query_graph_rag_langextract(project_id="cortex", query="...")` resolves
  graph `cortext` and collection `cortext_doc` regardless of server-boot env.
- Querying project A never returns project B's entities or paragraphs.
- All doc-tiny regression tests pass (with fixtures updated to provide
  `project_id`).
