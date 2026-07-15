---
title: "Unified MCP Capability and Parser Routing Upgrade"
status: completed_with_external_gate
created: 2026-07-15
mode: hi-plan --fast
scope: unified MCP discovery, aliases, capability profiles, parser-aware routing, validation, and tests
blockedBy: [neo4j-to-falkordb-migration]
dependencyOverride: implementation completed against the current provider-neutral contract; live Neo4j/FalkorDB parity remains gated
relatedPlans: [260713-1638-framework-parser-integration, 260714-1603-flutter-analyzer-parser, 260715-1629-perl-analyzer-parser, 260715-2011-aspnet-roslyn-analyzers]
---

# Unified MCP Capability and Parser Routing Upgrade

## Objective

Make one Unified MCP accurately describe and route all parser/framework profiles
without creating a new MCP server for every framework. Keep `cplus` as the
generic backend for compatible graph contracts, while preserving Android's
specialized backend where its semantics require it.

## Current context

`unified_mcp.py` has two physical backends (`android` and `cplus`). Framework
aliases, labels, relationships, and searchable properties are maintained in
`framework_registry.py`, but parser capability discovery is partly inferred
from backend directories and parser alias sets are split across modules.

The upgrade must distinguish:

- alias differences, which belong in registry metadata;
- graph profile differences, which belong in capability profiles; and
- materially different query/runtime semantics, which justify a new backend.

## Non-goals

- Do not create separate MCP servers for Spring, Struts, Flutter, or ASP.NET
  solely because they have different alias counts.
- Do not move Android into `cplus` while Android-specific tools and metadata
  still require its backend.
- Do not change parser ownership or analyzer output schemas.

## Target architecture

```text
canonical capability registry
        ├── aliases
        ├── backend assignment
        ├── labels / relationships / properties
        ├── default query profiles
        └── support level and feature gates
                    │
Unified MCP dispatch ─┴─> android backend or generic cplus backend
```

## Phases

1. [x] [Capability registry and alias normalization](phase-01-capability-registry.md)
2. [x] [Parser-aware backend dispatch and query defaults](phase-02-parser-aware-dispatch.md)
3. [x] [Capability validation and discovery UX](phase-03-capability-validation.md)
4. [x] [Regression, compatibility, and provider-gated verification](phase-04-verification.md)

## Success criteria

- Every advertised parser alias resolves deterministically to one framework
  profile and one backend.
- `list_parsers` reports actual capability/support level rather than directory
  presence alone.
- Search, subgraph, path, flow, endpoint, and workflow queries use the selected
  parser's relationship and label profile by default.
- Unsupported parser-specific operations return explicit capability errors or
  partial results, never silent empty success.
- Existing Android behavior remains unchanged.
- Spring, Struts, Flutter, ASP.NET, COBOL, Perl, and generic C++ routing remain
  backward compatible.
- Focused routing/discovery tests and existing MCP regressions pass.
- Live Neo4j/FalkorDB parity remains an explicit external gate, not a silently
  skipped success condition.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Alias collision across profiles | Validate uniqueness at registry load and test precedence explicitly. |
| Generic `cplus` query defaults omit framework edges | Resolve defaults from the selected capability profile before backend dispatch. |
| Advertised support exceeds implementation | Add `full`/`partial`/`generic` support levels and feature gates. |
| Android regressions from shared routing changes | Keep Android backend selection first and retain Android-specific overrides. |
| Provider-specific relationship differences | Filter profiles against relationships present in the active provider and report omissions. |

## Completion notes

- The canonical registry now owns aliases, backend assignment, support level,
  labels, relationships, searchable properties, query profiles, and feature
  discovery for every advertised parser.
- Unified dispatch, endpoint chains, workflow lookup, impact scoring, semantic
  expansion, and graph exploration consume the selected capability profile.
  Provider omissions are returned as structured diagnostics; missing mandatory
  relationships return `unsupported_capability`.
- Focused MCP/routing/provider-neutral verification passed, including
  deterministic Neo4j and FalkorDB query-shape runs. A live FalkorDB schema
  probe verified partial relationship diagnostics and mandatory-edge rejection.
- Mandatory code review approved the implementation at 9.6/10 with zero
  critical issues.
- Live Neo4j parity remains incomplete because no service was available on
  `127.0.0.1:7687`. This external gate is not counted as a passing test and
  remains blocked by `neo4j-to-falkordb-migration`.
- The repository-wide suite produced 145 passes and 23 unrelated environment
  failures in COBOL grammar/platform tests and Perl tests requiring the missing
  `tree_sitter_perl` package. The scoped MCP and provider suites are green.
