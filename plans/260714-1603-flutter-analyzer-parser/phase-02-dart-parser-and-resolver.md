# Phase 02: Implement Dart Parsing and Resolution

## Context

Flutter semantics depend on a reliable primary Dart graph. The Python grammar provides syntax structure; project-local indexes add conservative import, part, call, and inheritance resolution without regex parsing.

## Requirements

- Emit canonical language facts compatible with existing `LanguageCodeWriter` labels and properties.
- Resolve libraries, parts, imports, exports, types, inheritance, overrides, references, and call targets.
- Preserve diagnostics for partially invalid projects and continue when safe.
- Produce stable IDs independent of checkout location.
- Support changed/deleted manifests and reverse-dependent reanalysis.
- Use the existing project Qdrant collection for canonical Dart symbol embeddings.

## Architecture

The Python parser traverses syntax trees and emits source identities based on package URI, declaration kind, qualified name, and source range where needed. Project-local indexes resolve only unambiguous targets. The normalizer maps identities into CortexHarness IDs and canonical node/edge shapes before graph writes.

Generated sources are available to the analyzer context for resolution. Facts from `.g.dart`, `.freezed.dart`, and similar generated units are tagged and can be excluded from the visible graph while still serving as resolution targets.

## Related Files

Create:

- `code-tiny/tools/flutter/dart_parser.py`
- `code-tiny/tools/flutter/normalizer.py`
- `code-tiny/tools/flutter/pipeline.py`
- `code-tiny/tools/flutter/cache.py`
- `tests/test_dart_fixture_analysis.py`
- `tests/test_dart_incremental_resolution.py`

Reuse or update:

- `code-tiny/tools/graph/writer/language_writer.py` only if the canonical Dart shape exposes a verified missing generic operation.
- Existing Qdrant embedding utilities used by other primary language analyzers.

## Implementation Steps

1. Traverse Dart syntax trees and emit files, libraries, classes/mixins/extensions/enums, functions/methods/constructors, fields, parameters, and source locations.
2. Emit import/export/part, inheritance/implements/mixin, override, reference, and conservatively resolved call relationships.
3. Normalize display strings and IDs; include package URI and project scope while removing absolute checkout paths from identity.
4. Build a dependency index for imports/exports/parts and compute reverse dependents for incremental runs.
5. Add staged deletion handling that removes facts owned by deleted files only after successful reanalysis.
6. Map canonical facts into `LanguageCodeWriter` and the existing code-vector pipeline.
7. Test syntax errors, unresolved packages, conditional imports, parts, extensions, mixins, async functions, and generated files.

## Todo

- [ ] Define canonical mappings for Dart mixins, extensions, getters/setters, and constructors.
- [ ] Implement stable symbol IDs and collision tests.
- [ ] Implement reverse-dependency invalidation and cache versioning.
- [ ] Validate partial-analysis behavior on unresolved dependencies.
- [ ] Verify canonical writer and Qdrant payload compatibility.

## Risks

- Grammar node-shape changes can require extraction compatibility updates between pinned versions.
- Conditional imports and generated code can make one-file invalidation incomplete.
- Reusing generic labels may expose assumptions made by C++/Java-oriented queries.

## Success Criteria

- The fixture produces deterministic canonical Dart nodes and resolved cross-file edges.
- A change to one library reanalyzes its reverse dependents without rewriting unrelated libraries.
- Deleted files remove owned facts without deleting surviving targets.
- Invalid or unresolved files emit diagnostics while valid libraries still produce facts.
- Existing symbol lookup and semantic search can consume canonical Dart records.
