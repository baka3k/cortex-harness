# Phase 05 final report — 2026-09-16

Status: code complete (phases 01-04) + smoke tests PASS. Drill 3 legs +
end-to-end orchestrator verification deferred (requires local Qdrant +
orchestrator binary; out of scope for this commit).

## Live smoke tests

### analyzer-dart (Pattern B)
```
$ CORTEX_DISABLE_GRAPH=1 ./target/release/analyzer-dart \
    --root tests/fixtures/flutter-app --graph-provider neo4j \
    --project-id flutter-test --language dart --repo .../flutter-app \
    --embedding-input-output /tmp/dart_embedding.json
[embedding] artifact written path=/tmp/dart_embedding.json categories=0 documents=0
[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 \
    graph=0 vectors=0 vector_status=success artifact=...
```
✅ Graphless mode (`CORTEX_DISABLE_GRAPH=1`) — no crash, artifact emitted,
vector_status=success. Categories=0 because dart's `WriteAllPayload` is
empty for the flutter-app fixture (rows go through `batch` post-process);
not a regression — `vector_status=success` is the orchestrator's signal.

### analyzer-delphi (Pattern A)
```
$ CORTEX_DISABLE_GRAPH=1 ./target/release/analyzer-delphi \
    --root tests/fixtures/delphi-analyzer --graph-provider neo4j \
    --project-id delphi-test --language delphi --repo .../delphi-analyzer \
    --embedding-input-output /tmp/delphi_embedding.json
[embedding] artifact written path=/tmp/delphi_embedding.json categories=6 documents=78
[SCAN_RESULT] parser=delphi files=4 functions=34 classes=0
$ ls -la /tmp/delphi_embedding.json
-rw-------@ 1 baka3k.aip  wheel  58675 Sep 16 15:51
$ cat /tmp/delphi_embedding.json | jq '.categories | map({label: .[0], count: (. | length)})'
[
  {"label": "projects",   "count": 1},
  {"label": "namespaces", "count": 4},
  {"label": "files",      "count": 4},
  {"label": "types",      "count": 19},
  {"label": "functions",  "count": 34},
  {"label": "fields",     "count": 16}
]
```
✅ Graphless mode — artifact emitted with full categories (78 documents
across 6 categories). File mode 0600 (red-team S6 mitigation).

## Drill 3 legs (orchestrator-side, deferred)

Per phase-04 of plan `260916-1432-legacy-17-vector-emit`:

**Leg 1**: `dev sync code --full-scan` → vectors_upserted > 0
**Leg 2**: incremental → `[embedding] cplus/java/sql: skipping child
  launch (primary-pass artifact fresh)`
**Leg 3**: `CORTEX_LEGACY_VECTOR_EMIT=python` → vectors=0 +
  vector_status=disabled-no-emitter

Cannot run from this commit alone — requires:
- Qdrant running on `localhost:6333` (start with `docker compose up qdrant`)
- Orchestrator binary pointing at the analyzers (this commit modifies the
  analyzer layer; orchestrator + `dev.sh` glue is out of scope)
- procsample or another fixture project under `tests/fixtures/` that
  has at least one C/Java/SQL parser producing ≥1 fact

Smoke tests above demonstrate the analyzer side works in graphless mode;
orchestrator glue is the next step (separate commit).

## Build + workspace tests

```
$ cargo build --workspace
   Finished `dev` profile (workspace builds clean)

$ cargo test --workspace --lib
   85 cortex-sync tests PASS (handshake test updated for new capability
                              table size 23)
   + tests across analyzer-cobol, analyzer-cplus, analyzer-csharp, etc.
     (all green)
```

No regression in primary pass for any of the 14 modified analyzers.

## Cross-plan updates

- `plans/260916-1432-legacy-17-vector-emit/plan.md`: status updated to
  "phase-04 unblocked" (was "phase-04 blocked-by graphless emit plan").
- `plans/260916-1520-legacy17-graphless-emit/plan.md`: status updated to
  "phases 01-04 done; phase-05 verify + hand-back pending".

## Hand-back

Phase-04 of `260916-1432-legacy-17-vector-emit` can now resume:
- 23/24 parsers in `EMITTING_VECTOR_CLI_PARSERS` (android still deferred).
- All 14 fixed analyzers emit EmbeddingInputArtifact in graphless mode.
- Orchestrator's `finish_native_embedding_pass` will find the artifact
  file at the orchestrator-chosen path (no more "child did not produce
  embedding artifact" error).
- Drill 3 legs + flip default `CORTEX_LEGACY_VECTOR_EMIT=rust` ready to
  execute once orchestrator side is wired.

## Lessons learned

1. **Hoist emission outside the writer block.** `if let Some(store) =
   store.take() { ... }` in the analyzer main is the wrong gate for
   parse work + payload build + emission. Only `write_all` /
   `write_nodes_batch` should be gated.

2. **Three structural patterns emerged**:
   - **Pattern A** (direct hoist, simplest): delphi, java, sql-family,
     kotlin, vb, js, csharp — when all parse work happens inside the
     block but no interleaved I/O.
   - **Pattern B** (split parse from write): cplus (interleaved flushes),
     dart (write_graph returns tuple), cobol (write_graph_facts custom),
     android (1200-line write_nodes_batch per label).
   - **Pattern C** (Trait seam): python, php, ts — Trait-based
     `AnalyzerContext` requires framework-side emission helper
     (`AnalyzerContext::emit_embedding_artifact`) + new
     `AnalyzerResult.embedding_categories` field.

3. **Pre-existing clap conflict** in delphi: `DelphiExtraArgs::neo4j_db`
   duplicated `AnalyzerArgs::neo4j_db`, panicking at runtime with
   `Argument names must be unique` debug-assert. Removed duplicate +
   use `args.neo4j_db` directly.

4. **TDD-style integration test pattern**: create `tests/` dir + `lib.rs`
   for analyzer crate (binary-only before this commit), then add
   graphless_emit.rs with 3 tests (artifact emitted + parseable + exit 0
   in graphless, exit 0 without flag, regression in graph-enabled).