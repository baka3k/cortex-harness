# Phase 02 — Wire 14 legacy analyzers + dart fix (R12)

## Mục tiêu

Mỗi analyzer (14 legacy + dart) thêm ~10 LOC sau `write_all` (hoặc equivalent):
gọi `WriteAllPayload::embedding_categories()` + `maybe_emit_embedding_artifact(...)`
với các fields schema đúng `EmbeddingInputArtifact`.

## Scope (14 legacy + dart = 15 crates)

Dùng `reports/phase01-analyzer-write-all-locations.md` làm checklist.

### Pattern (mirror `analyzer-go/src/goanalyzer.rs:226-244`)

```rust
// ── Embedding-input artifact (only when orchestrator asked) ────
if let Some(output) = args.embedding_input_output() {
    let selected_rel: Vec<String> =
        selected.iter().map(|path| rel_posix(&root, path)).collect();
    cortex_analyzer_framework::embedding_artifact::maybe_emit_embedding_artifact(
        Some(output),
        cortex_analyzer_framework::embedding_artifact::EmbeddingEmission {
            parser: "cplus",  // từng parser tự điền
            project_id: &scope.project_id,
            root_scope: &scope.repo,
            full_replace: !args.incremental,
            scanned_directory: true,  // verify per-analyzer (R7)
            files_selected: selected_rel,
            files_deleted: deleted_manifest.iter().cloned().collect(),
            categories: payload.embedding_categories(),  // nếu analyzer dùng WriteAllPayload
            // hoặc hand-built categories nếu không có WriteAllPayload (overlays)
        },
    )
    .map_err(|e| e.to_string())?;
}
```

### Crates cần patch

| Parser | Crate | write_all location (research §F1, F12) | Notes |
|---|---|---|---|
| `cplus` | `analyzer-cplus/src/analyzer.rs` | line 1588-1621 | WriteAllPayload — reuse `embedding_categories()` |
| `java` | `analyzer-java/src/java_analyzer.rs` | (find write_all) | WriteAllPayload — reuse |
| `sql` | `analyzer-sql-family/src/sqlcommon.rs` | line 1251+ (SCAN_RESULT) | may use custom payload |
| `cobol` | `analyzer-cobol/src/analyzer.rs` | line 210+ | hardcode `vectors=0` → wire real |
| `csharp` | `analyzer-csharp/src/main.rs` | line 329-330 | hardcode → wire real |
| `delphi` | `analyzer-delphi/src/danalyzer.rs` | line 864+ | WriteAllPayload pattern |
| `kotlin` | `analyzer-kotlin/` | (find) | WriteAllPayload |
| `android` | `analyzer-android/src/main.rs` | line 641+ | WriteAllPayload |
| `vbnet/vb6/vba/vbscript` | `analyzer-vb/src/pipeline.rs` | line 456+ | WriteAllPayload |
| `python` | `analyzer-python/src/python_analyzer.rs` | (find) | WriteAllPayload |
| `js` | `analyzer-js/src/pipeline.rs` | line 795+ | WriteAllPayload |
| `ts` | `analyzer-ts/src/pipeline.rs` | line 1032+ | WriteAllPayload |
| `php` | `analyzer-php/src/php_analyzer.rs` | (find) | WriteAllPayload |
| `plsql` | `analyzer-sql-family/src/plsql/` | (find) | WriteAllPayload |
| `dart` | `analyzer-dart/src/main.rs` | line 693+ (SCAN_RESULT) | **R12**: missing emitter, wire it |

### R8 audit confirmations (per-parser)

For each parser, verify:
- `payload.embedding_categories()` returns Vec<(String, Vec<Value>)> with rows
  containing `id`/`symbol_id`, `project_id`, `file_path`, `kind`/`node_type`,
  `qualified_name`, `code`/`summary`/`comment`, `start_line`/`end_line`
  (per research §F8, F9)
- `args.scanned_directory()` returns bool (or hardcode `true` per analyzer if not exposed)
- `selected_rel` / `deleted_manifest` variables exist (else adapt — `files_selected` /
  `files_deleted` can be `vec![]` if no incremental support; `full_replace=true` covers)

### Tests per analyzer

- 1 unit test: `embedding_artifact::maybe_emit_embedding_artifact` called with analyzer
  payload → file written at expected path; 0600 mode; roundtrip parses
- 1 fixture test (integration, opt-in): analyzer binary run on small fixture → artifact
  file exists + parsable + non-empty categories

## Deliverables

- 15 analyzer crates: `git diff` per crate showing ~10 LOC added near write_all
- Per-crate unit test PASS
- `cortex-mcp` không regress (re-run MCP smoke)

## Gate

- [ ] 15 crates build xanh (`cargo build --release -p <each>`)
- [ ] Per-crate unit test PASS
- [ ] Primary pass ở procsample (`cplus/java/sql`) emit artifact file tại
  `manifest_root/{parser}_embedding_input_{token}.json` — verify bằng cách check file tồn tại
  sau `dev sync code`
- [ ] `cortex-mcp` smoke không regress (re-run)
- [ ] Dart fix R12 verified (fixture có .dart file → dart emitter works)

## Rollback

- Revert từng commit per-crate (atomic, surgical).
- Phase 02 không touch orchestrator → rollback không ảnh hưởng gate.