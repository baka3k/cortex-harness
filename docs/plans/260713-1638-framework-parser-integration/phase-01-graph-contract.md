# Phase 01: Complete the Graph Integration Contract

## Context

The new analyzers contain substantial parsing and graph-row construction logic, but their runtime integration layer is incomplete in this checkout. Import smoke tests fail before parser execution because provider helpers and three graph writers are missing.

This phase establishes a provider-neutral, testable graph contract before any analyzer is added to the main scan loop.

## Requirements

- Restore or implement the shared provider runtime expected by all three analyzers.
- Export the provider argument compatibility helpers expected by their imports.
- Implement one writer per framework using the existing `GraphDriver` abstraction.
- Preserve the canonical Java/Kotlin ownership model.
- Support Neo4j and FalkorDB without analyzer-specific direct drivers.
- Define stable node labels, IDs, relationship directions, cleanup behavior, and Servlet/JSP generation semantics.

## Architecture

### Provider runtime

Reconcile the analyzer imports with the existing `tools.graph.cli` API. Prefer one canonical provider-argument module and compatibility exports over parallel implementations.

The final contract should expose:

- graph provider CLI arguments;
- Neo4j compatibility requirement arguments;
- normalized provider selection;
- normalized Neo4j/FalkorDB connection fields;
- selected graph/database name;
- a single driver creation path.

If `provider_runtime.py` is retained, `tools.graph.cli` should delegate to it or vice versa; avoid two sources of truth.

### Writer responsibilities

`MyBatisFactWriter` and `SpringFactWriter` should provide:

- batched node upsert by `id`/`symbol_id` and framework label;
- batched relationship upsert using allow-listed relationship types;
- deletion/cleanup by `project_id` and source file;
- deterministic counts for created/updated/deleted facts;
- provider-neutral query parameters and result handling.

`ServletJspFactWriter` additionally needs:

- active module listing;
- active generation lookup;
- staged generation writes;
- atomic generation promotion;
- inactive generation cleanup;
- tombstone generation support;
- rollback behavior when staging or promotion fails.

### Identity and label contract

- Canonical language nodes remain owned by Java/Kotlin analyzers.
- Framework facts use their model `stable_id` and a label derived from the allow-listed `kind`.
- `SEMANTIC_OF` targets canonical `Function`/`Class` IDs without duplicating those nodes.
- Spring `ApiEndpoint`, `Controller`, `Service`, and `DataRepository` labels reuse the existing full-stack vocabulary where their semantics match.
- MyBatis persistence nodes retain explicit labels such as `MyBatisStatement`, `DatabaseTable`, and `DatabaseColumn`; do not collapse all facts into a generic label.
- All framework nodes carry `project_id`, `framework`, `file_path`, `parser_version`, and source span fields.
- Servlet/JSP generation-scoped nodes additionally carry `module_id`, `generation_id`, and `semantic_id`.

## Related Files

- `code-tiny/tools/graph/__init__.py`
- `code-tiny/tools/graph/cli.py`
- `code-tiny/tools/graph/core/provider_runtime.py` (missing)
- `code-tiny/tools/graph/core/factory.py`
- `code-tiny/tools/graph/writer/__init__.py`
- `code-tiny/tools/graph/writer/mybatis_writer.py` (missing)
- `code-tiny/tools/graph/writer/servlet_jsp_writer.py` (missing)
- `code-tiny/tools/graph/writer/spring_writer.py` (missing)
- `code-tiny/tools/mybatis/models.py`
- `code-tiny/tools/servlet_jsp/models.py`
- `code-tiny/tools/spring/models.py`
- `code-tiny/scripts/setup_constraints.py`

## Implementation Steps

1. Inventory the expected writer method calls from each analyzer and turn them into explicit interfaces/tests.
2. Decide whether `provider_runtime.py` or `tools.graph.cli` is canonical; add compatibility exports without duplicating normalization logic.
3. Add import smoke tests for the three analyzer entrypoints.
4. Implement framework writer node/relationship allow lists and batch APIs.
5. Implement project/file cleanup for Spring and MyBatis.
6. Implement Servlet/JSP generation state, stage, promote, tombstone, and cleanup transactions.
7. Add Neo4j and FalkorDB indexes/constraints for framework IDs, project scoping, endpoints, tables, and generation state.
8. Add provider-parity unit tests using fake drivers first, then integration tests when services are available.

## Todo

- [x] Analyzer imports succeed.
- [x] Writer API tests pin the analyzer-facing write, cleanup, stage, and promote contract.
- [x] Relationship types are allow-listed and safely interpolated.
- [x] Canonical language nodes are never overwritten by overlay cleanup.
- [x] Servlet/JSP promotion is atomic or fails closed.
- [ ] Schema setup is idempotent for both providers. Neo4j definitions are versioned/idempotent; FalkorDB schema CLI support remains part of the active provider migration.

## Risks

- The current active FalkorDB migration changes the same provider interfaces and schema setup files. Implementing before that contract stabilizes could cause duplicate work.
- Dynamic labels and relationship types can create Cypher-injection risks unless they are allow-listed.
- A cleanup query scoped only by file path could remove facts from another project or canonical language analyzer.
- Servlet/JSP generation promotion can expose partial data if state and node writes are not committed consistently.

## Success Criteria

- `python -c` imports for all three analyzers succeed.
- Fake-driver tests prove expected queries, parameters, batches, and cleanup scoping.
- Provider integration tests write the same logical graph shape in Neo4j and FalkorDB.
- Failed Servlet/JSP staging leaves the previously active generation queryable.
