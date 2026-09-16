# Phase 04 — 6 remaining analyzers (kotlin + delphi + vb + python + js + ts + php)

## Mục tiêu

Apply emission pattern cho 6 analyzers còn lại chưa được patch phase-02:
- `analyzer-kotlin` (Pattern A)
- `analyzer-delphi` (đã patch phase-01 — verify + populate files_*)
- `analyzer-vb` (Pattern A)
- `analyzer-python` (Pattern C — Trait/AnalyzerContext seam)
- `analyzer-js` (Pattern A)
- `analyzer-ts` (Pattern A)
- `analyzer-php` (Pattern A)

Note: phase-01 đã patch delphi với placeholder Vec::new() cho files_selected;
phase-04 này populate đúng từ scope vars.

## Scope

### Patch 1 — analyzer-kotlin (Pattern A)

Trong `analyzer-kotlin/src/kotlin_analyzer.rs`:
- Hoist `WriteAllPayload` build OUTSIDE `if let Some(store) = ... { ... }` block
  (currently line ~346)
- Hoist emission block OUTSIDE
- Populate `files_selected`/`files_deleted` từ `selected_files`/`deleted_set`
  (mirror java pattern)

### Patch 2 — analyzer-delphi (Pattern A — populate placeholders)

Trong `analyzer-delphi/src/danalyzer.rs`:
- Phase-01 patched emission OUTSIDE writer block but used `Vec::new()` for
  `files_selected`/`files_deleted`. Phase-04 populate từ:
  - `files_selected`: `selected_paths: Vec<String>` (verify availability) hoặc
    `selected_paths: BTreeSet<String>`
  - `files_deleted`: `deleted_paths: Vec<String>` (verify availability)
- Re-test phase-01 unit tests with populated values (assertion: file_count > 0
  when incremental mode)

### Patch 3 — analyzer-vb (Pattern A)

Trong `analyzer-vb/src/pipeline.rs::build_call_graph`:
- Currently returns counts; verify if WriteAllPayload built
- If yes: hoist emission; populate files_selected/files_deleted
- If no: build WriteAllPayload + categories first (mirror java)

### Patch 4 — analyzer-python (Pattern C — Trait seam)

Trong `analyzer-python/src/python_analyzer.rs::Analyzer::execute`:
- Currently uses `AnalyzerContext` (Trait-based); verify ctx has `embedding_input_output`
  accessor (via `args` field)
- Find where data is accumulated before `writer.write_all(&payload)` call
- Hoist categories extraction + emission outside `if let Some(store) = ctx.store.take()`
- Pattern C: emission seam may need new Trait method on `AnalyzerContext` (audit
  before patching)

### Patch 5 — analyzer-js (Pattern A)

Trong `analyzer-js/src/pipeline.rs`:
- WriteAllPayload at line ~779 — hoist emission
- Populate files_selected/files_deleted from scope vars

### Patch 6 — analyzer-ts (Pattern A)

Trong `analyzer-ts/src/pipeline.rs`:
- WriteAllPayload inline at line ~1041 — hoist to binding
- Hoist emission; populate files_selected/files_deleted

### Patch 7 — analyzer-php (Pattern A — populate categories)

Trong `analyzer-php/src/php_analyzer.rs`:
- Phase-02 used `Vec::new()` placeholder for categories (deferred to phase-04)
- Populate from `WriteAllPayload::embedding_categories()` (mirror java)
- Hoist emission OUTSIDE writer block
- Populate files_selected/files_deleted from scope vars

### Patch 8 — Per-analyzer unit tests (6 analyzers × 3 cases = 18 tests)

Trong mỗi analyzer `tests/`:
- Test graphless mode + `--embedding-input-output` → artifact exists + parseable
- Test graphless mode without `--embedding-input-output` → exit 0
- Test graph-enabled mode + `--embedding-input-output` → artifact exists

## Deliverables

- Code changes: 6 analyzer mains (~80-120 LOC total)
- Tests: 18 new tests PASS
- Reports: `reports/phase04-six-remaining.md` — per-analyzer before/after

## Gate

- [ ] All 6 analyzers build xanh
- [ ] 18 unit tests PASS
- [ ] cortex-sync 85+ tests still PASS
- [ ] No regression in primary pass for any of 6 analyzers
- [ ] Capability table confirmed: 24 parsers (csharp/android added phase-03; remaining
  6 already in 22 cap from prior plan)

## Rollback

- Per-analyzer revert (6 commits, atomic per analyzer)
- Each revert: parser falls back to `disabled-no-emitter` (graceful)

## Next phase

Phase 05: full drill 3 legs + cross-update both plans + final report.