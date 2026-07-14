---
title: "Flutter Analyzer Parser Tool Plan"
status: in_progress
created: 2026-07-14
mode: hi-plan --fast
source: /Users/account/Downloads/Flutter_Analyzer_Design_Spec.md
scope: Dart parsing, Flutter semantic analysis, graph ingestion, incremental sync, unified MCP
blockedBy: [neo4j-to-falkordb-migration]
relatedPlans: [260713-1638-framework-parser-integration]
---

# Flutter Analyzer Parser Tool Plan

## Overview

Build a new CortexHarness parser that reconstructs a Flutter application's Dart symbols and Flutter-specific architecture. The implementation should add Dart as a primary language parser and Flutter as a semantic overlay, then expose both through the existing graph providers, incremental scan flow, and unified MCP tools.

The implementation is a Python-only tool:

1. Python uses the precompiled `tree-sitter-dart` grammar to produce Dart syntax trees and source facts.
2. Python performs conservative project-local symbol resolution, applies CortexHarness IDs and incremental rules, and writes through the existing provider-neutral `LanguageCodeWriter`/`GraphDriver` APIs.

This keeps installation and execution Python-only while avoiding duplicate Neo4j/FalkorDB, Qdrant, CLI, and MCP implementations.

```text
dev sync code
  -> Dart primary parser
       -> Python tree-sitter-dart parser -> canonical File/Class/Function facts
  -> Flutter semantic overlay
       -> widgets/routes/assets/state/DI/platform facts
  -> provider-neutral graph writer + existing Qdrant collection
  -> unified MCP search, traversal, workflow, and impact tools
```

## Verified Project Context

- No Dart or Flutter analyzer exists under `code-tiny/tools/`.
- Primary parser ownership is registered in `code-tiny/tools/sync/incremental_sync.py::ANALYZERS`; framework enrichers use `FRAMEWORK_ANALYZERS` and prerequisite parsers.
- `cortex_harness/dev.py` maintains separate analyzer-discovery and extension maps that must remain consistent with incremental sync.
- `code-tiny/tools/graph/writer/language_writer.py` already supports canonical language nodes plus caller-defined semantic nodes and typed relationships.
- `code-tiny/mcp/framework_registry.py` is the shared extension seam for parser aliases, searchable labels, and default traversal relationships.
- Existing Android and framework analyzers provide the closest integration patterns, but Flutter needs resolved Dart semantics rather than name-only pattern matching.
- `tree-sitter-dart` publishes Python wheels, covers Dart/Flutter syntax, and exposes the standard `py-tree-sitter` parser API without requiring a Dart or Flutter SDK.
- `docs/development-rules.md` is absent; the supplied root instructions and existing repository conventions govern this plan.

## Scope Model

The source specification mixes a focused MVP in section 12 with broader final success criteria in section 15. This plan makes the distinction explicit.

| Gate                            | Included capability                                                                                                                                                                                                                                                                                                  |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MVP (end of Phase 04)           | Dart symbols and cross-file resolution; `main`/`runApp`; Stateless/Stateful widgets and `build`; widget hierarchy; `Navigator`, `MaterialPageRoute`, named routes, generic Router configuration; `pubspec.yaml` assets/fonts; `Image.asset`, `AssetImage`, and bundle usage; semantic graph; CLI and MCP integration |
| Spec-complete (end of Phase 05) | Provider/Riverpod/Bloc/Cubit/GetX and core notifier state flows; `get_it`/`injectable`; GoRouter/AutoRoute; themes and ARB localization; Method/Event/BasicMessage channels linked to native artifacts; Android/iOS/desktop/web platform metadata; full state, dependency, asset, and platform graphs                |

Excluded from this plan:

- Firebase, Hive, Isar, Drift, Dio, and GraphQL extractors listed as future extensions;
- automatic source-to-source migrations such as Provider-to-Riverpod;
- a new Flutter-specific MCP server or separate vector collection;
- runtime widget-tree inspection, generated application execution, or build instrumentation;
- wholesale refactors of existing Android, language, framework, or MCP backends.

## Target Architecture

### Runtime boundary

- `flutter_analyzer.py` remains the Python CLI entry point expected by incremental sync.
- `dart_parser.py` uses `tree-sitter-dart` for parsing, syntax diagnostics, declaration extraction, and conservative project-local resolution.
- The parser produces a versioned fact contract with `header`, `node`, `edge`, `diagnostic`, and `summary` records.
- The Python pipeline rejects unsupported schema versions and stages a complete fact set before graph mutation.
- Preflight validates the Python grammar package; no Dart/Flutter SDK or subprocess is required.

### Ownership model

- `dart` is the exclusive primary owner of `.dart` files and writes canonical `File`, `Class`, `Function`, `Field`, import, inheritance, reference, and call facts.
- `flutter` is a non-exclusive semantic overlay triggered only for a Flutter project detected from `pubspec.yaml` and Flutter SDK dependencies.
- YAML, ARB/JSON, XML, plist, Gradle, and asset files are overlay inputs, not competing primary language owners.
- Generated Dart files participate in resolution when required, but are marked `generated` and omitted from user-facing semantic ownership by default.

### Graph contract

Keep canonical Dart symbols in existing labels. Use namespaced semantic labels to prevent collisions with React, Android, and generic graph nodes:

- `FlutterApplication`
- `FlutterWidget`
- `FlutterState`
- `FlutterRoute`
- `FlutterStateSource`
- `FlutterService`
- `FlutterAsset`
- `FlutterLocalization`
- `FlutterTheme`
- `FlutterChannel`
- `FlutterPlatformTarget`

Normalize the specification's edge names to repository conventions:

- `SEMANTIC_OF`, `CONTAINS`, `BUILDS`, `HAS_CHILD`
- `DECLARES_ROUTE`, `NAVIGATES_TO`
- `DEPENDS_ON`, `PROVIDES`, `CONSUMES`
- `LOADS`, `USES_THEME`, `READS_LOCALIZATION`
- `INVOKES_CHANNEL`, `HANDLED_BY_NATIVE`

Every node and edge must carry project scope, source evidence, stable IDs, confidence, and analyzer/protocol versions where applicable. Dynamic targets that cannot be resolved must be emitted as diagnostics or low-confidence unresolved references, not fabricated nodes.

### Incremental model

- Maintain an import/part/export dependency index and reanalyze changed Dart libraries plus reverse dependents.
- Treat `pubspec.yaml`, `analysis_options.yaml`, ARB files, assets, native manifests, and routing/state configuration as semantic invalidation roots.
- Stage writes and apply file-scoped tombstones for deleted sources only after successful parser completion.
- Preserve canonical Dart nodes when the Flutter overlay is disabled or fails.

## Phases

1. [Phase 01 - Define the contract and tool skeleton](phase-01-contract-and-skeleton.md)
2. [Phase 02 - Implement Dart parsing and resolution](phase-02-dart-parser-and-resolver.md)
3. [Phase 03 - Build the Flutter MVP semantic graph](phase-03-flutter-mvp-semantics.md)
4. [Phase 04 - Integrate incremental sync and MCP](phase-04-harness-and-mcp-integration.md)
5. [Phase 05 - Complete advanced semantics and hardening](phase-05-advanced-semantics-and-hardening.md)

## Cross-Plan Dependencies

- `neo4j-to-falkordb-migration` blocks provider-parity acceptance because Flutter adds schema/index and custom semantic writer coverage on the same graph abstraction.
- `260713-1638-framework-parser-integration` is a completed implementation pattern, not a blocker. Reuse its primary-parser-plus-overlay model and shared MCP registry instead of creating Flutter-only orchestration.
- Phases 01 and 02 can proceed while the graph migration is active; Phase 03 writes and Phase 04/05 parity gates must use the stabilized provider contract.

## Validation Strategy

- Create one deterministic fixture application containing multi-file widgets, nested navigation, assets/fonts, ARB localization, state providers, DI, and a platform channel with native stubs.
- Unit-test Python Dart visitors and the versioned fact protocol independently from graph writes.
- Contract-test normalized facts and stable IDs using an in-memory/fake graph driver.
- Run focused Python integration tests for parser discovery, ownership, incremental updates/deletes, graph schema, MCP routing/search/traversal, and provider isolation.
- Run live Neo4j and FalkorDB smoke tests only after the provider migration gate is available; document an explicit exclusion if either service is unavailable.
- Add a medium fixture performance baseline and verify that one-file changes do not trigger a full project rewrite.

## Success Criteria

### MVP gate

- `dev sync code --parsers dart,flutter` and auto-detection invoke the tool with the existing full/incremental CLI contract.
- Valid Dart files produce stable canonical symbols, cross-file imports/inheritance/references, and conservatively resolved project-local calls without duplicating nodes.
- The fixture reconstructs application entry, widget hierarchy, core navigation, pubspec assets/fonts, and asset usage.
- Existing MCP tools list `dart` and `flutter`, find Flutter semantic nodes, traverse widget/route/asset relationships, and preserve project scoping.
- Changed and deleted files update only affected Dart libraries and Flutter facts.
- Existing non-Flutter parser, framework, graph-provider, and unified MCP tests remain green.

### Spec-complete gate

- The fixture reconstructs state, dependency injection, theme/localization, route-package, and platform-channel graphs with source evidence.
- Method channels link to existing Android/iOS native symbols when resolvable and report uncertainty otherwise.
- Neo4j and FalkorDB produce equivalent logical nodes, edges, and query results for the fixture.
- Parser/runtime versions, setup, limitations, and troubleshooting are documented.

## Risks and Mitigations

| Risk                                   | Mitigation                                                                                                                            |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Dart grammar API churn                 | Pin a compatible `tree-sitter-dart` range; keep output behind a versioned fact contract; add import and protocol compatibility tests. |
| Large Flutter projects                 | Stream JSONL, reuse analysis contexts within one run, cache dependency state, and limit incremental reanalysis to reverse dependents. |
| Dynamic widget and route construction  | Require resolved elements and source evidence; emit confidence and unresolved diagnostics rather than guessing.                       |
| Generated code affects resolution      | Include generated units in resolver contexts, tag them, and suppress user-facing ownership unless configured.                         |
| Package-specific false positives       | Identify APIs by resolved package/library URI and element identity, not method/class names alone.                                     |
| Graph collisions                       | Use namespaced Flutter labels, project-scoped stable IDs, and `SEMANTIC_OF` links to canonical Dart symbols.                          |
| Provider-specific Cypher               | Write only through provider-neutral APIs and test logical parity before acceptance.                                                   |
| Parser failure during incremental sync | Stage output, validate schema, and apply writes/tombstones atomically after successful completion.                                    |

## Reference Material

- Source design: `/Users/account/Downloads/Flutter_Analyzer_Design_Spec.md`
- Python Dart grammar: https://pypi.org/project/tree-sitter-dart/
- py-tree-sitter API: https://tree-sitter.github.io/py-tree-sitter/
- Flutter navigation: https://docs.flutter.dev/ui/navigation
- Flutter assets: https://docs.flutter.dev/ui/assets/assets-and-images
