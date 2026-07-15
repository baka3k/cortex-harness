# Guide: Integrating a New Analyzer Tool

Paths in this guide are relative to `code-tiny/`; `../` leaves that directory for repository-level files.

## Prerequisites

Before registering a tool, confirm that its analyzer entry point is importable and has focused parser tests. A production analyzer should support the shared sync arguments below, write project-scoped graph facts through the provider-neutral graph layer, and return a non-zero exit code on failure.

Required shared arguments:

- `--root`, `--project-id`, and `--project-name`
- `--commit-sha-before` and `--commit-sha-after`
- `--incremental`, `--changed-files-manifest`, and `--deleted-files-manifest`
- `--ignore-cache` and `--verbose`
- Neo4j compatibility flags plus `add_graph_provider_args(...)` for FalkorDB
- `--enable-message-scan` and `--disable-message-scan`, even if the analyzer currently ignores message scanning

Use `tools.graph.cli.create_graph_driver_from_args` and the writers under `tools/graph/writer/`. Do not add a language-specific MCP server when the unified MCP can expose the facts.

## Choose the Integration Type

| Type                    | Ownership                                                                                 | Examples                         |
| ----------------------- | ----------------------------------------------------------------------------------------- | -------------------------------- |
| Primary language parser | Exclusively owns matching source files and handles deletion                               | COBOL, Go, Rust, Dart            |
| Framework overlay       | Enriches facts owned by one or more primary parsers; never owns their source nodes        | Spring, Struts, Flutter, MyBatis |
| Backend specialization  | Uses a distinct MCP backend only when generic graph queries cannot represent its behavior | Android                          |

## Required Integration File Map

Every new tool must be reviewed against the following file set. These are the shared integration points used by the current COBOL, Go, Spring, Struts, and Flutter/Dart work; do not consider a tool integrated after changing only its analyzer file.

| File                              | Primary parser                             | Framework overlay | Required change                                                                                                                                               |
| --------------------------------- | ------------------------------------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tools/<tool>/<tool>_analyzer.py` | Yes                                        | Yes               | Implement the shared CLI, graph write, cleanup, incremental behavior, and non-zero failure exits. For example, Struts uses `tools/struts/struts_analyzer.py`. |
| `tools/sync/incremental_sync.py`  | Yes                                        | Yes               | Register the analyzer, route changed/deleted paths, declare prerequisites/order, add source or candidate extensions, and pass tool-specific `extra_args`.     |
| `tools/sync/owner_manifest.py`    | Yes                                        | No                | Register exclusive source ownership and extension/classifier routing. Never add overlays here.                                                                |
| `../cortex_harness/dev.py`        | Yes                                        | Yes               | Expose the tool through root CLI discovery using `LANG_ANALYZERS` or `FRAMEWORK_ANALYZERS`; add primary extensions to `LANG_EXTENSIONS`.                      |
| `mcp/framework_registry.py`       | When parser-specific graph behavior exists | Yes               | Register aliases, searchable labels/properties, and parser-specific traversal relationships.                                                                  |
| `mcp/unified_mcp.py`              | Yes                                        | Yes               | Route aliases to the shared backend, expose canonical names through `list_parsers`, and update public routing instructions.                                   |
| `docs/guide_tool_integrate.md`    | Yes                                        | Yes               | Update this guide whenever a new shared integration point, required flag, registry, or verification step is introduced.                                       |

The minimum review path is therefore:

```text
analyzer entry point
  -> incremental sync registry and routing
  -> primary owner manifest (primary parsers only)
  -> root dev CLI registry
  -> MCP framework profile when needed
  -> unified MCP routing and discovery
  -> integration guide and tests
```

## Files to Update

### 1. Analyzer implementation

Create or complete:

- `tools/<tool>/<tool>_analyzer.py`: shared CLI contract, full/incremental execution, graph writes, cleanup, and exit codes.
- `tools/<tool>/__init__.py`: public imports when the tool is a package.
- Supporting parser, detector, resolver, cache, and model modules only when the analyzer needs them.

For an overlay, emit stable IDs, `project_id`, `file_path`, `framework`, `kind`, confidence/resolution metadata, and explicit relationship types. Provide a project detector so auto mode does not run the overlay on unrelated repositories.

### 2. Root Cortex CLI

Update `../cortex_harness/dev.py`:

- Add a primary entry point to `LANG_ANALYZERS`, or an overlay to `FRAMEWORK_ANALYZERS`.
- Add primary file extensions to `LANG_EXTENSIONS`.
- If file ownership is ambiguous, update `_detect_langs` or delegate to a dedicated classifier rather than assigning the same extension to multiple parsers.

This registry drives CLI discovery/status output. It must agree with the incremental sync registry.

### 3. Shared incremental sync

Update `tools/sync/incremental_sync.py`:

- Primary parser: add `AnalyzerConfig` in `ANALYZERS`.
- Framework overlay: add `FrameworkAnalyzerConfig` in `FRAMEWORK_ANALYZERS`, including prerequisite parsers, execution order, vector behavior, and any `extra_args`.
- Map primary extensions in `_select_parser_for_path`.
- Add extensions to `_SOURCE_EXTENSIONS` so full scans can discover them.
- Add overlay candidate extensions to `_FRAMEWORK_CANDIDATE_EXTENSIONS`.
- Add detector routing in `_group_paths_by_framework`, including strong deleted-file candidates for cleanup.
- Add the parser to `MESSAGE_ENABLED_PARSERS` only when a matching detector exists under `tools/common/message_detectors/`.

An overlay must run after its prerequisite primary parsers. Caller-supplied parser selection remains authoritative; auto mode must remain detector-gated.

### 4. Primary owner manifests

For a primary parser, update `tools/sync/owner_manifest.py`:

- Add the canonical parser name to `SUPPORTED_PARSERS`.
- Map its files in `_select_parser_for_path`.
- Add or reuse a classifier when extensions overlap, as VB and SQL/PLSQL do.

Check `tools/sync/build_owner_manifests.py` only when a public alias must map to the canonical parser name. Framework overlays must not be added to owner manifests.

### 5. Unified MCP query routing

Update `mcp/framework_registry.py` when the tool introduces framework-specific labels or relationships:

- aliases accepted by `activate_project(parser_type=...)`
- graph labels/kinds that search may return
- default traversal relationships
- searchable properties
- freshness/generation behavior when stale facts can coexist

Update `mcp/unified_mcp.py`:

- Add generic primary aliases to the correct backend alias set.
- Update the MCP routing instructions.
- Ensure `tool_list_parsers` includes explicit aliases that cannot be discovered from a `tools/` directory name.

Also inspect these consumers when the graph contract is new:

- `mcp/fastmcp_server.py` and `mcp/cplus/cplus_mcp.py`: parser-aware default relationships and search behavior.
- `mcp/tool_metadata.py`: public tool descriptions and parameters.
- `mcp/services/`: semantic expansion, workflows, impact, endpoint, or full-stack behavior.
- `scripts/setup_constraints.py`: indexes/constraints for new custom labels.

Prefer the shared framework registry over duplicating label and relationship lists across MCP backends.

### 6. Tests

Add focused tests under `../tests/`:

- `<tool>_analyzer_imports`: entry point and support modules import.
- `<tool>_fixture_analysis`: deterministic facts from a minimal fixture.
- `test_dev_<tool>_parser_discovery`: root CLI registry and extension detection.
- `test_incremental_sync_<tool>`: changed, impacted, deleted, and full-scan routing.
- `test_<tool>_mcp_routing`: aliases, labels, and default relationships.
- MCP search/flow tests when the tool adds custom graph facts.
- Provider query-shape tests for both FalkorDB and Neo4j when graph behavior changes.

Keep `test_common_analyzer_registry.py` passing. It is the guard against implementing an analyzer but forgetting to expose it through the common toolchain.

### 7. Documentation

Update the relevant supported-tool lists and operating docs:

- `README.md`
- `docs/specs/sync-code.md`
- `docs/specs/sync-doc.md`
- `code-tiny/mcp/Readme.md`

Document aliases, prerequisites, supported source formats, incremental limitations, and graph labels/relationships.

## Verification Steps

1. Run analyzer import and fixture tests.
2. Run root discovery, owner-manifest, and incremental routing tests.
3. Run unified MCP routing, wrapper-signature, search, and flow tests.
4. Run `python -m py_compile` for every changed Python file.
5. Run the relevant regression suite.
6. Verify `list_parsers` returns the new canonical name and aliases.
7. Verify `activate_project` selects the expected backend and parser-aware relationships.
8. Execute one full sync and one changed/deleted incremental sync against a fixture.
9. When graph queries changed, verify both default FalkorDB and explicit `GRAPH_PROVIDER=neo4j` behavior.

## Troubleshooting

| Symptom                                      | Likely missing integration                                                             |
| -------------------------------------------- | -------------------------------------------------------------------------------------- |
| Analyzer exists but never runs               | `ANALYZERS`, `FRAMEWORK_ANALYZERS`, `_select_parser_for_path`, or `_SOURCE_EXTENSIONS` |
| Tool appears in CLI but not incremental sync | Root and incremental registries are out of sync                                        |
| Overlay runs on unrelated projects           | Missing or overly broad detector gating                                                |
| Deleted files leave stale facts              | Missing deleted candidate routing or graph cleanup                                     |
| `list_parsers` omits an alias                | `framework_registry.py` aliases or `tool_list_parsers` extras                          |
| MCP search returns only base-language nodes  | Missing framework labels/searchable properties or graph expansion relationships        |
| Neo4j works but FalkorDB fails               | Provider-specific Cypher escaped the shared graph abstraction                          |
| `dev sync code all` fails on unknown flags   | Analyzer does not implement the shared CLI contract                                    |

## Pull Request Checklist

- [ ] Analyzer entry point exists and accepts the shared CLI contract.
- [ ] Primary ownership or overlay prerequisites are explicit.
- [ ] Root CLI, incremental sync, and owner manifests agree.
- [ ] Unified MCP aliases, labels, and relationships are registered.
- [ ] Full, incremental, and deletion behavior is tested.
- [ ] Both graph providers are covered when query behavior changes.
- [ ] Supported-tool documentation is updated.
- [ ] No language-specific MCP server was added without a demonstrated backend requirement.
