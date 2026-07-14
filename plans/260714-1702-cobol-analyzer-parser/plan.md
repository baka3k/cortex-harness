---
title: "COBOL Analyzer Parser Tool Plan"
status: pending
created: 2026-07-14
mode: hi-plan --fast
source: /Users/yourcacc/Desktop/COBOL_Analyzer_Design_Spec_v2.md
target: /Users/yourcacc/AI/cortex-harness/code-tiny/tools/cobol
scope: COBOL parsing, copybook and symbol resolution, control/data semantics, graph ingestion, incremental sync, unified MCP
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans:
  [
    260713-1638-framework-parser-integration,
    260714-1603-flutter-analyzer-parser,
  ]
---

# COBOL Analyzer Parser Tool Plan

## Overview

Build a Python 3.10 COBOL analyzer in the user-specified `code-tiny/tools/cobol` directory. The analyzer will load the supplied Tree-sitter COBOL grammar, reconstruct program/data/procedure structure, resolve copybooks and cross-program calls, build a COBOL-aware control-flow graph, and publish searchable semantic facts through the existing CortexHarness graph, incremental-sync, Qdrant, and unified-MCP paths.

The implementation should remain one primary language analyzer, not a separate service:

```text
dev sync code
  -> COBOL primary parser (.cbl/.cob/.cpy/.copy)
       -> Tree-sitter AST
       -> structure and data extraction
       -> copybook and symbol resolution
       -> CFG and semantic facts
  -> provider-neutral LanguageCodeWriter / GraphDriver
  -> existing project-scoped Qdrant collection
  -> unified MCP search, traversal, dependency, and impact tools
```

## Verified Project Context

- `code-tiny/tools/cobol/` currently contains only `lib/cobol.cpython-310-darwin.so`; no COBOL Python analyzer or tests exist.
- The supplied library is a 2.8 MB universal Mach-O bundle for `x86_64` and `arm64`, exports `tree_sitter_cobol`, and successfully parses a minimal COBOL program with the project Python 3.10 runtime.
- The current `tree_sitter.Language(pointer)` loading path works but emits a deprecation warning. Runtime compatibility must therefore be isolated behind a tested loader rather than spread through extractors.
- A probe containing program/data/procedure divisions, `COPY`, `PERFORM`, `CALL`, `EXEC SQL`, and `EXEC CICS` produced useful grammar nodes. A common `GO TO ... DEPENDING ON` sample also produced an error node, so grammar-node and error-recovery behavior must be fixture-driven before semantic extraction is accepted.
- `code-tiny/tools/sync/incremental_sync.py::ANALYZERS` owns primary parser execution; COBOL is not registered.
- `cortex_harness/dev.py` has separate analyzer-discovery and extension maps that also lack COBOL.
- `LanguageCodeWriter` already supports canonical language facts, caller-defined nodes, and typed relationships through the provider-neutral graph driver.
- `code-tiny/mcp/framework_registry.py` is the current query-profile extension seam for aliases, searchable labels, properties, and default relationships; unified MCP routes general JVM/native languages through the existing C++ backend.
- The active Neo4j-to-FalkorDB migration owns the provider, schema, and raw-query contract that COBOL graph writes and MCP queries must use.
- `docs/development-rules.md` is not present. The supplied root instructions, project memories, and existing repository conventions govern this plan.

## Scope Model

The source specification combines an MVP list with broader technology and success claims. This plan separates delivery gates so the initial analyzer remains testable.

| Gate                            | Included capability                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| MVP (end of Phase 04)           | Parser preflight; `.cbl/.cob/.cpy/.copy`; identification/environment/data/procedure structure; program/section/paragraph/data-item indexes; `COPY`; static and dynamic `CALL`; `PERFORM`, `PERFORM THRU`, `GO TO`, fall-through, exits, conditionals, and `ALTER`; variable references; file bindings and I/O; preserved SQL/CICS statements; semantic graph; full/incremental CLI and MCP integration |
| Spec-complete (end of Phase 05) | ANSI/IBM Enterprise/Micro Focus/GnuCOBOL fixture matrix; fixed/free source formats and dialect directives; deeper SQL/CICS extraction; cross-platform grammar packaging/loading; malformed-source recovery; scale, provider-parity, and operational documentation                                                                                                                                      |

Excluded from this plan:

- COBOL execution, compilation, code generation, or source-to-source migration;
- DB2 query optimization or complete SQL grammar implementation;
- full CICS, JCL, IMS, or batch-job analyzers (only metadata/extension seams are retained);
- runtime resolution of genuinely dynamic calls, altered branches, or computed file assignments;
- a COBOL-specific MCP server, graph backend, or vector collection;
- broad refactoring of existing analyzers, graph providers, or MCP tools.

## Target Architecture

### Package boundary

Use a flat package consistent with other complex analyzers:

```text
code-tiny/tools/cobol/
├── __init__.py
├── cobol_analyzer.py       # CLI entry point
├── models.py               # versioned facts and diagnostics
├── parser_runtime.py       # grammar discovery, ABI/platform preflight
├── parser.py               # AST traversal and source evidence
├── resolver.py             # copybooks, labels, data items, programs
├── cfg.py                  # paragraph/section control-flow graph
├── semantics.py            # graph-node/edge normalization
├── pipeline.py             # staged full/incremental orchestration
├── README.md
└── lib/
    └── cobol.cpython-310-darwin.so
```

The parser produces a versioned in-memory fact model with deterministic IDs, source spans, confidence, analyzer/grammar versions, and diagnostics. Graph mutation begins only after parse and resolution stages complete successfully.

### Parser runtime

- Resolve the grammar from an explicit CLI/environment override first, then the bundled platform-compatible default.
- Validate file existence, OS/architecture, exported `tree_sitter_cobol` symbol, Tree-sitter ABI, and a minimal parse during preflight.
- Keep the current Darwin binary usable while making Linux/Windows artifacts or a documented build/install path a Phase 05 acceptance requirement.
- Pin or compatibility-test the `tree-sitter` Python range; fail with an actionable diagnostic when the deprecated pointer API becomes unsupported.
- Preserve error nodes and source ranges. Do not silently turn partial parses into high-confidence semantic facts.

### Identity and graph contract

Reuse canonical `Project`, `Repository`, and `File` ownership. Use namespaced semantic labels to avoid collisions with generic graph concepts:

- `CobolProgram`
- `CobolSection`
- `CobolParagraph`
- `CobolDataItem`
- `CobolCopybook`
- `CobolFile`
- `CobolSqlStatement`
- `CobolCicsCommand`

Normalize the specification's lowercase relationships to repository-style uppercase types:

- structure/data: `DEFINES`, `INCLUDES`, `REFERENCES`;
- calls/control: `CALLS`, `PERFORMS`, `PERFORMS_THRU`, `RETURNS`, `GOES_TO`, `GOES_TO_DYNAMIC`, `FALLS_THROUGH`, `ALTERS`, `CONDITIONAL`, `EXITS`;
- resources: `READS`, `WRITES`.

Every node and edge must carry project scope, stable identity, source evidence, and confidence. Dynamic or unresolved targets remain explicit diagnostics/references; the analyzer must not fabricate resolved programs, paragraphs, variables, files, tables, or CICS resources.

### Resolution and control-flow rules

- Build source-order program, section, and paragraph indexes before resolving branches.
- Expand `COPY` through configured search roots/extensions with cycle detection, nested-copy support, and provenance preservation; `COPY REPLACING` behavior must be tested before being marked resolved.
- Resolve `PERFORM THRU` ranges by paragraph order and emit the return continuation separately from a `GO TO`.
- Add fall-through only when the preceding path is not terminated by `GO TO`, `EXIT`, `STOP RUN`, `GOBACK`, or an equivalent terminal construct.
- Model `GO TO DEPENDING ON` as candidate edges with `dynamic=true` and selector evidence.
- Mark `ALTER`-affected paths as dynamic and lower confidence instead of replacing the original static graph.
- Resolve literal `CALL` targets across the project; retain identifier-based calls as dynamic unless the data value can be proven statically.
- Keep definitions imported from copybooks linked to their original file while exposing a resolved program view.

### Incremental behavior

- Register `cobol` as the exclusive owner of `.cbl`, `.cob`, `.cpy`, and `.copy` files.
- Maintain reverse dependencies from copybooks to including programs, including transitive includes.
- Reparse changed programs/copybooks and affected dependents; re-resolve project call targets without rewriting unrelated facts.
- Apply file-scoped tombstones only after successful staged analysis, preserving prior graph state on fatal parser/runtime failure.
- Treat optional `.jcl` as future metadata input, not primary COBOL ownership in the MVP.

## Phases

1. [Phase 01 - Establish the runtime contract and tool skeleton](phase-01-runtime-contract-and-skeleton.md)
2. [Phase 02 - Parse structure, data, and copybooks](phase-02-structure-data-and-copybooks.md)
3. [Phase 03 - Build resolution, control flow, and semantic facts](phase-03-control-flow-and-semantics.md)
4. [Phase 04 - Integrate graph writes, incremental sync, and MCP](phase-04-harness-and-mcp-integration.md)
5. [Phase 05 - Harden dialects, portability, and validation](phase-05-dialects-portability-and-hardening.md)

## Cross-Plan Dependencies

- `neo4j-to-falkordb-migration` blocks provider-parity acceptance because COBOL adds schema/index coverage and uses the provider-neutral writer/query contract.
- `260714-1603-flutter-analyzer-parser` is not a functional blocker, but both plans modify analyzer registries, owner manifests, schema setup, unified MCP routing, shared query profiles, requirements, and root tests. Coordinate those edits and preserve both parser registrations.
- `260713-1638-framework-parser-integration` is a completed-with-exclusions implementation pattern for staged writers, sync integration, shared MCP profiles, and provider-aware tests. Reuse its seams; do not copy framework ownership semantics into COBOL.
- Phases 01-03 can proceed while the graph migration remains active. Phase 04 live provider parity and Phase 05 rollout acceptance require the migration contract to be stable.

## Validation Strategy

- Add one deterministic multi-file fixture containing two programs, nested copybooks, working/local/linkage/file data, static/dynamic calls, `PERFORM THRU`, conditional flow, `GO TO DEPENDING ON`, `ALTER`, file I/O, SQL, CICS, and malformed syntax.
- Keep focused golden tests for grammar node names, source spans, IDs, diagnostics, CFG edges, and resolved/unresolved relationships.
- Test full scan, single-program change, copybook fan-out change, deletion, parser failure, and two identical runs.
- Use fake/in-memory graph-driver tests first; run live Neo4j/FalkorDB parity only after the migration gate is available.
- Verify unified MCP parser listing, routing, name search, symbol lookup, graph traversal, module paths, dependency planning, and workflow impact with strict project scoping.
- Establish parse/error-rate and incremental invalidation baselines on a medium fixture before claiming scale readiness.

## Success Criteria

### MVP gate

- `code-tiny/tools/cobol/cobol_analyzer.py` imports, exposes the standard analyzer CLI contract, and preflights the supplied grammar deterministically.
- Valid fixtures reconstruct program/division/section/paragraph/data structure with stable IDs and source evidence.
- Copybook definitions retain provenance and resolve into consuming program symbol tables; missing/cyclic includes produce diagnostics.
- CFG output distinguishes `PERFORM`/return from `GO TO`, models `PERFORM THRU`, fall-through, dynamic branches, conditionals, exits, and `ALTER` without fabricated certainty.
- Static cross-program calls, variable references, file reads/writes, and SQL/CICS statement nodes are queryable.
- `dev sync code --parsers cobol` and auto-detection support full, incremental, and deletion flows.
- Existing MCP tools list, search, resolve, and traverse COBOL facts without a new server or regressions in existing parser aliases.

### Spec-complete gate

- The dialect/source-format fixture matrix passes or unsupported syntax is explicitly documented with recoverable diagnostics.
- Linux and Windows have verified grammar artifacts or a reproducible supported build/install path; Darwin continues to support both bundled architectures.
- Embedded SQL and CICS facts cover the operations promised by the source specification without claiming full DB2/CICS analysis.
- Neo4j and FalkorDB produce equivalent logical COBOL nodes, relationships, and core MCP results.
- Runtime versions, copybook search configuration, commands, graph schema, limitations, and troubleshooting are documented.

## Risks and Mitigations

| Risk                                                           | Mitigation                                                                                                                                        |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bundled binary is Darwin/Python-API specific                   | Isolate loading, validate symbol/ABI/platform, support override paths, and gate Linux/Windows on tested artifacts or reproducible builds.         |
| Grammar shape differs across COBOL dialects                    | Pin/identify grammar version, create dialect golden fixtures, tolerate error nodes, and keep extractors query/field-name driven with diagnostics. |
| Fixed/free source formats and continuation rules corrupt spans | Preserve original bytes, make normalization offset-aware, and test sequence area, continuation, comments, directives, and mixed encodings.        |
| Copybook expansion causes cycles or symbol collisions          | Use canonical include identities, cycle/depth guards, scoped symbol tables, `COPY REPLACING` tests, and original-file provenance.                 |
| COBOL control flow is non-structured                           | Build CFG after paragraph indexing, encode dynamic edges/confidence, and validate edge sets against hand-authored fixtures.                       |
| Incremental copybook changes have large fan-out                | Persist reverse include dependencies, recompute only the affected transitive closure, and report invalidation counts.                             |
| Provider or MCP changes conflict with active parser plans      | Coordinate shared files through cross-plan links and add additive regression tests that assert all aliases/labels remain present.                 |
| Dynamic calls/branches create false dependencies               | Resolve only provable literals/constants; retain unresolved candidates and confidence instead of guessing.                                        |

## Reference Material

- Source design: `/Users/yourcacc/Desktop/COBOL_Analyzer_Design_Spec_v2.md`
- Required implementation root: `/Users/yourcacc/AI/cortex-harness/code-tiny/tools/cobol`
- Bundled grammar: `/Users/yourcacc/AI/cortex-harness/code-tiny/tools/cobol/lib/cobol.cpython-310-darwin.so`
- Integration patterns: `code-tiny/tools/flutter/`, `code-tiny/tools/sync/incremental_sync.py`, `code-tiny/tools/graph/writer/language_writer.py`, and `code-tiny/mcp/framework_registry.py`
