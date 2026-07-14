# Phase 01: Define the Contract and Tool Skeleton

## Context

CortexHarness invokes analyzers as Python scripts and expects graph-provider, incremental manifest, project, cache, and Qdrant arguments. This phase establishes a Python-only parser and stable fact boundary before implementing extractors.

## Requirements

- Add a Python analyzer entry point that matches the existing analyzer CLI contract.
- Add the pinned `tree-sitter-dart` Python grammar dependency.
- Define a versioned, streaming JSONL protocol with deterministic ordering.
- Detect Flutter projects from `pubspec.yaml` without scanning arbitrary Dart packages as Flutter.
- Fail clearly when the Python grammar dependency is unavailable or incompatible.
- Add the representative fixture and protocol golden files before graph writes.

## Architecture

The Python process owns parsing, syntax-tree visitors, conservative project-local resolution, environment/config normalization, staging, and graph integration. The versioned fact model is validated before any graph mutation.

Protocol records:

- `header`: schema version, analyzer version, SDK version, root, project ID;
- `node`: semantic kind, stable source identity, properties, evidence;
- `edge`: source identity, target identity, relationship, evidence, confidence;
- `diagnostic`: file/range, severity, code, message, recoverability;
- `summary`: processed/skipped/error counts and timing.

## Related Files

Create:

- `code-tiny/tools/flutter/__init__.py`
- `code-tiny/tools/flutter/flutter_analyzer.py`
- `code-tiny/tools/flutter/detector.py`
- `code-tiny/tools/flutter/protocol.py`
- `code-tiny/tools/flutter/models.py`
- `code-tiny/tools/flutter/dart_parser.py`
- `tests/fixtures/flutter-app/`
- `tests/test_flutter_analyzer_imports.py`
- `tests/test_flutter_protocol.py`
- `tests/test_flutter_project_detection.py`

Reference:

- `code-tiny/tools/android/android_kotlin_analyzer.py`
- `code-tiny/tools/spring/spring_analyzer.py`
- `code-tiny/tools/graph/cli.py`

## Implementation Steps

1. Define fixture expectations and the JSONL schema, including forward-compatible optional properties and hard rejection of unsupported major versions.
2. Add and pin a compatible `tree-sitter-dart` Python package range.
3. Implement project discovery using `pubspec.yaml` package metadata and a Flutter SDK dependency marker.
4. Implement the Python CLI adapter and direct parser invocation.
5. Add preflight validation for the Python grammar dependency.
6. Add deterministic protocol serialization and Python validation/golden tests.

## Todo

- [ ] Approve JSONL schema version `1` and required fields.
- [ ] Add a fixture with valid, invalid, generated, and part files.
- [x] Pin the `tree-sitter-dart` package compatibility range.
- [x] Implement Python grammar preflight behavior.
- [ ] Verify protocol output is stable across two identical runs.

## Risks

- Grammar releases may alter syntax node shapes and require visitor compatibility updates.
- `pubspec.yaml` parsing must not mistake a pure Dart package for Flutter.

## Success Criteria

- The Python grammar imports and parses the fixture without a Dart SDK.
- The Python adapter validates a complete protocol stream without touching a graph.
- Missing Python dependencies and malformed protocol records fail with deterministic, actionable errors.
- Golden protocol output is stable and includes source evidence and diagnostics.
