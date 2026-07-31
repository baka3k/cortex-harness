# Legacy Migration Parser Coverage - 2026-07-31

## Context

The legacy migration ingestion plan identified invisible Pro*C, shell, JP1/AJS, flat INI, and DAT assets in CP932-heavy batch projects (`plans/260731-1500-legacy-migration-parser-coverage/plan.md:1`).

## Change

- Added shared BOM, UTF-8, CP932, and CP1252 decoding in `code-tiny/tools/common/legacy_encoding.py:1`.
- Extended C/C++ ingestion for `.pc`/`.pcc`, masked `EXEC SQL` before tree-sitter parsing, and emitted `CplusSqlStatement` nodes with `DEFINES` relations in `code-tiny/tools/cplus/cplus_analyzer.py:600`.
- Added shell function, script-call, and INI-reference extraction in `code-tiny/tools/shell/parser.py:1`.
- Added content-sniffed JP1 unit, nesting, sequencing, and shell-target analysis in `code-tiny/tools/jp1/parser.py:1` and `code-tiny/tools/jp1/sniff.py:1`.
- Added flat INI descriptor parsing and DAT resource coverage in `code-tiny/tools/project_topology/registry.py:105`.
- Registered shell and JP1 across sync, ownership, root CLI, MCP capabilities, vectors, and acceptance contracts.

## Impact

Legacy batch projects now expose the jobnet to shell to INI dependency chain and Pro*C SQL evidence to graph and semantic retrieval. Incremental graph/vector cleanup removes stale custom nodes. Static target resolution is confined to the project root. Risk: medium, due to new primary analyzers and cross-parser graph relations; mitigated by focused integration, registry, security, and acceptance tests.

## Decision

The implementation uses bounded regex/line parsers instead of general shell or JP1 interpreters, matching the migration use cases while avoiding execution and runtime interpolation. JP1 `.txt` ownership remains content-sniffed with a metadata-keyed LRU cache, and unresolved dynamic targets remain explicit diagnostics rather than fabricated resolved edges.

## References

- Plan: `plans/260731-1500-legacy-migration-parser-coverage/plan.md:1`
- Tests: `tests/test_legacy_migration_e2e.py:1`
- Tests: `tests/test_common_analyzer_registry.py:1`
- Tests: `tests/test_mcp_acceptance_matrix.py:1`