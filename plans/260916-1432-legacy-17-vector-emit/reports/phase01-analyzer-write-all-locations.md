# Phase 01 — Analyzer write_all / write_graph Inventory Table

**Date:** 2026-09-16
**Branch:** feat/change-db
**Purpose:** Phase-02 dùng table này làm checklist chính xác — không cần grep mù.
Mỗi row: parser × crate × file:line × pattern × biến name cần cho
`EmbeddingEmission { parser, project_id, root_scope, full_replace, scanned_directory,
files_selected, files_deleted, categories }`.

---

## Inventory

### Pattern A — `WriteAllPayload::embedding_categories()` (14 parsers dùng `write_all`)

Các analyzer này đã build `WriteAllPayload { functions, types, classes, ... }` rồi
gọi `writer.write_all(&payload)`. Pattern phase-02:
```rust
if let Some(output) = args.embedding_input_output() {
    let selected_rel: Vec<String> = ...;
    maybe_emit_embedding_artifact(Some(output), EmbeddingEmission {
        parser: "<name>",
        project_id: &scope.project_id,
        root_scope: &scope.repo,
        full_replace: !args.incremental,
        scanned_directory: true,
        files_selected: selected_rel,
        files_deleted: deleted_manifest.iter().cloned().collect(),
        categories: payload.embedding_categories(),
    })?;
}
```

| Parser | Crate | File:Line (write_all) | Payload var | Scope var | Notes |
|---|---|---|---|---|---|
| cplus | analyzer-cplus | `src/analyzer.rs:1489` & `1588` | `payload` | `scope` | 2 write_all sites (full + incremental); chỉ wire incremental site |
| java | analyzer-java | `src/java_analyzer.rs:305` | `WriteAllPayload {...}` literal | `scope` | literal build, capture to `let payload = ...` |
| sql | analyzer-sql-family | `src/sqlcommon.rs:1191` | `payload` (build above) | scope local | verify `payload.embedding_categories()` returns valid |
| cobol | analyzer-cobol | `src/pipeline.rs:315` (`write_graph_facts`) | `nodes_by_label: BTreeMap<String, Vec<Row>>` + `relations` | scope local | **PATTERN B** — custom function; capture nodes_by_label BEFORE consume |
| csharp | analyzer-csharp | `src/graph.rs:497` | `payload` | scope local | uses WriteAllPayload |
| delphi | analyzer-delphi | `src/danalyzer.rs:732` | `payload` (build above) | scope local | uses WriteAllPayload |
| kotlin | analyzer-kotlin | `src/kotlin_analyzer.rs:345` | `WriteAllPayload {...}` literal | scope local | capture to let |
| android | analyzer-android | `src/main.rs:606` (`assemble::write_graph`) | (assemble.rs:671) | scope local | **PATTERN C** — uses `write_nodes_batch` direct; adapter module may need |
| vb | analyzer-vb | `src/pipeline.rs:440` | `payload` | scope local | uses WriteAllPayload — cover vbnet/vb6/vba/vbscript |
| python | analyzer-python | `src/python_analyzer.rs:220` | `WriteAllPayload {...}` literal | scope local | capture to let |
| js | analyzer-js | `src/pipeline.rs:779` | `payload` | scope local | uses WriteAllPayload |
| ts | analyzer-ts | `src/pipeline.rs:1041` | `WriteAllPayload {...}` literal | scope local | capture to let |
| php | analyzer-php | `src/php_analyzer.rs:186` | `WriteAllPayload {...}` literal | scope local | capture to let |
| plsql | analyzer-sql-family | `src/plsql/` (location TBD) | TBD | TBD | audit phase-02 |
| dart | analyzer-dart | `src/main.rs:432` | `WriteAllPayload {...}` literal | scope local | **R12**: missing emitter, wire it |

### Pattern B — Custom write function (cobol)

`write_graph_facts` builds `nodes_by_label: BTreeMap<String, Vec<Row>>` then writes
each label's rows. Pattern phase-02:
```rust
// In write_graph_facts, after building nodes_by_label, BEFORE consuming:
let embedding_categories: Vec<(String, Vec<Value>)> = nodes_by_label
    .iter()
    .filter(|(name, _)| !["relations",",calls"].contains(&name.as_str()))
    .map(|(name, rows)| {
        (name.clone(), rows.iter().cloned().map(Value::Object).collect())
    })
    .filter(|(_, rows)| !rows.is_empty())
    .collect();
// ... existing write_batches loop ...
// After write_graph_facts returns:
if let Some(output) = args.embedding_input_output() {
    maybe_emit_embedding_artifact(Some(output), EmbeddingEmission {
        parser: "cobol",
        project_id: &result.project_id,
        root_scope: &repo,
        full_replace: !args.incremental,
        scanned_directory: true,
        files_selected: result.changed_paths.to_vec(),
        files_deleted: result.deleted_paths.to_vec(),
        categories: embedding_categories,
    })?;
}
```

Note: `embedding_categories` need to be returned from `write_graph_facts` (or stored
in result struct) so caller can pass to artifact.

### Pattern C — Direct `write_nodes_batch` (android, shell)

`write_nodes_batch` is called per-label. Pattern phase-02:
- Collect rows BEFORE each write_nodes_batch call
- Build `Vec<(String, Vec<Value>)>` from collected
- After all batches, emit artifact

For analyzer-shell (already has `embedding_categories` function at `rows.rs:477`):
- Just call existing `embedding_categories(&result, project_name, &repo, &program_mappings)`
  after `write_graph` finishes

For analyzer-android (no existing `embedding_categories`):
- Audit `assemble.rs:671 write_graph` — extract rows per category before write_nodes_batch

### Pattern D — `WriteAllPayload::embedding_categories()` already used (shared-7)

These analyzers already wire embedding artifact. Phase-02 NOT modify; verify still passes:

| | analyzer-go | `src/goanalyzer.rs:230` (already calls `payload.embedding_categories()`) |
| | analyzer-perl | `src/main.rs:466` (calls `embedding_categories` from rows.rs:401) |
| | analyzer-rust | `src/main.rs:204` (calls `embedding_categories` from pipeline.rs:428) |
| | analyzer-shell | `src/main.rs:330` (calls `embedding_categories` from rows.rs:477) |
| | analyzer-swift | `src/main.rs:205` (calls `embedding_categories` from pipeline.rs:441) |
| | analyzer-jp1 | `src/analyzer.rs:105` (hand-built categories from `units`) |

---

## Cross-cutting variables

For each analyzer, identify:

| Var | Source | Used in |
|---|---|---|
| `parser` | string literal | EmbeddingEmission |
| `project_id` | `scope.project_id` OR `result.project_id` (varies) | EmbeddingEmission |
| `root_scope` | `scope.repo` OR `repo` | EmbeddingEmission |
| `args.incremental` | AnalyzerArgs (verify exists per analyzer) | `full_replace: !args.incremental` |
| `args.scanned_directory()` | AnalyzerArgs method (verify) | `scanned_directory` |
| `selected_rel` | `selected.iter().map(|path| rel_posix(&root, path)).collect()` | `files_selected` |
| `deleted_manifest` | analyzer-specific (passed via args or built locally) | `files_deleted` |
| `payload.embedding_categories()` | WriteAllPayload method | `categories` (Pattern A) |

## Verification matrix (phase-02 gate)

- [ ] analyzer-cplus: artifact emitted at incremental site (line ~1588)
- [ ] analyzer-java: artifact emitted (line ~305)
- [ ] analyzer-sql: artifact emitted (line ~1191)
- [ ] analyzer-cobol: artifact emitted (write_graph_facts extension)
- [ ] analyzer-csharp: artifact emitted (line ~497)
- [ ] analyzer-delphi: artifact emitted (line ~732)
- [ ] analyzer-kotlin: artifact emitted (line ~345)
- [ ] analyzer-android: artifact emitted (assemble.rs ~671 + main.rs ~606)
- [ ] analyzer-vb: artifact emitted (line ~440)
- [ ] analyzer-python: artifact emitted (line ~220)
- [ ] analyzer-js: artifact emitted (line ~779)
- [ ] analyzer-ts: artifact emitted (line ~1041)
- [ ] analyzer-php: artifact emitted (line ~186)
- [ ] analyzer-plsql: artifact emitted (location TBD phase-02)
- [ ] analyzer-dart: artifact emitted (line ~432, R12 fix)
- [ ] 6 shared-7 still wire (verify, no regression)

## Unknowns (open questions)

- **OQ1**: analyzer-plsql exact write_all location — phase-02 needs grep confirm
- **OQ2**: `args.embedding_input_output()` accessor exists in all 15 crates
  (analyzer-framework `cli.rs:208-213` defines it; verify crate re-exports)
- **OQ3**: `args.scanned_directory()` accessor — verify per analyzer
- **OQ4**: `rel_posix` helper exists in all crates (used for `selected_rel`)
- **OQ5**: deleted_manifest variable naming varies per analyzer — confirm per crate