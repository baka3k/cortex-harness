# Phase 03: Make Framework Facts Queryable Through MCP

## Context

Ingestion alone is insufficient. The current unified MCP can route Java-family parsers, but its search label lists, semantic expansion defaults, full-stack queries, schema indexes, and metadata do not understand the three new framework graphs.

The Desktop MCP snapshot contains useful Servlet/JSP-specific changes, but it also contains unrelated Living Docs work and regressions. This phase ports only verified framework behavior and adds the missing Spring/MyBatis coverage.

## Requirements

- Preserve all existing parser aliases and tool input validation.
- Route `spring`, `servlet_jsp`, and `mybatis` to the current Java/C++-compatible backend.
- Make framework nodes searchable by name/kind/file/project.
- Make canonical code-to-framework traversal work without direct framework vector collections.
- Support endpoint-to-persistence flows across Spring, Servlet/JSP, and MyBatis.
- Filter inactive Servlet/JSP generations consistently.
- Keep Neo4j and FalkorDB query compatibility explicit.

## Architecture

### Shared framework query registry

Add a small registry under `code-tiny/mcp/` that describes each framework:

- aliases;
- node labels/kinds;
- default traversal relationships;
- searchable properties;
- freshness behavior;
- full-stack role mappings.

Use the registry in the unified router, fast backend search, semantic graph expansion, and tool metadata. Avoid duplicating large literal lists across backends.

### Routing and metadata

Update:

- `PARSER_ALIASES_CPLUS` without removing existing entries;
- unified MCP instructions;
- `list_parsers` extras/fallbacks;
- `activate_project` tool metadata;
- testtool defaults and fixtures.

Framework parser selection changes default relationship behavior, not the underlying backend implementation.

### Search behavior

Extend `search_functions` because it already searches functions, classes, types, namespaces, and packages despite its name. Add optional `framework` and `kinds` filters while preserving old calls.

Search should match:

- existing core labels;
- nodes with an allow-listed framework/kind;
- `name`, `qualified_name`, `file_path`, `path`, `raw_value`, and `resolved_value` as appropriate.

Update full-text schema to include framework labels and properties. Do not rely on the reference snapshot's fallback-only label expansion: if the existing full-text query returns a base-language hit, the fallback may never run and matching framework nodes can be silently omitted.

`search_by_code` should remain focused on actual source code. It may include framework facts only when they contain a real code/SQL body; do not treat every property blob as code.

### Traversal behavior

Framework-aware default relationship sets should include, at minimum:

- common: `SEMANTIC_OF`, `HANDLES`;
- Spring: `DECLARES_QUERY`, `DERIVES_QUERY`, `MANAGES_ENTITY`, `APPLIES_TO`, `PROTECTS`, messaging/event edges;
- Servlet/JSP: `MAPS_TO`, `PASSES_THROUGH`, `FORWARDS_TO`, `READS`, `WRITES`, `RESOLVES_TO`;
- MyBatis: mapper-method/statement binding, include/result-map relations, table/column read-write relations, and Spring bridge relations as defined by the final writer contract.

Keep caller-provided relationship lists authoritative. Apply framework defaults only when none are supplied.

### Full-stack flow behavior

Port the verified reference changes selectively:

- accept both `ApiEndpoint-[:HANDLES]->Controller` and `Controller-[:HANDLES]->ApiEndpoint` where legacy data may use either direction;
- use `ApiEndpoint-[:SEMANTIC_OF]->Function` for Servlet/JSP handlers;
- filter Servlet/JSP endpoints and facts to the active generation.

Extend persistence traversal to match actual emitted labels/edges:

- Spring `Controller`/`Service`/`DataRepository` and repository query facts;
- MyBatis mapper method -> statement -> SQL semantic -> table/column facts;
- do not assume the existing `Repository-[:QUERIES]->Database` path exists if writers emit `DataRepository`, `DECLARES_QUERY`, or table-level edges instead.

### Semantic search strategy

Keep canonical Java/Kotlin Qdrant points as semantic seeds. When `parser_type` is a framework and `expand_graph` is not explicitly disabled, expand through the framework relationship defaults. Return framework nodes in `graph_expansion.results` with their `framework`, `kind`, and resolution status.

Only add direct framework embeddings in a follow-up if test queries cannot reach unanchored facts such as XML-only MyBatis statements.

## Related Files

- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/fastmcp_server.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/tool_metadata.py`
- `code-tiny/mcp/semantic_graph_expansion.py`
- `code-tiny/mcp/services/explore_service.py`
- `code-tiny/scripts/setup_constraints.py`
- `code-tiny/testtool/input_exam/*.json`
- `code-tiny/testtool/tool_defaults.py`
- `tests/test_framework_mcp_routing.py` (new)
- `tests/test_framework_mcp_search.py` (new)
- `tests/test_framework_mcp_flows.py` (new)

## Implementation Steps

1. Introduce and test the framework query registry.
2. Add parser aliases and metadata while pinning the full existing parser set.
3. Extend schema/index setup for framework labels and text fields with a safe migration path for existing full-text indexes.
4. Extend `search_functions` with framework-aware filters and active-generation filtering.
5. Add framework-aware default relationships to subgraph, flow, semantic expansion, and explore paths.
6. Centralize the Servlet/JSP active-generation predicate and apply it to search, lookup, subgraph, endpoint, and full-stack tools.
7. Update endpoint and persistence chain queries based on the final writer graph contract.
8. Run the same query fixtures against Neo4j and FalkorDB; isolate provider-specific query syntax behind helpers.
9. Compare each selected Desktop MCP hunk before porting and exclude Living Docs, alias regressions, and unrelated validation rewrites.

## Todo

- [x] `list_parsers` preserves old aliases and adds all three frameworks.
- [x] Framework name search returns nodes even when base-language hits also exist.
- [x] Semantic search can reach framework nodes through graph expansion.
- [x] XML-only facts have a documented retrieval path.
- [x] Full-stack endpoint flows work for Spring and Servlet/JSP.
- [x] Persistence flows reach MyBatis SQL/table facts.
- [x] Inactive Servlet/JSP generations never leak.
- [ ] Neo4j/FalkorDB compatibility tests cover every new query shape. Query-shape tests pass, but live cross-provider MCP execution awaits the active provider migration.

## Risks

- Expanding default relationship sets too broadly can produce noisy or expensive graph traversals. Use parser-specific allow lists, depth caps, and limits.
- Full-text index definitions cannot always be changed in place. A versioned or explicit recreate migration may be required.
- The current full-stack query assumes labels/edges that differ from the new parser models. Tests must use actual writer output, not hand-written idealized graphs.
- Direct Neo4j bridge code in `unified_mcp.py` overlaps the FalkorDB migration and must not be extended without provider-aware execution.

## Success Criteria

- Existing MCP tests pass unchanged.
- New framework routing/search/flow tests pass against both graph providers where supported.
- Representative natural-language semantic searches return canonical code seeds plus relevant framework neighbors.
- Query results are project-scoped, generation-safe, bounded, and explainable.
