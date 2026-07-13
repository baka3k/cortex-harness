# Phase 04: End-to-End Validation and Documentation

## Context

This integration spans parser imports, incremental routing, provider-neutral writes, schema setup, and MCP retrieval. Unit tests alone will not prove that the normal user workflow produces queryable framework graphs.

## Requirements

- Verify full and incremental scans through the public `dev` CLI.
- Verify Neo4j and FalkorDB graph parity at the contract level.
- Verify MCP search and flow outputs against graphs produced by the real analyzers.
- Measure scan overhead and query bounds.
- Document supported framework behavior, limitations, and troubleshooting.

## Validation Fixtures

Create a compact multi-module fixture containing:

- a Spring Boot application with controller, service, repository, configuration, security, event/messaging, and transaction annotations;
- a classic Servlet/JSP module with `web.xml`, annotated servlet/filter/listener, JSP EL/scriptlet/tag usage, properties, forwards, and error/welcome pages;
- MyBatis mapper interface, annotation queries, mapper XML, includes, dynamic SQL, result maps, Spring bridge configuration, and table/column references;
- shared Java/Kotlin classes so `SEMANTIC_OF` and cross-framework links can be verified;
- deletable artifacts for incremental/tombstone tests.

## Related Files

- `tests/fixtures/framework-java-app/` (new)
- `tests/test_framework_analyzer_imports.py` (new)
- `tests/test_incremental_sync_framework_overlays.py` (new)
- `tests/test_framework_graph_contract.py` (new)
- `tests/test_framework_mcp_routing.py` (new)
- `tests/test_framework_mcp_search.py` (new)
- `tests/test_framework_mcp_flows.py` (new)
- `docs/HARNESS_WORKFLOW.md`
- `docs/DATABASE_INTEGRATION.md`
- `code-tiny/README.md`
- `docs/specs/sync-code.md`
- `docs/specs/mcp.md`

## Implementation Steps

1. Add import and parser-capability smoke tests.
2. Add detector tests for true positives, false positives, Android exclusions, multi-module projects, and deletion-only changes.
3. Add writer contract tests with fake drivers and provider integration tests.
4. Run `dev sync code --full-scan` against the mixed fixture and capture graph counts by framework/kind/relationship.
5. Modify one artifact per framework and verify incremental updates, unchanged preservation, and deleted-fact cleanup.
6. Force a Servlet/JSP staging failure and verify the previous generation remains active.
7. Start unified MCP and verify:
   - parser discovery;
   - framework name search;
   - symbol lookup;
   - semantic search plus graph expansion;
   - endpoint callers and API chains;
   - Spring/MyBatis persistence flow;
   - project and active-generation filtering.
8. Run the existing root test suite and code-tiny MCP tester fixtures.
9. Measure full-scan and incremental overhead with overlays enabled and document the observed cost.
10. Update user and developer documentation with examples and limitations.

## Verification Commands

Exact commands should be finalized during implementation, but the validation set must include equivalents of:

```bash
python -m pytest tests/test_framework_analyzer_imports.py
python -m pytest tests/test_incremental_sync_framework_overlays.py
python -m pytest tests/test_framework_graph_contract.py
python -m pytest tests/test_framework_mcp_routing.py tests/test_framework_mcp_search.py tests/test_framework_mcp_flows.py
python -m pytest tests
dev sync code --full-scan --project-dir <fixture-project>
dev sync code --project-dir <fixture-project>
```

## Todo

- [ ] Mixed fixture covers all three frameworks and canonical Java/Kotlin nodes.
- [ ] Full scan passes.
- [ ] Incremental updates and deletions pass.
- [ ] Servlet/JSP failure/rollback path passes.
- [ ] Neo4j/FalkorDB parity report is recorded.
- [ ] MCP end-to-end query matrix passes.
- [ ] Existing tests remain green.
- [ ] Scan/query performance is within agreed bounds.
- [ ] Documentation reflects actual CLI and MCP behavior.

## Risks

- Local graph services may be unavailable in CI. Split deterministic fake-driver tests from service-gated integration tests and fail clearly when required services are requested but absent.
- A fixture that manually inserts nodes could hide writer bugs. MCP acceptance tests must consume analyzer-produced graph data.
- Provider parity should compare logical results, not driver-specific ordering or raw record types.

## Success Criteria

- The public workflow from `dev sync code` to MCP query is demonstrated on the fixture.
- Full and incremental scans produce deterministic framework counts and no duplicate canonical nodes.
- Neo4j and FalkorDB return equivalent logical MCP answers for the supported query matrix.
- Documentation includes setup, parser selection, overlay behavior, example queries, known limitations, and troubleshooting for missing graph services or parser runtimes.

