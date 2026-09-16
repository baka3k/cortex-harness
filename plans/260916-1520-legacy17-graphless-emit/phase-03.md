# Phase 03 — 4 carve-outs (csharp + cobol + android + plsql)

## Mục tiêu

Wire emission cho 4 analyzers đã defer ở phase-02 của plan `260916-1432`:
- `analyzer-csharp` (Pattern A — never wired before)
- `analyzer-cobol` (Pattern B — custom `write_graph_facts`)
- `analyzer-android` (Pattern B — major refactor `assemble.rs` ~1200 lines)
- `analyzer-plsql` (inherits from `analyzer-sql-family::sqlcommon` — minimal work since phase-02 patched sqlcommon)

End state: `EMITTING_VECTOR_CLI_PARSERS` cap-table move from 22 → 25 parsers
(actually 22 with sql/plsql via sqlcommon = 22 distinct parsers; csharp/android adds 2).

## Scope

### Patch 1 — analyzer-csharp Pattern A (complexity 3)

Trong `analyzer-csharp/src/main.rs` + `graph.rs`:
- Current state: csharp has NO `write_all` call in main flow (verified phase-02
  inventory). Only SCAN_RESULT prints; no `LanguageCodeWriter` use.
- Need to: build `WriteAllPayload` from `payloads: Vec<Value>` (or equivalent per-row data)
  → call `payload.embedding_categories()` → emit artifact
- Look at how `payloads` is assembled in `cscan.rs` / `analyzer.rs` (verify location)
- Wire `--embedding-input-output` flag in csharp CLI (may not exist yet — check
  `cortex-analyzer-framework::cli::AnalyzerArgs`)
- Populate `files_selected` từ `selected_paths`; `files_deleted` từ `deleted_paths`
  (mirror java pattern)

### Patch 2 — analyzer-cobol Pattern B (complexity 5)

Trong `analyzer-cobol/src/pipeline.rs::write_graph_facts`:
- Currently returns `Result<BTreeMap<String, i64>, WriterError>`
- Refactor to ALSO return categories: `Result<(BTreeMap<String, i64>, Vec<(String, Vec<Value>)>), WriterError>`
- Inside `write_graph_facts`: after building `nodes_by_label: BTreeMap<String, Vec<Row>>`
  but BEFORE consuming, extract `embedding_categories: Vec<(String, Vec<Value>)>`
  (skip `relations`/`calls` per `documents_from_categories` skip rules)
- Modify caller in `analyzer.rs::write_graph` (line ~330) to receive tuple
- In `analyzer.rs::main`, add emission block (always when `args.embedding_input_output()`
  is Some)

### Patch 3 — analyzer-android Pattern B (complexity 5 — major)

Trong `analyzer-android/src/assemble.rs::write_graph` (line ~671) + `main.rs`:
- Currently uses direct `writer.write_nodes_batch("label", ...)` per category
- Refactor: BEFORE each `write_nodes_batch`, capture the rows into
  `Vec<(String, Vec<Value>)>` accumulator
- Return accumulator alongside counts
- In `main.rs`, after `write_graph` returns, emit artifact if `--embedding-input-output`
  is set
- ⚠️ Major refactor — ~80-120 LOC changes; phase-02 audit flagged as Pattern B

### Patch 4 — analyzer-plsql (trivial — inherits sqlcommon)

Verify: `analyzer-plsql/src/bin/analyzer-plsql.rs` calls `sqlcommon::run(args, &spec, ...)`
where `spec.language_default = "plsql"`. The emission patched in phase-02 of plan
`260916-1432` already handles plsql via `spec.language_default` field. Confirm
- plsql builds xanh
- plsql emits artifact in graphless mode (live run on procsample if .plsql files exist;
  else unit test only)

### Patch 5 — Per-analyzer unit tests (3 analyzers × 3 cases = 9 tests)

Trong mỗi analyzer `tests/`:
- Test graphless mode + `--embedding-input-output` → artifact exists + parseable
- Test graphless mode without `--embedding-input-output` → exit 0
- Test graph-enabled mode + `--embedding-input-output` → artifact exists

### Patch 6 — Capability table update

Trong `cortex-sync/src/registry.rs::EMITTING_VECTOR_CLI_PARSERS`:
- Add `csharp`, `android` (after cobol and plsql wiring verified)
- Confirm total = 24 parsers (cplus + csharp + dart + delphi + go + java + jp1 + js +
  kotlin + perl + php + plsql + python + rust + shell + sql + swift + ts + vb6 + vba
  + vbnet + vbscript + cobol + android — 24 entries)

## Deliverables

- Code changes: 3 analyzer mains + 1 sqlcommon regression check (cumulative ~150-200 LOC)
- Tests: 9 new tests PASS (plsql inherits from sqlcommon test)
- Reports: `reports/phase03-carve-outs.md` — per-analyzer before/after

## Gate

- [ ] All 3 modified analyzers build xanh (`csharp`, `cobol`, `android`)
- [ ] plsql regression: plsql binary still builds and runs (verify via `--help`)
- [ ] 9 unit tests PASS
- [ ] Capability table updated: `EMITTING_VECTOR_CLI_PARSERS` = 24 parsers
- [ ] cortex-sync 85+ tests still PASS
- [ ] No regression in shared-7 parsers

## Rollback

- Per-analyzer revert (3 commits); capability table revert to 22
- cobol/android revert = parser falls back to `disabled-no-emitter` (no crash)
- csharp revert = same

## Next phase

Phase 04: 6 remaining analyzers (kotlin/delphi/vb/python/js/ts/php) — apply
patterns learned (most are Pattern A; python/ts/php may need Pattern C Trait seam).