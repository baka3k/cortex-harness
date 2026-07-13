# Framework Parser Scan and MCP Integration Plan

status: complete-with-exclusions
created: 2026-07-13
completed: 2026-07-13
mode: hi-plan --fast
scope: code-tiny framework analyzers, incremental scan orchestration, graph schema, unified MCP
blockedBy: [neo4j-to-falkordb-migration]
dependencyOverride: user approved implementation before the migration completed

## Overview

Integrate the newly added `mybatis`, `servlet_jsp`, and `spring` analyzers into the normal `dev sync code` flow and make their graph facts discoverable and traversable through the unified MCP.

The recommended model is **base-language ownership plus framework overlays**:

1. Java/Kotlin remains responsible for canonical `File`, `Class`, `Function`, and `CALLS` nodes.
2. Spring, Servlet/JSP, and MyBatis run afterward as non-exclusive enrichers over the same project changes.
3. Framework facts link back to canonical language symbols through stable IDs and semantic relationships such as `SEMANTIC_OF`.
4. MCP search and traversal understand framework labels, relationships, and Servlet/JSP generation freshness without replacing the existing Java/C++ backend.

This avoids assigning a `.java`, `.kt`, or `.xml` file to only one parser, which would be incompatible with the current single-owner routing in `incremental_sync.py` and `owner_manifest.py`.

## Current Findings

### Parser packages

- The three parser directories in `code-tiny/tools/` are byte-equivalent to the copies under `/Users/hieplq1.rpm/Desktop/testnewmcp`, excluding generated `__pycache__` directories.
- Each analyzer already exposes incremental manifest arguments:
  - `--incremental`
  - `--changed-files-manifest`
  - `--deleted-files-manifest`
- The analyzers are not runnable in the current checkout. All three fail import because these shared integration files or exports are missing:
  - `code-tiny/tools/graph/core/provider_runtime.py`
  - `code-tiny/tools/graph/writer/mybatis_writer.py`
  - `code-tiny/tools/graph/writer/servlet_jsp_writer.py`
  - `code-tiny/tools/graph/writer/spring_writer.py`
  - `add_require_neo4j_argument` and `resolve_require_neo4j` exports from `tools.graph`
- The analyzers accept Qdrant-related arguments, but the inspected execution paths do not write framework facts to Qdrant. Servlet/JSP explicitly documents that it creates no separate vector collection.

### Main scan flow

- `dev sync code` and `dev sync code all` invoke `code-tiny/tools/sync/incremental_sync.py`.
- `incremental_sync.py` has an `ANALYZERS` registry that does not include the three new frameworks.
- `_select_parser_for_path()` returns exactly one parser per file. Java/Kotlin/XML are therefore currently modeled as exclusive owners.
- `owner_manifest.py` uses the same exclusive ownership model and does not list the three frameworks in `SUPPORTED_PARSERS`.
- `cortex_harness/dev.py` has a second `LANG_ANALYZERS` map used for CLI discovery/status output and legacy helpers; it also lacks the new frameworks.

### MCP and graph query flow

- `unified_mcp.py` routes Android aliases to the Android backend and Java/JVM-family aliases to the C++ backend. It does not declare `mybatis`, `servlet_jsp`, or `spring` aliases.
- `fastmcp_server.py::search_functions` only searches core language labels. Framework facts will therefore be invisible to name search even after ingestion.
- `semantic_graph_expansion.py` defaults to `CALLS`, `USES_TYPE`, `REFERENCES`, and `INHERITS`; it cannot reach framework overlays through `SEMANTIC_OF`, `HANDLES`, MyBatis binding edges, or Spring persistence edges unless callers explicitly provide custom relationships.
- The full-text indexes in `scripts/setup_constraints.py` include several full-stack labels, but do not cover most new Spring, Servlet/JSP, or MyBatis labels/properties.
- Servlet/JSP uses generation-scoped facts and `ServletJspAnalysisState`. Queries must filter inactive generations or they can return stale nodes.

### Reference MCP changes

The files in `/Users/hieplq1.rpm/Desktop/testnewmcp/mcp` are useful as a **patch source**, not as full-file replacements:

- Useful changes:
  - add Servlet/JSP labels to `search_functions`;
  - filter Servlet/JSP nodes by active generation;
  - support both directions of `HANDLES` and the `ApiEndpoint-[:SEMANTIC_OF]->Function` bridge in full-stack chains.
- Incomplete for this task:
  - no comparable Spring or MyBatis search/traversal coverage;
  - no scan orchestration changes;
  - no writer/provider runtime implementation;
  - no schema/index migration for the added labels.
- Unrelated or risky changes that should not be ported with this work:
  - large Living Docs tool additions;
  - parser alias changes that remove `rust` and add `st`;
  - broad parameter-validation rewrites;
  - unrelated tool metadata changes.

## Recommended Architecture

### 1. Separate primary parsers from framework overlays

Keep the existing primary parser map for mutually exclusive language ownership. Add a separate framework registry with:

- analyzer path;
- supported source/config extensions;
- project detector or lightweight trigger;
- incremental support flag;
- prerequisite primary parsers;
- execution order;
- whether the framework emits a Qdrant collection.

Suggested order:

1. Java/Kotlin primary analyzers.
2. Spring and Servlet/JSP overlays.
3. MyBatis overlay, after canonical language symbols and Spring bridge facts are available.

Do not add framework analyzers to the exclusive `owner_manifest.py` ownership set. Generate overlay manifests from the global changed/deleted set and let each framework detector narrow affected modules.

### 2. Define one provider-neutral graph contract

Before wiring the scan, restore the missing provider runtime and writer implementations. The writers must:

- support both Neo4j and FalkorDB through `GraphDriver`;
- preserve canonical Java/Kotlin node ownership;
- merge framework nodes by stable ID and framework-specific label;
- link to existing symbols without creating duplicate canonical `Function`/`Class` nodes;
- handle deleted files and partial scans safely;
- implement Servlet/JSP stage/promote/cleanup generation semantics;
- return deterministic write summaries for tests and CLI output.

Because this touches the same provider APIs and schema files as the active FalkorDB migration, implementation should begin only after its provider argument/runtime contract is stable.

### 3. Extend MCP through a shared framework contract

Add a small shared MCP registry rather than copying label and relationship lists across `fastmcp_server.py`, `cplus_mcp.py`, and `unified_mcp.py`. Per framework, define:

- parser aliases;
- searchable node labels/kinds;
- default traversal relationships;
- optional freshness predicate;
- searchable text properties.

Then update existing tools rather than adding three separate tool families:

- `list_parsers` and `activate_project` accept the framework aliases and continue routing them to the existing C++/Java-compatible backend.
- `search_functions` includes framework semantic nodes and can optionally filter by framework/kind.
- `get_symbol` and `get_node_details` return framework properties without classifying them as documents.
- `query_subgraph`, `trace_flow`, `semantic_search(expand_graph=true)`, and `explore_graph` use framework-aware default relationship sets when a framework parser is active.
- `find_callers_of_endpoint` and `get_api_call_chain` support Spring and Servlet/JSP endpoint/controller directions and MyBatis persistence edges.
- Servlet/JSP active-generation filtering is centralized and applied consistently.

Do not create separate framework vector collections in the first integration. Seed semantic search from the canonical Java/Kotlin Qdrant results, then expand through `SEMANTIC_OF` and framework edges. Revisit direct framework embeddings only if end-to-end retrieval tests show that graph expansion is insufficient.

## Phases

1. [Phase 01 - Complete the graph integration contract](phase-01-graph-contract.md)
2. [Phase 02 - Add framework overlays to the main scan](phase-02-scan-orchestration.md)
3. [Phase 03 - Make framework facts queryable through MCP](phase-03-mcp-query-integration.md)
4. [Phase 04 - End-to-end validation and documentation](phase-04-validation-and-docs.md)

## Dependencies

- `neo4j-to-falkordb-migration` must stabilize:
  - provider argument helpers and exports;
  - `GraphDriverFactory` configuration keys;
  - provider-neutral result shapes;
  - schema/index setup behavior.
- Parser runtime packages already present in `requirements.txt`/`code-tiny/requirements.txt` must be verified in the project virtual environment.
- Neo4j and FalkorDB parity fixtures are required before MCP queries are accepted.
- `docs/development-rules.md` was requested by the planning skill but is not present in this repository; existing repository conventions and the supplied root `AGENTS.md` govern this plan.

## Scope Boundaries

Included:

- missing shared graph runtime/writers required by the new analyzers;
- incremental and full scan integration;
- graph schema/index additions;
- unified MCP routing, search, traversal, and full-stack flow support;
- targeted tests and docs.

Excluded:

- wholesale replacement of current MCP files with the Desktop copies;
- Living Docs functionality found in the reference MCP snapshot;
- redesign of existing Java/Kotlin parsers;
- separate framework Qdrant collections unless validation proves them necessary;
- unrelated cleanup of legacy helpers in `cortex_harness/dev.py`.

## Success Criteria

- All three analyzer modules import successfully in the project virtual environment.
- A fixture containing Java/Kotlin plus Spring, Servlet/JSP, and MyBatis artifacts produces both canonical language nodes and framework overlay nodes in one `dev sync code` run.
- Incremental updates and deletions update only affected framework modules and do not delete canonical Java/Kotlin graph data.
- The same write contract passes against Neo4j and FalkorDB or has explicitly documented provider-specific exclusions.
- `list_parsers` returns `spring`, `servlet_jsp`, and `mybatis` without losing existing parser aliases such as `rust`.
- Name search, symbol lookup, graph expansion, endpoint flow, and persistence flow queries return framework facts with project scoping.
- Servlet/JSP queries never return inactive generations.
- Existing MCP, incremental sync, graph-provider, and Qdrant tests remain green.

## Completion Notes

Implementation and FalkorDB validation are complete. Live Neo4j write/query parity and live FalkorDB MCP execution remain excluded because the overridden `neo4j-to-falkordb-migration` dependency has not yet converted `setup_constraints.py` and the general C++/JVM MCP graph connection. See [reports/validation-report.md](reports/validation-report.md) for commands, counts, timings, and exclusions.

## Evidence Coverage

- `mind_mcp`: used; unavailable for project context because the configured `documents` collection does not exist.
- `graph_mcp.semantic_search`: used; identified the upstream HyperGraph framework parser and MCP integration areas through Qdrant. Neo4j relationship expansion was unavailable because port 7687 was down.
- Serena: unavailable in this session.
- `rg`/direct file inspection: used to verify local call paths, imports, diffs, graph contracts, and test coverage.
