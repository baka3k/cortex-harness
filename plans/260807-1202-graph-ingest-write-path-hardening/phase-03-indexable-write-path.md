# Phase 03: Label-qualified, integrity-aware relationship writes

## Context

`LanguageCodeWriter.write_relations_typed()` claims to group by endpoint labels
but groups only by relationship type and emits unlabeled endpoint `MATCH`
clauses. Other generic writers repeat the same query shape. Creating indexes
alone cannot improve queries that do not reference the indexed labels.

## Requirements

- Every generic endpoint lookup names source and target labels.
- Dynamic identifiers are allowlisted and never accepted as arbitrary input.
- Grouping and checkpoint identity include both labels and relationship type.
- Missing/ambiguous endpoints are counted and governed by declared policy.
- Replaying a committed batch is idempotent and does not inflate counters.

## Architecture

Create a shared relationship-write compiler fed by validated manifest objects,
not raw string interpolation. It compiles provider-specific parameterized
queries and returns a `RelationshipBatchResult` with expected, matched,
created, updated, unchanged, and unresolved counts. Specialized writers can
remain only when their semantic merge key cannot use this contract.

## Related files

- `code-tiny/tools/graph/writer/language_writer.py`
- `code-tiny/tools/graph/writer/spring_writer.py`
- `code-tiny/tools/graph/writer/mybatis_writer.py`
- `code-tiny/tools/graph/writer/project_topology_writer.py`
- `code-tiny/tools/graph/operations/cross_edge_ops.py`
- `code-tiny/tools/android/android_java_analyzer.py`
- `code-tiny/tools/android/android_kotlin_analyzer.py`
- `code-tiny/tools/ts/ts_backend_analyzer.py`
- Other Phase 01 inventory hits under `code-tiny/tools/graph/writer/`
- New compiler/result modules under `code-tiny/tools/graph/`

## Implementation steps

1. Define the relationship row and endpoint identity contract, including
   project-scope behavior and optional-edge policy.
2. Group rows by `(source_label, target_label, relationship_type, identity
   mode)` and validate all identifiers against the active manifest.
3. Compile label-qualified endpoint matches and preserve relationship merge
   keys/properties without interpolating values.
4. Return and reconcile expected/matched/write counts. Fail a required batch
   before reporting progress when endpoints are missing or ambiguous.
5. Correct `LanguageCodeWriter.write_relations_typed()` and its older generic
   relationship path, then migrate Spring, MyBatis, topology, cross-edge,
   Android Java/Kotlin, and TypeScript backend mutations.
6. Add a static repository guard that rejects mutating Cypher containing an
   unlabeled identity `MATCH`/`MERGE`; permit exceptions only through a reviewed
   explicit polymorphic API.
7. Review specialized call-site writes: retain them only when labeled and
   indexable; otherwise move them to the shared compiler.
8. Version checkpoint/query-shape fingerprints and reject stale resume states.
9. Remove superseded unlabeled helpers after every caller is migrated; do not
   leave a silent fallback path.

## Todo

- [x] Implement strict relationship query compilation and result accounting.
- [x] Fix endpoint-label grouping and checkpoint identity.
- [x] Migrate every generic unlabeled endpoint writer from the inventory.
- [x] Add the static no-unlabeled-identity-mutation guard.
- [ ] Add required/optional unresolved-endpoint reconciliation.
- [x] Prove idempotent replay and project isolation.
- [x] Remove the old unlabeled fallback after migration.

## Risks

IDs may be unique only within a label or project. Using labels without the
declared project key can connect nodes across projects; adding uniqueness
constraints without auditing can fail or conceal existing ambiguity. Dynamic
labels/types must be registry values to avoid Cypher injection.

## Success criteria

No migrated write template performs a generic unlabeled identity upsert or
endpoint lookup;
expected relationship counts reconcile exactly; representative queries select
both endpoints by index; repeat execution leaves final counts unchanged.
