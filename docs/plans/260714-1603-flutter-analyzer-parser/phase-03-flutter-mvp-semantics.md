# Phase 03: Build the Flutter MVP Semantic Graph

## Context

The primary Dart graph explains language structure but not Flutter architecture. This phase adds the MVP overlay: application entry, widgets, widget hierarchy, core navigation, pubspec assets/fonts, and asset usage.

## Requirements

- Identify Flutter APIs by resolved element/library identity, not names alone.
- Link every semantic node to canonical Dart/File facts through `SEMANTIC_OF` or source ownership.
- Preserve source evidence and confidence for every inferred hierarchy or route edge.
- Parse `pubspec.yaml`, asset directories, fonts, ARB/JSON, and core asset-loading APIs.
- Add provider-neutral graph writes, schema indexes, deletion handling, and fixture contracts.

## Architecture

Python syntax-tree extractors emit framework-neutral source facts plus Flutter semantic facts. Python artifact readers handle YAML/JSON/XML/plist inputs. A Flutter graph builder resolves identities across both sources and writes namespaced nodes plus typed edges.

MVP extractors:

- entry: `main`, `runApp`, root `MaterialApp`/`CupertinoApp`/`WidgetsApp`;
- widgets: StatelessWidget, StatefulWidget, State pairing, `build`, constructor composition;
- hierarchy: evidence-backed parent/child relations from returned widget expressions and common collection children;
- navigation: `Navigator.push/pop/pushNamed`, `MaterialPageRoute`, `MaterialApp.routes`, `onGenerateRoute`, and generic Router configuration;
- assets: pubspec asset/font declarations, `Image.asset`, `AssetImage`, `rootBundle`/`AssetBundle` loads;
- graph: stable semantic IDs, indexes, overlay cleanup, and query-ready properties.

## Related Files

Create:

- `code-tiny/tools/flutter/flutter_api.py`
- `code-tiny/tools/flutter/widget_extractor.py`
- `code-tiny/tools/flutter/navigation_extractor.py`
- `code-tiny/tools/flutter/asset_usage_extractor.py`
- `code-tiny/tools/flutter/artifact_parser.py`
- `code-tiny/tools/flutter/graph_builder.py`
- `code-tiny/tools/flutter/writer.py`
- `tests/test_flutter_fixture_analysis.py`
- `tests/test_flutter_graph_contract.py`

Modify:

- `code-tiny/scripts/setup_constraints.py`
- `code-tiny/requirements.txt` if YAML parsing is not already a declared direct dependency.

## Implementation Steps

1. Define semantic label/property/relationship contracts and stable ID functions.
2. Detect application roots and connect `main -> runApp -> root widget` with source evidence.
3. Extract widget definitions, State pairings, build methods, and conservative child composition.
4. Extract core imperative, named, and generic Router navigation facts.
5. Parse pubspec assets/fonts and link declarations to discovered files and code usages.
6. Implement provider-neutral batch writes, indexes, file ownership, and tombstones.
7. Add contract tests that compare logical graph output independent of Neo4j/FalkorDB record shapes.

## Todo

- [ ] Approve namespaced Flutter labels and relationship names.
- [ ] Define confidence rules for dynamic widget children and route targets.
- [ ] Implement asset directory expansion exactly as Flutter documents it.
- [ ] Test duplicate route names, missing assets, resolution variants, and conditional widget branches.
- [ ] Verify overlay failure cannot delete canonical Dart data.

## Risks

- Widget composition is dynamic and cannot always be reconstructed statically.
- Builder callbacks and generated routes can hide concrete targets.
- Asset directories have Flutter-specific non-recursive semantics and resolution variants.

## Success Criteria

- The fixture reconstructs its root application, widget hierarchy, routes, declared assets/fonts, and code asset usage.
- Every Flutter semantic fact links to canonical source evidence and carries confidence.
- Duplicate or unresolved semantic targets are diagnosed, not silently merged.
- Incremental updates and deletions change only affected overlay facts.
- Logical graph contract tests pass against provider-neutral fake records.
