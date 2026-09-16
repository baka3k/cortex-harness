# Phase 03 + Phase 04 progress — 2026-09-16

Status: code changes for Phase 03 (4 carve-outs: csharp, cobol, android-deferred)
+ Phase 04 (6 remaining: kotlin, delphi, vb, python, js, ts, php) DONE.

## Phase 03 — 4 carve-outs

### Patch 1 — analyzer-csharp Pattern A (DONE)
- `analyzer-csharp/src/graph.rs`: split `write_graph` into `build_payload`
  (always runs, captures `embedding_categories`) + `write_graph` (gated on
  writer).
- `analyzer-csharp/src/main.rs`: `build_graph_rows` + `build_payload` hoisted
  OUTSIDE `if let Some(store) = store_opt.take()`; emission moved AFTER.
  `files_selected` from `selected_rel_paths`, `files_deleted` from
  `deleted_rel_paths`.

### Patch 2 — analyzer-cobol Pattern B (DONE)
- `analyzer-cobol/src/pipeline.rs`: `write_graph_facts` signature changed from
  `&mut LanguageCodeWriter` to `Option<&mut LanguageCodeWriter>` + returns
  `(BTreeMap<String, i64>, Vec<(String, Vec<Value>)>)` — counts + categories.
  Categories derived from `nodes_by_label` BEFORE the writes; skips
  `relations`/`calls` per `documents_from_categories` skip rules.
- `analyzer-cobol/src/analyzer.rs`: removed `if args.graph_writes_disabled()
  { return Ok(0); }` short-circuit. Parse + categories build always run;
  persistence + cleanup gated on `writer.as_mut()`. Emission + SCAN_RESULT
  extension done OUTSIDE writer block.

### Patch 3 — analyzer-android Pattern B (DEFERRED)
- `assemble::write_graph` at `assemble.rs:671` is 1200+ lines using
  `write_nodes_batch` per-label extensively (no `WriteAllPayload`). Full
  refactor would require capturing `(label, rows)` before each batch +
  returning categories from `write_graph`. Out of scope for this commit;
  documented in `registry.rs` comment + `orchestrator.rs:3535` test
  (capability table excludes android).

### Patch 4 — analyzer-plsql (DONE — inherits sqlcommon)
- plsql binary at `analyzer-sql-family/src/bin/analyzer-plsql.rs:72` calls
  `sqlcommon::run(args, &plsql::analyzer_spec(), ...)` — already inherits
  Phase-02 hoisting. `spec.language_default = "plsql"` ⇒ parser="plsql" in
  emitted artifact. No additional code change.

### Patch 6 — Capability table (DONE)
- `cortex-sync/src/registry.rs:451-460`: `EMITTING_VECTOR_CLI_PARSERS`
  widened 22 → 23 (cobol added; android remains deferred).
- `cortex-sync/src/orchestrator.rs:3520-3540` test updated to match.

## Phase 04 — 6 remaining

### Patch 1 — analyzer-kotlin Pattern A (DONE)
- `analyzer-kotlin/src/kotlin_analyzer.rs`: `assemble_graph` + WriteAllPayload
  + `embedding_categories()` capture hoisted OUTSIDE `if let Some(store)
  = store.take()`; emission moved AFTER. `files_selected` from
  `selected_rel_paths`, `files_deleted` from `deleted_set` (no longer
  `Vec::new()` placeholder).

### Patch 2 — analyzer-delphi Pattern A (DONE in Phase 01)
- See `phase01-02-progress.md` §"Phase 01". Already populated from
  `selected_rel_paths` + `deleted_set`.

### Patch 3 — analyzer-vb Pattern A (DONE)
- `analyzer-vb/src/pipeline.rs`: row Vecs (files/namespaces/types/functions/
  relations/calls/properties/events/interfaces/enums/constants/variables) +
  payload → row loop + WriteAllPayload build + `embedding_categories()`
  capture hoisted OUTSIDE the writer block. `build_call_graph` signature
  gained `embedding_output: Option<&str>` parameter. Emission added AFTER
  the writer block; `files_selected` from `changed_files`, `files_deleted`
  from `deleted_files`.

### Patch 4 — analyzer-python Pattern C — Trait seam (DONE)
- `cortex-analyzer-framework/src/traits.rs`:
  - `AnalyzerResult` gained `embedding_categories: Vec<EmbeddingCategory>`
    field.
  - `AnalyzerContext` gained `emit_embedding_artifact(parser, categories)`
    helper that reads `args.embedding_input_output()` and calls
    `maybe_emit_embedding_artifact` with the standard emission contract.
  - `EmbeddingCategory` re-exported.
- `analyzer-python/src/python_analyzer.rs`: WriteAllPayload + categories
  hoisted OUTSIDE `if let Some(store) = ctx.store.take()`. Categories
  populated into `AnalyzerResult.embedding_categories`. `execute()` calls
  `ctx.emit_embedding_artifact("python", ...)` AFTER `run()` returns.

### Patch 5 — analyzer-js Pattern A (DONE)
- `analyzer-js/src/main.rs`: `JsArgs` gained `embedding_input_output:
  Option<String>` field.
- `analyzer-js/src/pipeline.rs`: `assemble_graph` + WriteAllPayload +
  `embedding_categories()` capture hoisted OUTSIDE the writer block;
  emission moved AFTER. `files_selected` from `selected_rel_paths`,
  `files_deleted` from `deleted_set`.

### Patch 6 — analyzer-ts Pattern A (DONE — partial emission)
- `analyzer-ts/src/pipeline.rs`: WriteAllPayload extracted into `let
  write_payload` binding BEFORE the `let Some(writer) = writer else {
  ... }` early return. Categories captured regardless of writer. Graphless
  branch prints SCAN_RESULT + returns; the embedding emission seam
  requires a `TsPipeline::args` field which doesn't exist (TS uses custom
  `TsPipeline`, not `AnalyzerContext`). Documented as TODO(phase-05):
  framework-side hook for non-`AnalyzerContext` TS pipeline. Categories
  are now captured (no longer inline-suppressed).

### Patch 7 — analyzer-php Pattern C — Trait seam (DONE)
- `analyzer-php/src/php_analyzer.rs`: WriteAllPayload + categories hoisted
  OUTSIDE `if let Some(store) = ctx.store.take()`. Categories populated
  into `AnalyzerResult.embedding_categories`. `execute()` calls
  `ctx.emit_embedding_artifact("php", ...)` AFTER `run()` returns.

## Build status

```
cargo build --workspace
   Finished `dev` profile (full Rust workspace builds clean)
cargo test -p cortex-sync
   85 unit + 4 lib + 2 integration tests PASS (handshake test updated for
   new capability table size 23).
cargo test -p analyzer-delphi
   9 existing + 3 graphless_emit tests PASS.
```

## Outstanding for Phase 05

- Per-analyzer integration test for java/sql/dart/csharp/cobol/kotlin/vb/js
  /python/php (Phase 02 §Patch 5 + Phase 03 §Patch 5 + Phase 04 §Patch 8
  call for 24 × 3 = 72 cumulative tests). Skipped to keep commit focused;
  will add as part of Phase 05 verification drill.
- procsample end-to-end drill (`dev sync code --full-scan` →
  vectors_upserted > 0). Requires orchestrator side running Qdrant locally;
  deferred to Phase 05 leg 1.
- analyzer-android emission (Phase 03 §Patch 3 — 1200-line `assemble::write_graph`
  refactor) — deferred.
- analyzer-ts emission seam (Phase 04 §Patch 6 — `TsPipeline` lacks `args`
  field; framework-side hook needed).