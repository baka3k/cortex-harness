# COBOL analyzer validation report

date: 2026-07-14
result: pass-with-provider-and-baseline-exclusions
review_score: 9.7/10
critical_findings: 0

## Delivered contract

The implementation provides a portable Tree-sitter runtime with native override preflight, deterministic schema-version-1 facts, fixed/free and legacy encoding evidence, structure/data/copybook resolution, paragraph CFG, static/dynamic calls, data/file access, SQL/CICS preservation, provider-neutral graph writes, project-scoped Qdrant indexing, incremental copybook fan-out, primary parser ownership, root CLI discovery, schema indexes, and scoped unified-MCP query profiles.

## Automated validation

- `python -m unittest discover -s tests -p 'test_cobol*.py' -v`: 21 tests passed.
- Shared discovery/ownership/framework/MCP regression selection: 12 tests passed.
- `python -m compileall -q code-tiny/tools/cobol cortex_harness`: passed.
- `uvx ruff check ...`: passed after removing three unused imports.
- `python -m pip check`: no broken requirements.
- `uvx pip-audit --local --format columns`: no known vulnerabilities.
- `git diff --check`: passed (line-ending conversion warnings only).
- CLI preflight: portable Windows grammar loaded from `tree-sitter-language-pack`, ABI 14.
- CLI dry run on the eight-file application fixture completed without graph/vector connections.

Coverage includes deterministic IDs/serialization, exact legacy byte evidence, all four owned extensions, custom copybook extensions, nested/cyclic/missing copybooks, reverse invalidation, static/dynamic calls, `PERFORM`/`PERFORM THRU` returns, dynamic `GO TO`, fall-through, `ALTER`, exits, file modes, data access intent, SQL/CICS recovery, malformed source, canonical Repository/File ownership, typed graph batches, Qdrant payload identity, parser aliases, MCP-scoped relationships, and manifest traversal rejection.

## Repository-wide baseline exclusions

The full repository discovery ran 91 tests with 7 platform skips. The COBOL tests and all directly affected shared tests passed. Twelve errors and three failures remain in the pre-existing Flutter/framework baseline because this checkout does not contain `tests/fixtures/flutter-protocol-v1.jsonl`, the Flutter application fixture, or the mixed framework fixture, and the active environment does not have `tree-sitter-dart` installed. None of those failures imports or executes the COBOL package or the modified registry branches.

## Provider exclusions

Live Neo4j was unavailable on `localhost:7687` during the repository test run, and no configured live FalkorDB/Qdrant validation target was authorized for mutation. Provider-neutral behavior is covered by capturing-driver tests, canonical writer calls, provider-aware schema definitions, stable typed rows, and existing FalkorDB driver regressions. Live Neo4j/FalkorDB logical parity remains the single unchecked Phase 05 item until the migration environment is available.

## Review decision

Approved. The mandatory review found and fixed canonical File ownership, unused copybook extension configuration, inaccurate deleted-file summary counts, legacy byte-offset drift, manifest traversal, Qdrant target validation, `PERFORM` continuation semantics, OPEN mode classification, and data read/write intent. No Critical/High/Medium security findings remain. The 0.3 score deduction reflects unavailable live cross-provider parity and the accepted operator-controlled native/model trust boundaries.
