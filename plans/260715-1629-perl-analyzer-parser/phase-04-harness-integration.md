# Phase 04: Integrate Graph, CLI, Sync, and MCP

## Context

An analyzer is not integrated when only its parser entry point exists. This phase connects the validated Perl result to shared graph/vector adapters and all repository discovery, ownership, sync, and MCP surfaces.

## Requirements

- Expose `python -m tools.perl.perl_analyzer` and `run_perl_analysis(...)`.
- Accept the shared analyzer CLI contract: root/project identity, commit SHAs, incremental and changed/deleted manifests, cache, logging, graph-provider/Neo4j compatibility, vector/provider compatibility, message-scan toggles, dry-run/output/diagnostics, and failure policy.
- Return non-zero exit codes for argument errors, requested analysis-policy failures, and persistence failures.
- Write canonical language facts through `LanguageCodeWriter` and shared provider setup; no direct provider-specific driver or Cypher in the Perl package.
- Reuse the existing code/vector collection contract when optional embedding is enabled; core analysis remains independent.
- Register `perl` consistently in incremental sync, source extension routing, owner manifests, root CLI discovery, and the common registry test.
- Route `parser_type="perl"` through the generic C++ MCP backend and update public routing instructions.
- Avoid adding a framework profile, custom labels, schema indexes, message detector, or Perl-specific MCP server unless a tested requirement proves it necessary.

## Architecture

```text
perl_analyzer CLI
  -> validated AnalysisResult
  -> JSON/diagnostics output
  -> shared graph provider setup -> LanguageCodeWriter
  -> optional existing Qdrant writer

incremental_sync + owner_manifest + cortex_harness/dev.py
  -> perl entry point

unified_mcp
  -> generic backend with parser_type=perl
```

## Related Files

- `code-tiny/tools/perl/__init__.py`
- `code-tiny/tools/perl/perl_analyzer.py`
- `code-tiny/tools/perl/pipeline.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/mcp/unified_mcp.py`
- `tests/test_common_analyzer_registry.py`
- `tests/common/test_incremental_sync_routing.py`
- `tests/common/test_owner_manifest.py`
- `tests/test_dev_perl_parser_discovery.py`
- `tests/test_perl_graph_contract.py`
- `tests/test_perl_cli.py`
- `tests/test_perl_mcp_routing.py`

## Implementation Steps

1. Implement the public package API and CLI orchestration without duplicating parsing rules in the CLI module.
2. Add all shared CLI arguments using the repository's graph/provider helpers and `allow_abbrev=False`.
3. Transform normalized records into canonical writer rows, validate endpoints, batch writes, and preserve stable semantic IDs plus generation-aware storage IDs.
4. Add optional embedding for bounded subroutine/comment/POD content using the existing collection/writer contract; make absence of vector services non-fatal unless explicitly required.
5. Register `AnalyzerConfig("perl", .../tools/perl/perl_analyzer.py, True)` and route approved extensions in `_select_parser_for_path` and `_SOURCE_EXTENSIONS`.
6. Add `perl` to owner-manifest supported parsers and exclusive extension routing.
7. Add `perl` to `LANG_ANALYZERS` and `LANG_EXTENSIONS`; verify `_detect_langs` on the fixture.
8. Add `perl` to unified MCP generic-backend routing and public parser mapping instructions; verify `list_parsers` and `activate_project`.
9. Update common registry expectations and add focused discovery, owner, incremental, CLI, graph, and MCP routing tests.
10. Run provider-neutral contract tests now; defer live Neo4j/FalkorDB parity acceptance until the blocking migration stabilizes.

## Todo

- [ ] Public API and CLI accept every shared invocation flag.
- [ ] Dry-run works without external services.
- [ ] Graph rows use canonical labels/relations and contain no orphan edges.
- [ ] Optional vector output is bounded and service-independent.
- [ ] All three primary-parser registries agree on `perl` and extensions.
- [ ] Unified MCP lists and activates `perl` on the generic backend.
- [ ] No unnecessary custom MCP/profile/schema/message components are added.

## Risks

- A shared sync invocation can break if even ignored compatibility flags are absent.
- Registry changes may conflict with concurrent Flutter/provider plan edits.
- Mapping lexical variables into canonical graph fields may need writer-contract clarification; it must not introduce a custom label casually.
- Live provider behavior remains gated by the active migration.

## Success Criteria

- Root CLI and incremental auto-detection invoke Perl for the approved extensions.
- Owner manifests assign those files exclusively to `perl`.
- Full, changed, and deleted runs accept the shared CLI and preserve correct exit behavior.
- Generic MCP search/traversal sees project-scoped Perl facts after activation.
- Existing common analyzer registry and non-Perl integration tests remain green.

