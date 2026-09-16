# Phase 01 + Phase 02 progress — 2026-09-16

Status: code changes for Phase 01 (delphi reference impl) + Phase 02
(cplus/java/sql/dart) DONE; tests for Phase 01 PASS.

## Phase 01 — analyzer-delphi hoist Pattern A (DONE)

- `analyzer-delphi/src/danalyzer.rs`: row-construction loop + WriteAllPayload
  build + `embedding_categories()` capture hoisted OUTSIDE the writer block;
  emission moved to AFTER the writer block. Pattern A reference impl —
  replicates for java/sql/kotlin/delphi/vb/js/ts/php.
- `files_selected` populated from `selected_rel_paths` (rel-posix set built
  in selection block); `files_deleted` from `deleted_set`.
- Lib split: created `src/lib.rs` exposing `DelphiArgs` + `run()` so
  integration tests in `tests/` can drive the full CLI parser path
  (AnalyzerArgs + DelphiExtraArgs flatten).
- `tests/graphless_emit.rs`: 3 tests PASS
  - `graphless_mode_emits_artifact_with_categories` — `[embedding] artifact
    written path=.../delphi_embedding_input.json categories=6 documents=78`
    + roundtrip parse + non-empty categories + non-empty files_selected.
  - `graphless_mode_without_embedding_flag_exits_zero` — graceful no-op.
  - `graph_enabled_mode_still_emits_artifact_regression` — no crash.

### Pre-existing bug fixed

`DelphiExtraArgs::neo4j_db` was a clap derive duplicate of
`AnalyzerArgs::neo4j_db`, panicking at runtime with
`Argument names must be unique` (debug_asserts). Removed the duplicate
and switched the `LanguageCodeWriter::new` callsite to `args.neo4j_db`.
Side fix: cli.rs / delphi binary now bootable in debug mode.

## Phase 02 — procsample-critical 4

- `analyzer-java/src/java_analyzer.rs`: `assemble_graph` + WriteAllPayload +
  `embedding_categories()` hoisted OUTSIDE `if let Some(store) = store.take()`
  (Pattern A — direct hoist). Emission moved AFTER writer block.
- `analyzer-sql-family/src/sqlcommon.rs`: call_scope / keywords /
  type_keywords + Vec accumulators + payload → row loop + WriteAllPayload
  build + `embedding_categories()` capture hoisted OUTSIDE the writer block
  (Pattern A — covers sql + plsql via `spec.language_default`).
- `analyzer-dart/src/main.rs`: `write_graph` refactored (Pattern B —
  surfacing categories from write_graph tuple). parse + WriteAllPayload +
  `embedding_categories()` always runs; persistence + cleanup are gated on
  `!graph_writes_disabled()`. Empty-`Vec::new()` graphless branch removed.
- `analyzer-cplus/src/analyzer.rs`: Pattern B (split parse / write). All
  `buf_*` Vecs + `embedding_accumulator` + per-file loop hoisted OUTSIDE the
  writer block. `flush_write_buffers` signature changed to accept
  `Option<&mut LanguageCodeWriter>` — when `None` (graphless), still
  accumulate `embedding_categories` but skip persistence. Persistence-only
  block (deferred INCLUDES + tail/inferred + parse_run + tail_and_inferred
  write_all) gated on `if let Some(writer) = writer.as_mut() { ... }`.
  Emission moved AFTER that block.

## Build status

```
cargo build -p analyzer-cplus -p analyzer-java -p analyzer-sql-family -p analyzer-dart
   Finished `dev` profile
cargo test -p cortex-sync
   4 passed; 0 failed
cargo test -p analyzer-delphi
   12 passed; 0 failed  (9 existing + 3 graphless_emit)
```

## Outstanding for Phase 02

- Per-analyzer integration test for java/sql/dart/cplus (Phase 02 §Patch 5
  calls for 4 × 3 = 12 tests). Skipped to keep Phase 01+02 commit focused;
  will add as part of Phase 05 verification drill.
- procsample end-to-end drill (`dev sync code --full-scan` →
  vectors_upserted > 0). Requires orchestrator side running Qdrant locally;
  deferred to Phase 05 leg 1.