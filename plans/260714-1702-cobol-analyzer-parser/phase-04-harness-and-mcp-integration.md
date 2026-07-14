# Phase 04: Integrate Graph Writes, Incremental Sync, and MCP

## Context

The analyzer becomes usable only when parser ownership, CLI discovery, incremental invalidation, provider-neutral graph writes, schema setup, Qdrant indexing, and unified MCP behavior agree on COBOL identities and relationships.

## Requirements

- Register `cobol` as a primary parser for `.cbl`, `.cob`, `.cpy`, and `.copy`.
- Preserve the established full/incremental analyzer command contract.
- Reanalyze copybook consumers transitively and apply safe file-scoped deletions.
- Write only through `LanguageCodeWriter`/`GraphDriver`; do not add direct Neo4j-only code.
- Use the existing project-scoped code vector collection.
- Make COBOL aliases, labels, properties, and relationships visible through unified MCP.
- Add schema/index changes additively and preserve all existing parser registrations.
- Coordinate shared-file edits with active Flutter and FalkorDB plans.

## Architecture

COBOL is an exclusive primary parser. Program and copybook files share one analyzer but have different semantic roles. A persisted include dependency index maps copybook changes/deletions to affected programs. Analysis stages all affected facts, then applies graph updates and tombstones only after successful completion.

The general code MCP backend receives the `cobol` alias. The existing shared query-profile seam supplies COBOL labels, searchable properties, and default control/data relationships to name search, symbol lookup, subgraph/path traversal, semantic expansion, dependency planning, and impact queries. No new backend or tool family is added.

## Related Files

Modify:

- `code-tiny/tools/cobol/cobol_analyzer.py`
- `code-tiny/tools/cobol/pipeline.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/tools/graph/writer/language_writer.py` only if the existing generic APIs cannot express a required fact
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/fastmcp_server.py` only where registry-driven behavior is insufficient
- `code-tiny/mcp/semantic_graph_expansion.py`
- `code-tiny/scripts/setup_constraints.py`
- `requirements.txt` and `code-tiny/requirements.txt` if runtime constraints changed
- `docs/HARNESS_WORKFLOW.md`

Create:

- `tests/test_dev_cobol_parser_discovery.py`
- `tests/test_incremental_sync_cobol.py`
- `tests/test_cobol_graph_contract.py`
- `tests/test_cobol_mcp_routing.py`
- `tests/test_cobol_mcp_search.py`
- `tests/test_cobol_mcp_flows.py`

## Implementation Steps

1. Add analyzer path, extensions, ownership, source walking, selected-parser validation, and root CLI discovery maps.
2. Extend owner manifests so programs and copybooks remain COBOL-owned while `.jcl` stays outside MVP ownership.
3. Pass parser-library, copybook roots, graph-provider, Qdrant, cache, and incremental manifests through the existing command builder.
4. Persist include dependencies and compute affected program closure for changed/deleted copybooks.
5. Map staged facts into generic writer batches; add only the smallest provider-neutral writer extension if tests prove one is necessary.
6. Add COBOL constraints/indexes/full-text coverage through provider-aware schema setup.
7. Add `cobol` routing plus a shared query profile for labels, properties, and relationships.
8. Verify existing MCP tools return COBOL facts with project scoping and do not globally add COBOL traversal relationships for unrelated parsers.
9. Add end-to-end tests for full sync, one-program change, nested-copybook change, deletion, runtime failure, and repeated idempotent runs.
10. Document setup, grammar override, copybook paths, commands, output schema, and MVP limitations.

## Todo

- [x] Verify `list_parsers` adds `cobol` without dropping any existing alias.
- [x] Verify copybook fan-out invalidation counts and summaries.
- [x] Verify fatal parse/runtime failure leaves previous graph state intact.
- [x] Verify Qdrant uses the existing project-scoped collection and stable IDs.
- [x] Run focused COBOL, framework, graph-provider, and MCP regressions; record pre-existing Flutter fixture/runtime failures as exclusions.

## Risks

- Primary parser maps are duplicated between incremental sync and root CLI discovery.
- Copybook invalidation can become a near-full scan in heavily shared legacy repositories.
- Generic typed relationships currently match nodes by ID; project-scoped stable IDs and uniqueness constraints must prevent cross-project collisions.
- Shared MCP query-profile edits can leak labels/relationships into unrelated parser modes.
- Live provider parity remains blocked until the active migration stabilizes schema and query behavior.

## Success Criteria

- Auto-detection and `--parsers cobol` invoke the correct analyzer for all four extensions.
- Full, incremental, copybook-change, and deletion tests update exactly the affected logical facts with deterministic summaries.
- Graph writes use provider-neutral APIs and remain idempotent.
- Existing MCP tools list, find, inspect, traverse, and plan dependencies for COBOL facts with strict project scope.
- Existing parser aliases, framework/Flutter profiles, and non-COBOL test suites remain intact.
- The MVP gate in `plan.md` is satisfied.
