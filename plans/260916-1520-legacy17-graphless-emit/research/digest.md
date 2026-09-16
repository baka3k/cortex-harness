# Research digest — refactor 11 legacy + 4 carve-out analyzer mains to emit `EmbeddingInputArtifact` in graphless mode (plan `260916-1520-legacy17-graphless-emit`)

Researched 2026-09-16 on branch `feat/change-db`. All refs are absolute paths. Evidence-first; unknowns flagged.

---

## Background recap (verified)

- Orchestrator embedding pass sets `CORTEX_DISABLE_GRAPH=1` and overrides `CODE_GRAPH_PROVIDER=neo4j` (`~/REDACTED_HOST/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:3128-3142`).
- Child env: child calls `args.graph_writes_disabled()` (`~/REDACTED_HOST/AI/cortex-harness/rust/crates/cortex-analyzer-framework/src/cli.rs:237-246`) → true → `open_store()` skipped → `Option<Box<dyn GraphStore>>` is `None`.
- Each analyzer gates the entire graph-write (and the artifact emission!) inside `if let Some(store) = store.take()` (or equivalent) → in graphless mode the whole block is skipped → no parse, no rows, no artifact.
- Prior plan `260916-1432-legacy-17-vector-emit` phases 01-03 wired the `WriteAllPayload::embedding_categories()` emission **inside** the writer block (see `phase01-analyzer-write-all-locations.md`). This worked for graph-enabled primary pass but is unreachable when embedding pass re-launches with `CORTEX_DISABLE_GRAPH=1`.
- Orchestrator's reader (`finish_native_embedding_pass`, `orchestrator.rs:2793-2884`) expects a file at the path passed via `--embedding-input-output` (set at `orchestrator.rs:2265-2268`).
- Capability table `EMITTING_VECTOR_CLI_PARSERS` already lists 22 parsers (incl. 11 legacy + dart + plsql): `~/REDACTED_HOST/AI/cortex-harness/rust/crates/cortex-sync/src/registry.rs:451-455`. Carve-outs remaining: `cobol` + `android` (per registry.rs:446-447 comment).

---

## Common contract (every analyzer needs)

1. `embedding_artifact::maybe_emit_embedding_artifact(output_path, emission)` — `~/REDACTED_HOST/AI/cortex-harness/rust/crates/cortex-analyzer-framework/src/embedding_artifact.rs:145-176`. No-op if `output_path` empty/whitespace; atomic 0600 write.
2. `args.embedding_input_output() -> Option<&str>` — `cli.rs:208-213`.
3. `EmbeddingEmission { parser, project_id, root_scope, full_replace, scanned_directory, files_selected, files_deleted, categories }` — `embedding_artifact.rs:48-58`.
4. `WriteAllPayload::embedding_categories()` — `~/REDACTED_HOST/AI/cortex-harness/rust/crates/cortex-graph-writer/src/language_writer.rs:2069-2111`. Returns `Vec<(String, Vec<Value>)>` — declaration order, empty categories dropped, relations/calls excluded.

Required refactor pattern for each analyzer:

```
// HOISTED OUTSIDE the writer block:
let payload = build_write_all_payload(...);     // may build empty payload if no parse ran
let categories = payload.embedding_categories();
let files_selected = ...;
let files_deleted  = ...;

// INSIDE the writer block (unchanged):
if let Some(store) = store.take() {
    let counts = writer.write_all(&payload)?;
    // (no artifact emit here)
}

// AFTER the writer block (runs in both modes):
if let Some(output) = args.embedding_input_output() {
    maybe_emit_embedding_artifact(Some(output), EmbeddingEmission {
        parser, project_id, root_scope,
        full_replace: !args.incremental,
        scanned_directory: true,
        files_selected, files_deleted,
        categories,
    })?;
}
```

The parse + assemble_graph + WriteAllPayload-build MUST happen unconditionally; only `write_all` stays gated.

---

## Findings (high-level cross-cutting)

1. **Every analyzer's primary pass already wires `maybe_emit_embedding_artifact`** — except where emission is gated inside the writer block (causing the graphless gap). Phase-02 of `260916-1432` left emission inside; **this plan moves it outside**.
2. **Two analyzers (python, php) intentionally deferred emission with `let _ = embedding_categories;`** — see `python_analyzer.rs:262`, `php_analyzer.rs:227`. Same for `vb` (`pipeline.rs:450`), `js` (`pipeline.rs:780`), `ts` (`pipeline.rs:1079`, "deferred to phase-03 widening"), `dart` (which DOES emit but with empty categories when graphless — `main.rs:688-689`).
3. **Three analyzers already partially moved work outside** — `java`/`sql`/`kotlin`/`delphi` parse BEFORE the writer block, but `assemble_graph` + payload construction happens inside. `cplus` is the most invasive (parse interleaved with streaming flushes).
4. **No analyzer currently reads the orchestrator's `CORTEX_DISABLE_GRAPH=1` and skips parsing** — they all do the work, just don't emit. So the fix is structural, not algorithmic.
5. **`scanned_directory: true` is hard-coded everywhere today** — phase-01 contract. Keep this value for plan `260916-1520`.
6. **`full_replace: !args.incremental`** is the uniform convention. `args.incremental` is a clap flag, present in all 11+4.
7. **Cleanup before parse is conditional on `store.is_some()`** — e.g. `kotlin_analyzer.rs`, `python_analyzer.rs:107-110`, `java_analyzer.rs:193-211`. These already gracefully skip when graphless — good.
8. **Dart is the only analyzer that splits embedding categories via a return tuple** — `write_graph` returns `(counts, embedding_categories)` (`main.rs:412-417`) and `run` consumes the tuple (`main.rs:688-698`). With graphless, the early-return returns empty `Vec` → artifact is wasted. Fix: build categories from `batch` (the `normalizer::normalize_facts` output) BEFORE the graphless branch.

---

## Per-analyzer structure (11 legacy + 4 carve-outs)

### 1. analyzer-cplus — Complexity **5 (major)**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-cplus/src/analyzer.rs`
- Main signature: `fn execute(args: &AnalyzerArgs, extra: &CplusExtraArgs) -> Result<i32, String>` (binary `analyzer-cplus/src/main.rs`).
- Store init: `:304-308` (`let store = if args.graph_writes_disabled() { None } else { Some(args.open_store()...)? };`).
- Writer block: `if let Some(mut writer) = writer {` at `:872` (variable name shadows — the local `writer` is built fresh; the outer is the `Option<Box<dyn GraphStore>>`).
- `buf_*` declarations (must be hoisted outside writer block): `buf_files, buf_namespaces, buf_types, buf_function_types, buf_functions, buf_fields, buf_aliases, buf_templates, buf_resources, buf_resource_elements, buf_relations, buf_possible_calls, buf_unknown_calls, files_in_buf, embedding_accumulator` at `:875-891`.
- Current emission: `:1537-1559` — **inside** writer block → unreachable when graphless.
- Streaming flushes: per-file loop `:913+` interleaves parse + buffer push + `flush_write_buffers` (`:1575-1741`). Two `write_all` sites (`:1497` tail+inferred, `:1622` streaming).
- **Refactor needed**: the entire `for (_rel_path, payload) in &raw_payloads` loop + `flush_write_buffers` calls + `tail_and_inferred::write_all` must run regardless of writer. Move the `embedding_accumulator.extend(payload.embedding_categories())` (`:1659`) outside; capture `(counts, embedding_accumulator)` from the writer block; emit artifact after.
- Risk: 1700+ lines, two `write_all` sites, deferred `inferred_relations`/`inferred_types` — must guard `LanguageCodeWriter::new` call.
- Files selected/deleted: `selected` set from incremental + `all_rel_paths` (`:1537-1542`); `deleted_files` (`:1552`).

### 2. analyzer-java — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-java/src/java_analyzer.rs`
- Main signature: `fn execute(args: &AnalyzerArgs, extra: &JavaExtraArgs) -> Result<i32, String>`.
- Store init: `:67-71`.
- Writer block: `if let Some(store) = store.take()` at `:285`.
- Parse BEFORE block: `:213-282` (`assemble_graph` consumed at `:292`). Wait — `assemble_graph` is INSIDE the writer block.
- Current emission: `:354-371` — inside writer block.
- **Refactor**: hoist `assemble_graph` + `WriteAllPayload` build + `embedding_categories` capture OUTSIDE the writer block. Only `writer.write_all(&payload)` + count handling stays inside.
- Files: `selected_rel_paths.iter().cloned().collect()` (`:363`), `deleted_set.iter().cloned().collect()` (`:364`).

### 3. analyzer-sql-family (sql + plsql) — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-sql-family/src/sqlcommon.rs`
- Main signature: `pub fn run(args, spec, call_scope, neo4j_batch_size) -> Result<i32, String>` at `:798`.
- Store init: `:811-815`.
- Writer block: `if let Some(store) = store_opt.take()` at line just before `:1186`.
- `WriteAllPayload` build: `:1192-1226`. `payload.embedding_categories()` at `:1229`.
- Current emission: `:1235-1256` — inside writer block.
- **Refactor**: hoist `WriteAllPayload` build + `embedding_categories` outside. Move `maybe_emit_embedding_artifact` call out.
- Files: `selected_files` (`:1236-1239`), `deleted_set` (`:1249`).
- plsql reuses sqlcommon: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-sql-family/src/bin/analyzer-plsql.rs:72` calls `sqlcommon::run(...)`. Fix lands in sqlcommon → plsql inherits.
- Parser value: `spec.language_default` (`:1243`).

### 4. analyzer-dart — Complexity **4**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-dart/src/main.rs`
- Main signature: `fn run(args: &DartArgs) -> i32` at `:504`.
- Store: `fn open_store(args)` at `:172-177` returns `Option<Box<dyn GraphStore>>`. Caller stores in `let store = open_store(args)?;` at `:280`.
- Writer block: `let (counts, embedding_categories) = if graph_writes_disabled() { (BTreeMap::new(), Vec::new()) } else { match write_graph(...) }` at `:688-698`. **This is the structural bug for graphless**: the empty `Vec::new()` means artifact is emitted with NO categories.
- `write_graph` at `:412-502` does: open store → `LanguageCodeWriter::new` → `normalizer::normalize_facts` (parses) → builds `WriteAllPayload` → `payload.embedding_categories()` → `writer.write_all`.
- Current emission: `:704-739` — OUTSIDE writer block (good), but categories are empty in graphless (bad).
- **Refactor**: split `write_graph` into `parse_and_build_payload` (always runs, returns `batch` + `embedding_categories`) and `persist_payload` (writer-only). In graphless branch, only call the parse half; emit artifact with real data.
- Files: `changed.iter().cloned().collect()` (`:718`), `deleted.iter().cloned().collect()` (`:719`).

### 5. analyzer-kotlin — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-kotlin/src/kotlin_analyzer.rs`
- Main signature: `pub fn execute(args: &AnalyzerArgs, extra: &KotlinExtraArgs) -> Result<i32, String>` at `:60`.
- Store init: `:68-72`.
- Writer block: `if let Some(store) = store.take()` at `:311`.
- `WriteAllPayload` build: `:346-380` (inside writer block). `embedding_categories()` at `:382`.
- Current emission: `:393-410` — inside writer block.
- **Refactor**: same pattern as java — hoist payload build + categories out, move emission out.
- Files: `files_selected: Vec::new()`, `files_deleted: Vec::new()` at `:402-403` — **placeholder**, must fix to use `selected_rel_paths` and `deleted_set`.

### 6. analyzer-delphi — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-delphi/src/danalyzer.rs`
- Main signature: `pub fn execute(args, extra) -> Result<i32, String>` at `:180`.
- Store init: `let store = open_store(args)?;` at `:280` (custom wrapper `open_store` at `:172-177`).
- Writer block: `if let Some(store) = store.take()` at `:535`.
- `WriteAllPayload` build: `:688-731` (inside writer block).
- Current emission: `:738-754` — inside writer block. **Files-selected/deleted both `Vec::new()`** at `:747-748` — must fix.
- **Refactor**: hoist `WriteAllPayload` build (or `all_*` Vec declarations `:551-556`) outside. The `all_files_nodes`/`all_namespaces`/etc. are populated from `payloads` inside the writer block — hoist those too OR refactor `payloads` (already built at `:404-516`) so the row-construction loop moves outside.

### 7. analyzer-vb — Complexity **4**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-vb/src/pipeline.rs`
- Main signature: `pub fn execute(base: &AnalyzerArgs, extra: &VbExtraArgs) -> Result<i32, String>` (`pipeline.rs:1`).
- Store init: `:100-104`.
- Writer block: `if let Some(store) = store.take()` at `:284`.
- Row Vecs (must hoist): `files_rows, namespaces_rows, types_rows, functions_rows, relations_rows, calls_rows, properties_rows, events_rows, interfaces_rows, enums_rows, constants_rows, variables_rows` at `:285-296` (ALL inside writer block).
- `WriteAllPayload` build: `:406-440` (inside writer block).
- Current emission: `let _ = embedding_categories;` at `:450` (suppressed). Comment at `:444-446` notes "caller (`analyzer-vb/src/main.rs`) handles emission via the returned categories — out of scope for this minimal patch".
- **Refactor**: hoist row-Vecs + payload build outside; emit artifact after. The caller for vb is `analyzer-vb/src/lib.rs::run_binary` at `:41` — needs `args.embedding_input_output()` propagated through.
- Files: `files_selected` / `files_deleted` need population from `changed_files` + `deleted_files` (`:158-159` of `build_call_graph`).

### 8. analyzer-python — Complexity **4**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-python/src/python_analyzer.rs`
- Main signature: `fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String>` at `:57` (Trait/AnalyzerContext pattern).
- Store: `ctx.store: Option<Box<dyn GraphStore>>` set by `cortex-analyzer-framework::cli::run_python_analyzer` adapter.
- Writer block: `if let Some(store) = ctx.store.take()` at `:216`.
- `assemble_graph` at `:203` (BEFORE block — good).
- `WriteAllPayload` build: `:221-255` (inside writer block). `embedding_categories()` at `:259`.
- Current emission: `let _ = embedding_categories;` at `:262`. `// TODO(phase-03)` comment at `:261`.
- **Refactor**: hoist payload build outside writer block; move emission into `AnalyzerResult.summary_json` or post-write step (since `run` returns `AnalyzerResult`, not `i32`). **Tricky**: the trait pattern may require framework-side hook. Check `analyzer-framework::cli::run_python_analyzer` for emission seam.
- Cleanup before parse: `:107-110` already conditional on `ctx.store.is_some()` (good).

### 9. analyzer-js — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-js/src/pipeline.rs`
- Main signature: `pub fn execute(args, extra) -> Result<i32, String>` (file head).
- Writer block: `if let Some(store) = store.take()` at `:724`.
- `WriteAllPayload` build: `:744-778` (inside). `let _embedding_categories = payload.embedding_categories();` at `:780` (suppressed).
- **Refactor**: hoist payload + categories out; emit after.

### 10. analyzer-ts — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-ts/src/pipeline.rs`
- Main signature: `fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String>` at `:56` (Trait pattern, like python).
- Writer block uses inline `WriteAllPayload { ... }` literal at `:1041`.
- Comment: `:1078-1079` — "TS uses inline WriteAllPayload above; deferred to phase-03 widening".
- **Refactor**: extract the `WriteAllPayload` build into a let before the write_all call; capture `embedding_categories`; emit in the result/return path. Same Trait/AnalyzerContext concern as python.

### 11. analyzer-php — Complexity **3**

- File: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-php/src/php_analyzer.rs`
- Main signature: `fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String>` at `:56` (Trait pattern, like python).
- Writer block: `if let Some(store) = ctx.store.take()` at `:165`.
- `WriteAllPayload` build at `:186-220`. `let _embedding_categories: Vec<(String, Vec<serde_json::Value>)> = Vec::new();` at `:227` (DEFERRED, EMPTY — worse than suppressed).
- **Refactor**: replace the empty placeholder with real `payload.embedding_categories()`; emit artifact via the AnalyzerResult path.

### 12. analyzer-csharp (carve-out) — Complexity **3**

- Files: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-csharp/src/main.rs` + `graph.rs`
- Main signature: `fn run(common: &AnalyzerArgs, extra: &CsharpExtraArgs) -> i32` at `main.rs:106`.
- Store init: `main.rs:162-172` (handles open errors gracefully).
- Writer block: `if let Some(store) = store_opt.take()` at `main.rs:282`.
- `write_graph` at `graph.rs:493-534` builds `WriteAllPayload { ... }` and calls `writer.write_all(&payload)` at `:532`. **Currently no embedding_categories call.**
- `[SCAN_RESULT]` at `main.rs:329-330` hard-codes `vectors=0 vector_status=disabled`.
- **Refactor**: extract `WriteAllPayload` build into a local in `write_graph`; capture `embedding_categories` and return them from `write_graph` (or pass back via tuple). Emit artifact in `main.rs` after the writer block.

### 13. analyzer-cobol (carve-out) — Complexity **5 (major)**

- Files: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-cobol/src/{analyzer.rs, pipeline.rs, main.rs}`
- Main signature: `fn execute(args, extra) -> i32` in `analyzer.rs`.
- Store init: `analyzer.rs:323` `args.open_store()` (only inside `write_graph`).
- Writer block / gate: `write_graph` at `analyzer.rs:312-` returns `Ok(0)` early at `:320-322` if `args.graph_writes_disabled()`. **Returns BEFORE parsing** → no data, no artifact possible.
- `write_graph_facts` at `pipeline.rs:313-386` builds `nodes_by_label: BTreeMap<String, Vec<Row>>` (`:322`) and calls `writer.write_nodes_batch` per label (`:379`).
- **Refactor (hard)**: split `write_graph_facts` so the `nodes_by_label` building happens regardless. Capture `nodes_by_label` from the function, derive `embedding_categories` from it (filter `relations`/`calls`, map `Row` → `Value::Object`), return the categories. Emit artifact in `analyzer.rs::write_graph` after the writer block — but `write_graph` must first stop short-circuiting on graphless.
- Pattern per phase01 report (`~/REDACTED_HOST/AI/cortex-harness/plans/260916-1432-legacy-17-vector-emit/reports/phase01-analyzer-write-all-locations.md` §"Pattern B").

### 14. analyzer-android (carve-out) — Complexity **5 (major)**

- Files: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-android/src/{main.rs, assemble.rs}`
- Main signature: `fn execute(args, extra) -> Result<i32, String>` at `main.rs:148`.
- Store init: `main.rs:156-160`.
- Writer block: `if let Some(writer) = writer {` at `main.rs:601` (note: variable shadowing — outer is `Option<Box<dyn GraphStore>>` named `store`; inner is `LanguageCodeWriter`).
- `assemble::write_graph` at `assemble.rs:671-` is huge (~1200 lines); uses `write_nodes_batch` per label extensively (e.g. `:1905`).
- No existing `embedding_categories` helper.
- **Refactor (hard)**: either (a) refactor `assemble::write_graph` to collect rows into a `Vec<(String, Vec<Row>)>` before each `write_nodes_batch` and return them, OR (b) duplicate the row-collection logic in `main.rs`. Option (a) is cleaner. Then emit artifact in `main.rs` after the writer block, but only if `embedding_accumulator` was populated.

### 15. analyzer-plsql (carve-out) — Complexity **1 (trivial, shared)**

- Binary: `~/REDACTED_HOST/AI/cortex-harness/rust/crates/analyzer-sql-family/src/bin/analyzer-plsql.rs`
- Calls `sqlcommon::run(args, &plsql::analyzer_spec(), &call_scope, batch_size)` at `:72`.
- **Inherits fix from sqlcommon (#3 above)**. No additional code change needed.
- Verify `spec.language_default == "plsql"` for `EmbeddingEmission.parser`.

---

## Cross-cutting variables (per analyzer)

| Var | Source | Used in | Status |
|---|---|---|---|
| `parser` | string literal | `EmbeddingEmission.parser` | OK |
| `project_id` | `scope.project_id` / `result.project_id` / `project_id` | `EmbeddingEmission.project_id` | OK |
| `root_scope` | `repo` / `scope.repo` | `EmbeddingEmission.root_scope` | OK |
| `args.incremental` | clap flag, exists in all 11+4 | `full_replace: !args.incremental` | OK |
| `scanned_directory` | hardcoded `true` today | `EmbeddingEmission.scanned_directory` | OK |
| `files_selected` | rel_posix paths from `selected_rel_paths` / `selected` set | `EmbeddingEmission.files_selected` | **delphi/vb/kotlin** use `Vec::new()` placeholder — must fix |
| `files_deleted` | `deleted_set` / `deleted_files_manifest` paths | `EmbeddingEmission.files_deleted` | **delphi/vb/kotlin** use `Vec::new()` placeholder — must fix |
| `payload.embedding_categories()` | `WriteAllPayload::embedding_categories()` | `EmbeddingEmission.categories` | python/vb/js/ts use suppressed/empty placeholder — must fix |

---

## Risks / open questions

| # | Severity | Item |
|---|---|---|
| R1 | high | **Custom write function for cobol + android**: no `WriteAllPayload` is built. Must derive categories from `nodes_by_label` (cobol) or collect rows per label (android). Pattern per phase01 report §"Pattern B" (cobol) and §"Pattern C" (android). |
| R2 | high | **Trait/AnalyzerContext pattern (python/ts/php)**: emission happens in `AnalyzerResult`-returning `run`. Need framework-side hook or post-process in `run_python_analyzer`/`run_*` adapter. Inspect `cortex-analyzer-framework::cli` for emission seam. |
| R3 | medium | **`files_selected`/`files_deleted` placeholders**: delphi (`:747-748`), vb (not yet populated), kotlin (`:402-403`) currently emit `Vec::new()`. Fix when hoisting. |
| R4 | medium | **Dart graphless empty categories**: `main.rs:689` returns `Vec::new()` for embedding_categories when `graph_writes_disabled`. Refactor split (parse always, write conditionally) required. |
| R5 | medium | **Cplus interleaving**: parse work is interleaved with streaming flushes. Refactor must move `flush_write_buffers` calls and the per-file loop outside the writer block. Risk of subtle behavior changes. |
| R6 | medium | **Two write_all sites in cplus**: `:1497` (tail+inferred) and `:1622` (streaming). `embedding_accumulator` at `:1659` must capture both. |
| R7 | low | **Orchestrator re-launch short-circuit**: `orchestrator.rs:2277-2290` (`skip_child`) is already gated on `legacy_emit_enabled() && should_skip_embedding_child_relaunch(...)` — when artifact exists. Once analyzers emit artifact in graphless, this short-circuit becomes live (no separate orchestrator change needed). |
| R8 | low | **EMITTING_VECTOR_CLI_PARSERS already has 22 entries** (`registry.rs:451-455`) — orchestrator side already widened; this plan only touches analyzers. |
| R9 | low | **`vector_status` semantics**: orchestrator at `orchestrator.rs:2185-2192` already maps `(qdrant_url, native_parser) → vector_status`. With artifact emission working, `native_parser = true` ⇒ `pending` ⇒ `"success"` after `finish_native_embedding_pass`. No change needed here. |
| R10 | low | **Cleanliness gate**: `cleanup_graph_files` (kotlin/java/python/csharp) is already gated on `store.is_some()` — graphless path skips cleanup. No change. |
| R11 | low | **Plsql alias**: `analyzer-spec.language_default` must equal `"plsql"` — verify at `plsql::analyzer_spec()`. |
| R12 | unknown | **Analyzer-context emission seam for python/ts/php**: confirm whether `cortex_analyzer_framework::cli::run_python_analyzer` (and TS/PHP equivalents) reads `--embedding-input-output` from args and calls `maybe_emit_embedding_artifact` post-`run()`. If yes, the trait analyzers only need to surface `embedding_categories` via `AnalyzerResult`. |

---

## Verification matrix (per-analyzer gating)

- [ ] cplus: parse work + flushes hoisted outside writer block; artifact emitted after (catches `embedding_input_output` flag in graphless).
- [ ] java: `assemble_graph` + payload + categories hoisted; emission moved out.
- [ ] sql-common: payload + categories hoisted; emission moved out.
- [ ] plsql: inherits sql-common.
- [ ] dart: `write_graph` split into parse-only and write-only; categories populated in graphless.
- [ ] kotlin: payload + categories hoisted; emission moved out; `files_selected`/`files_deleted` populated.
- [ ] delphi: row-Vecs + payload hoisted; emission moved out; `files_selected`/`files_deleted` populated.
- [ ] vb: row-Vecs + payload + categories hoisted; emission wired (currently `let _ = embedding_categories;` placeholder).
- [ ] python: `WriteAllPayload` build hoisted; categories populated; emission surfaced via `AnalyzerResult` or framework-side hook.
- [ ] js: payload + categories hoisted; emission wired.
- [ ] ts: extract payload let; categories populated; emission surfaced via `AnalyzerResult`.
- [ ] php: replace empty `Vec::new()` placeholder with `payload.embedding_categories()`; emission surfaced.
- [ ] csharp: `write_graph` returns categories; emission wired in `main.rs`.
- [ ] cobol: `write_graph_facts` returns `nodes_by_label` (or categories derived); `write_graph` stops short-circuiting in graphless; emission wired.
- [ ] android: `assemble::write_graph` collects rows per label; returns categories; emission wired in `main.rs`.

---

## Recommended approach (3 lines)

1. **Hoist `WriteAllPayload` build + `embedding_categories()` capture outside every analyzer's `if let Some(store)` gate; move `maybe_emit_embedding_artifact` call out of the writer block to AFTER it** — this single pattern covers java/sql/plsql/kotlin/delphi/vb/js/ts/php and matches phase01 patterns A & D.
2. **Split parse from write** for cplus (interleaved flushes), dart (write_graph tuple), cobol (write_graph_facts custom), android (assemble.rs ~1200 lines): build `categories` from the pre-write buffer regardless of graphless, gate only the `write_all` / `write_nodes_batch`.
3. **For Trait/AnalyzerContext analyzers** (python/ts/php), surface `embedding_categories` via `AnalyzerResult` and add a post-`run` emission seam in `cortex-analyzer-framework::cli::run_python_analyzer` (and TS/PHP equivalents) — confirm seam exists in `cli.rs` before coding.