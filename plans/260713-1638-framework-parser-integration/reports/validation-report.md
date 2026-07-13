# Framework Parser Integration Validation Report

date: 2026-07-13
result: pass-with-provider-exclusions

## Decision

The user approved overriding the `neo4j-to-falkordb-migration` dependency and proceeding. The implementation therefore keeps compatibility shims narrow and records rather than expands the unfinished migration boundary.

## Automated Tests

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`: 38 tests ran, 7 skipped, OK.
- `.\.venv\Scripts\python.exe -m unittest tests.test_framework_analyzer_imports tests.test_framework_fixture_analysis tests.test_framework_graph_contract tests.test_framework_mcp_routing tests.test_incremental_sync_framework_overlays`: 11 framework-focused tests passed.
- `.\.venv\Scripts\python.exe -m py_compile ...`: all changed integration modules and framework tests compiled.
- `git diff --check`: passed.
- `make doctor`: Python venv, Python dependencies, and Docker CLI passed. Docker daemon, Qdrant, and FalkorDB were not running in the current Windows environment; code-tiny and doc-tiny MCP ports were warnings.
- `incremental_sync.py --full-scan --parsers spring,servlet_jsp,mybatis` against `tests/fixtures/framework-java-app`: attempted, but the Java primary analyzer failed during Qdrant cleanup because local Qdrant on `127.0.0.1:6333` was unavailable. The graphless analyzer fixture test above is the current reproducible coverage until local services are running.

The test set covers analyzer imports, writer validation/cleanup/generation behavior, overlay detection and prerequisite routing, parser discovery, framework search filters, traversal defaults, endpoint/persistence query shapes, project scoping, and Servlet/JSP active-generation filtering.

Current follow-up validation also fixed a Windows import failure in `tools.servlet_jsp.pipeline` by treating the POSIX-only `resource` module as optional for peak-RSS enforcement, declared the missing `tree-sitter-language-pack` runtime dependency required by MyBatis parser gates, restored tracked test visibility by removing the `tests/**` ignore rule, and added the compact `tests/fixtures/framework-java-app` mixed-framework fixture used by graphless analyzer coverage.

## Full-Scan Validation

A graphless full scan of `tests/fixtures/framework-java-app` completed without Qdrant and produced Java plus all three overlays. The measured duration was 24.824 seconds.

The same fixture then completed against isolated FalkorDB graph `framework_validation_20260713` in 27.131 seconds:

| Layer | Nodes/facts | Relationships | Duration |
| --- | ---: | ---: | ---: |
| Java primary | 4 files, 5 functions, 4 classes | canonical graph edges | 6.706 s |
| Spring | 8 | 6 | 0.896 s |
| Servlet/JSP | 14 active facts | 13 | 6.531 s |
| MyBatis | 14 | 9 | 6.411 s |

Direct FalkorDB inspection confirmed 8 Spring nodes, 14 MyBatis nodes, one active Servlet/JSP generation containing 14 facts, and the expected framework relationship types. The isolated graph was deleted after inspection.

During this validation FalkorDB rejected dictionary-valued MyBatis properties. Root cause analysis traced the values to `MyBatisFact.to_graph_node()` and `MyBatisRelationship.to_graph_relationship()`, which forwarded structured values through `SET node += row`. The boundary now preserves primitives and primitive arrays and encodes structured values as deterministic JSON. A regression test covers node and relationship properties, and the live rerun passed.

## Incremental and Deletion Validation

A temporary two-commit Git fixture was scanned into isolated FalkorDB graph `framework_incremental_validation_20260713`.

Deletion cycle:

- Deleted `src/main/resources/mappers/CatalogMapper.xml`.
- The sync detected one deleted path and ran all three applicable overlays.
- MyBatis cleanup deleted 10 file-owned nodes; 5 mapper-interface facts remained.
- No MyBatis node retained the deleted XML path.

Update cycle:

- Changed `CatalogController.java` in a separate commit.
- Java scanned one changed path and dependency-expanded to four Java files.
- Spring, Servlet/JSP, and MyBatis each received one changed overlay path and completed successfully.
- Final counts were stable at 8 Spring nodes, 15 Servlet/JSP nodes including its state node, 14 facts in exactly one active Servlet/JSP generation, 5 MyBatis nodes, and 18 primary/support nodes.

The isolated graph and temporary repository were deleted after inspection.

## MCP Query Matrix

Deterministic tests pass for:

- framework aliases and preservation of existing parser aliases;
- framework-filtered name search alongside base-language labels;
- framework traversal defaults and semantic graph expansion metadata;
- both Spring/Servlet endpoint `HANDLES` directions;
- Servlet `SEMANTIC_OF` handler bridging;
- Spring repository and MyBatis statement/table persistence paths;
- active Servlet/JSP generation predicates in search, lookup, and full-stack flows;
- provider-neutral execution for the unified full-stack bridge.

## Provider Exclusions

Live FalkorDB framework writes passed. The following live parity checks remain intentionally excluded until `neo4j-to-falkordb-migration` completes:

1. Neo4j was unavailable on port 7687 and Docker CLI was not installed, so a live Neo4j write comparison could not be started locally.
2. `code-tiny/scripts/setup_constraints.py` still exposes a Neo4j-only CLI. Framework index definitions are present and versioned, but live FalkorDB execution of this script belongs to the provider migration.
3. The general C++/JVM MCP graph connection still constructs a Neo4j driver. Framework query shapes and the unified full-stack bridge are provider-aware, but a live FalkorDB run of the complete MCP matrix depends on that migration work.

These exclusions do not affect analyzer discovery, overlay orchestration, FalkorDB graph ingestion, incremental cleanup, or the deterministic MCP contract tests delivered by this plan.
