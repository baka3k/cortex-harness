# Phase 02 — Procsample-critical 4 (cplus + java + sql + dart)

## Mục tiêu

Apply Pattern A/B cho 4 analyzers mà user procsample phụ thuộc:
- `analyzer-cplus` (Pattern B — major refactor, interleaved flushes)
- `analyzer-java` (Pattern A — direct hoist)
- `analyzer-sql-family` (Pattern A — covers sql + plsql qua `run()`)
- `analyzer-dart` (Pattern B — surface categories from `write_graph` tuple)

End state: `dev sync code --full-scan` ở procsample → vectors_upserted > 0 + Qdrant có collections.

## Scope

### Patch 1 — analyzer-cplus Pattern B (complexity 5)

Trong `analyzer-cplus/src/analyzer.rs`:
- Hoist buf_* declarations OUTSIDE `if let Some(mut writer) = writer { ... }` block
  (lines ~875-887 currently inside)
- Hoist `embedding_accumulator: Vec<(String, Vec<Value>)>` OUTSIDE
- Hoist `flush_write_buffers` call to ALWAYS run (with `Option<&mut LanguageCodeWriter>`
  param — `None` for graphless, `Some(&mut writer)` for graph-enabled)
- Refactor `flush_write_buffers` to accept `Option<&mut LanguageCodeWriter>` —
  when None, skip `write_node_properties_batch` calls but still do
  `embedding_accumulator.extend(payload.embedding_categories())`
- Move artifact emission OUTSIDE writer block
- Populate `files_selected` từ `selected: Option<BTreeSet<String>>`; `files_deleted`
  từ `deleted_files: BTreeSet<String>` (đã có sẵn)

### Patch 2 — analyzer-java Pattern A (complexity 3)

Trong `analyzer-java/src/java_analyzer.rs`:
- `WriteAllPayload` đã build trong main (line ~305) — chỉ cần hoist emission
- Move `if let Some(output) = args.embedding_input_output()` block ra ngoài
  `if let Some(store) = ctx.store.take() { ... }`
- Populate `files_selected` từ `selected_rel_paths` (đã có); `files_deleted` từ `deleted_set`
  (đã có)

### Patch 3 — analyzer-sql-family Pattern A (complexity 3) — covers sql + plsql

Trong `analyzer-sql-family/src/sqlcommon.rs`:
- Move emission ra ngoài `if let Some(store) = ...` block (line ~1191)
- Populate `files_selected` từ `selected_files` (đã có); `files_deleted` từ `deleted_set`
  (đã có)

### Patch 4 — analyzer-dart Pattern B (complexity 4)

Trong `analyzer-dart/src/main.rs`:
- Hiện `write_graph` returns `(BTreeMap, Vec<(String, Vec<Value>)>)` (đã có từ prior plan)
  — verify emission site đã dùng `embedding_categories` (line ~720-735)
- Move emission ra ngoài `if !args.dry_run && !args.graph_writes_disabled()` check
  (always emit when `--embedding-input-output` is set, regardless of dry_run/graphless)
- Verify analyzer-dart flag field `embedding_input_output: Option<String>` already added
  (prior plan phase-02 R12)

### Patch 5 — Per-analyzer unit tests (4 analyzers × 3 cases = 12 tests)

Trong mỗi analyzer `tests/`:
- Test graphless mode + `--embedding-input-output` → artifact exists + parseable
- Test graphless mode without `--embedding-input-output` → exit 0 (no crash)
- Test graph-enabled mode + `--embedding-input-output` → artifact exists (regression)

### Patch 6 — End-to-end drill on procsample

Live run:
```
$ /Users/.../dev.sh sync code --project-dir /Users/.../procsample --full-scan 2>&1 \
  | grep -E "embedding|vectors_upserted|embedding pass"
$ curl http://localhost:6333/collections | jq '.result.collections[].name'
```

Expect:
- `[embedding] cplus: artifact written path=.../cplus_embedding_input_*.json`
- `[embedding] java: artifact written path=.../java_embedding_input_*.json`
- `[embedding] sql: artifact written path=.../sql_embedding_input_*.json`
- `[embedding] pass complete: parsers=3 vectors_upserted=1517 backend=orchestrator`
  (or similar — actual count depends on facts quality)
- Qdrant collections: `procsample_*_cplus_functions`, `procsample_*_java_functions`,
  `procsample_*_sql_functions` (or similar names per `code_collection_name`)

## Deliverables

- Code changes: 4 analyzer mains (~80-150 LOC total depending on cplus complexity)
- Tests: 12 new tests PASS
- Reports: `reports/phase02-procsample-drill.md` — drill log + before/after metrics
  (vectors_upserted, Qdrant collections count)

## Gate

- [ ] All 4 analyzers build xanh (`cargo build --release -p analyzer-cplus -p analyzer-java -p analyzer-sql-family -p analyzer-dart`)
- [ ] 12 unit tests PASS
- [ ] **procsample end-to-end**: `dev sync code --full-scan` → vectors_upserted > 0
- [ ] Qdrant có ít nhất 3 collections (1 per parser)
- [ ] No regression: shared-7 parsers (dart/go/jp1/perl/rust/shell/swift) still emit vectors
- [ ] Final 85+ cortex-sync tests still PASS

## Rollback

- Per-analyzer revert (4 commits); capability table revert if needed
- After revert: legacy 17 → `disabled-no-emitter` (no vectors, no crash)

## Next phase

- Phase 03: 4 carve-outs (csharp/cobol/android/plsql) — never wired emission; require Pattern A/B from scratch
- Phase 04: 6 remaining (kotlin/delphi/vb/python/js/ts/php) — apply patterns learned
- Phase 05: verify + hand-back