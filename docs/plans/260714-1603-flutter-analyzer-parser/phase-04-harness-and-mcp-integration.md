# Phase 04: Integrate Incremental Sync and MCP

## Context

The tool is not usable through CortexHarness until parser discovery, primary ownership, overlay execution, CLI status, schema setup, and unified MCP query behavior agree on the new parser types.

## Requirements

- Register `dart` as a primary parser and `flutter` as a prerequisite-aware overlay.
- Auto-detect Flutter from project metadata and avoid running it for unrelated Dart packages.
- Preserve full and incremental analyzer command compatibility.
- Reuse existing MCP tools instead of adding a Flutter-only server/tool family.
- Make Flutter labels, aliases, and relationships searchable and traversable.
- Keep existing parser aliases and tests unchanged.

## Architecture

`.dart` files are exclusively owned by the Dart parser. Flutter overlay candidates include Dart, pubspec/analysis YAML, ARB/JSON, assets, XML/plist, and Gradle metadata, narrowed by the Flutter project detector. The overlay runs after Dart canonical facts are available.

The shared MCP framework registry receives a Flutter query profile. `dart` and `flutter` route to the existing general code backend, while registry-driven labels and relationships extend search, symbol lookup, subgraphs, semantic expansion, workflow discovery, and impact analysis.

## Related Files

Modify:

- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/fastmcp_server.py` only where registry-driven behavior is not already sufficient
- `code-tiny/mcp/semantic_graph_expansion.py`
- `code-tiny/scripts/setup_constraints.py`
- `docs/HARNESS_WORKFLOW.md`

Create:

- `tests/test_dev_flutter_parser_discovery.py`
- `tests/test_incremental_sync_flutter.py`
- `tests/test_flutter_mcp_routing.py`
- `tests/test_flutter_mcp_search.py`
- `tests/test_flutter_mcp_flows.py`

## Implementation Steps

1. Add Dart ownership and Flutter overlay configuration, candidate extensions, prerequisites, and execution order.
2. Extend owner manifests and root CLI analyzer/extension maps without giving Flutter exclusive ownership of YAML/XML/assets.
3. Ensure analyzer command construction supports the Python adapter and passes graph/Qdrant/incremental settings unchanged.
4. Add `dart`/`flutter` aliases, labels, searchable properties, and default relationships to unified MCP routing and the shared registry.
5. Verify existing search, symbol, subgraph, semantic expansion, route-flow, and impact tools return Flutter facts with project scoping.
6. Add end-to-end fixture tests for full sync, one-file change, pubspec change, and deletion.
7. Document prerequisites, setup, commands, output semantics, and MVP limitations.

## Todo

- [ ] Add parser discovery and auto-detection tests.
- [ ] Verify `list_parsers` retains every existing alias and adds `dart`/`flutter`.
- [ ] Verify MCP defaults do not make Flutter relationships global for unrelated parsers.
- [ ] Validate full and incremental CLI summaries and failure codes.
- [ ] Run the focused MVP regression suite.

## Risks

- The incremental command builder currently assumes a Python script entry point; the adapter must preserve that contract.
- Broad YAML/XML candidate sets can over-invalidate unless the Flutter detector and module scoping run first.
- Shared MCP label expansion can leak cross-project or cross-framework results without strict scoping.

## Success Criteria

- Auto and explicit `dart,flutter` scans invoke the correct primary/overlay order.
- Pure Dart projects run Dart only; non-Dart projects run neither parser.
- Full sync and incremental change/delete tests pass with deterministic summaries.
- Unified MCP lists, searches, resolves, and traverses Flutter facts without adding a separate backend.
- Existing framework, Android, provider, semantic expansion, and wrapper-signature tests remain green.
- The MVP gate in `plan.md` is satisfied.

