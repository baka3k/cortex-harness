# Research digest — embedding-vector-pipeline gap for legacy 17 parsers

Researched 2026-09-16 14:33 (branch `feat/change-db`, HEAD `b17f5ba`). All refs are absolute paths. Evidence-first; unknown/inferred items are flagged explicitly.

---

## Findings

1. **Orchestrator splits parsers in embedding pass by `SHARED_VECTOR_CLI_PARSERS`** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:2115-2116`:
   ```rust
   let mut native_parser = native_store.is_some()
       && registry::SHARED_VECTOR_CLI_PARSERS.contains(&parser_name);
   ```
   Only those 7 get the native embedder path. Everything else falls into the
   else branch at `:2309+` that scrapes `vectors=N` from `[SCAN_RESULT]`.

2. **Shared-7 set is hard-coded** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/registry.rs:438-439`:
   ```rust
   pub const SHARED_VECTOR_CLI_PARSERS: [&str; 7] =
       ["dart", "go", "jp1", "perl", "rust", "shell", "swift"];
   ```
   None of the legacy 17 (`cplus`, `java`, `sql`, `cobol`, `delphi`, `kotlin`,
   `android`, `vbnet`, `vb6`, `vba`, `vbscript`, `python`, `js`, `ts`, `php`,
   `csharp`, `plsql`) is in the list.

3. **Registry still marks legacy parsers as `writes_vectors: true`** — all 24
   primary parsers in `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/registry.rs:93-133`
   get the default `writes_vectors: true` (`:61` and `:74`); the embedding
   pass gate is `:2105` (`config.writes_vectors`), which every legacy parser
   passes. So every legacy parser enters the embedding pass loop — but never
   gets the native embedder, never reads an artifact, and never counts vectors.

4. **Only 5 (of 7 shared) analyzers actually emit the `EmbeddingInputArtifact`** —
   `grep` for `maybe_emit_embedding_artifact` / `EmbeddingEmission` finds exactly
   six crates that wire it up: `analyzer-rust/src/main.rs:204`, `analyzer-perl/src/main.rs:466`,
   `analyzer-swift/src/main.rs:205`, `analyzer-shell/src/main.rs:330`,
   `analyzer-go/src/goanalyzer.rs:230`, `analyzer-jp1/src/analyzer.rs:105`.
   `analyzer-dart` does NOT emit it (separate `grep` returns no hits), so the
   "7" in the registry are really **5 emitters + dart + 17 legacy** = 23.
   Dart's analyzer is in `analyzer-dart/` and apparently uses a different
   `WriteAllPayload`-style code path; **unknown** whether its rows have the
   fields `documents_from_categories` requires (see F9).

5. **Legacy analyzers' `[SCAN_RESULT]` does not carry a useful `vectors=`** —
   sample grep of the 14 legacy analyzer crates for the SCAN_RESULT format:
   - `cobol` hardcodes `vectors={} artifact={}` (`analyzer-cobol/src/analyzer.rs:210`)
   - `csharp` hardcodes `vectors=0 vector_status=disabled`
     (`analyzer-csharp/src/main.rs:329-330`)
   - `java`/`kotlin`/`delphi`/`android`/`vb`/`sql-family`/`js`/`python`/
     `php`/`ts`/`cplus` print SCAN_RESULT lines **without any `vectors=` key**
     (e.g. `analyzer-java/src/java_analyzer.rs:354`,
     `analyzer-cplus/src/analyzer.rs:1529`,
     `analyzer-ts/src/pipeline.rs:1032`,
     `analyzer-php/src/php_analyzer.rs` module docs,
     `analyzer-python/src/python_analyzer.rs` module docs,
     `analyzer-delphi/src/danalyzer.rs:864`,
     `analyzer-vb/src/pipeline.rs:456`,
     `analyzer-android/src/main.rs:641`,
     `analyzer-sql-family/src/sqlcommon.rs:1251`,
     `analyzer-js/src/pipeline.rs:795`).
   - The orchestrator parser
     `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:2782-2785`:
     ```rust
     fn scan_result_vector_count(output: &str) -> Option<i64> {
         let re = regex::Regex::new(r"(?m)^\[SCAN_RESULT].*\bvectors=(\d+)\b").ok()?;
         re.captures(output)?.get(1)?.as_str().parse().ok()
     }
     ```
     Returns `None` for every parser that omits the key, so the orchestrator
     never even sets `vector_count` for those — and the value remains `null`
     (initialized at `:2181-2183` and only overwritten on a successful
     `scan_result_vector_count` call at `:2310-2312`).

6. **The Rust children explicitly do NOT embed (per analyzer-frame's note)** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-analyzer-framework/src/cli.rs:215-233`:
   ```rust
   pub fn note_orchestrated_embedding(&self) {
       ...
       println!(
           "[lane] --embed-*/--qdrant-* accepted as no-op: embedding moved to the \
            orchestrator (phase-06); this child never embeds or writes qdrant"
       );
   }
   ```
   So the legacy 17's "primary pass" already has the infrastructure for the
   embedding-input flag (`cli.rs:134`, `:208-213` accessor) but does not
   wire `maybe_emit_embedding_artifact` into its pipeline. The seam exists;
   it is unwired.

7. **Vector lane has only one write path** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:2264-2316`
   is the ONLY branch that calls `finish_native_embedding_pass` (the only
   function that ever writes vectors to Qdrant / the local JSON engine).
   The else branch at `:2309-2316` is the read-`vectors=N`-from-child-stdout
   path — and as F5 shows, legacy parsers don't print anything useful.

8. **Each legacy analyzer's primary pass DOES build a `WriteAllPayload` with
   the same shape the embedding pass consumes** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/analyzer-cplus/src/analyzer.rs:1588-1621`
   calls `LanguageCodeWriter::write_all(&WriteAllPayload { … functions, types, …})`.
   `WriteAllPayload::embedding_categories()`
   (`/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-graph-writer/src/language_writer.rs:2069-2111`)
   is the producer the orchestrator needs — but it's never called by any
   legacy analyzer.

9. **`documents_from_categories` requires per-row `id` (or `symbol_id`) plus
   `project_id` (and tolerates `name`, `qualified_name`, `node_type`/`kind`,
   `file_path`, `summary`/`comment`/`note`/`code`, `start_line`/`end_line`,
   `repo`, `language`, `project_name`)** —
   `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/vector_sync.rs:275-360`
   (and `documents_from_categories` at `:369-402`). Missing `symbol_id`/`id`
   fails loudly (`:283`, test at `:632-637`).
   Legacy `WriteAllPayload` rows do carry the field — `analyzer-cplus/src/analyzer.rs:64-79`
   shows `project_row` injecting `project_id`/`project_name`/`language`/`repo`
   on every row, and `project_row` substitutes `symbol_id` → `id` (`:67-69`).
   `start_line`/`end_line` are in the key arrays for `FUNC_KEYS`/`TYPE_KEYS`
   (`:85-95`). So the data is already in shape.

10. **EmbeddingInputArtifact fields (the schema the orchestrator will read)** —
    `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-analyzer-framework/src/embedding_artifact.rs:29-45`:
    ```rust
    pub struct EmbeddingInputArtifact {
        pub schema_version: u64,           // = 1, hard-pinned
        pub generated_at: String,
        pub parser: String,
        pub project_id: String,
        pub root_scope: String,
        pub full_replace: bool,
        pub scanned_directory: bool,
        pub files_selected: Vec<String>,
        pub files_deleted: Vec<String>,
        pub categories: Vec<EmbeddingCategory>,  // Vec<(String, Vec<Value>)>
    }
    ```
    `EMBEDDING_ARTIFACT_SCHEMA_VERSION = 1` (`:21`); version mismatch is a
    hard error (`:65-69`). Files are atomic 0600 (`:99-140`, test at `:200-221`).

11. **Orchestrator's reader expects everything the artifact promises** —
    `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:2793-2884`
    (`finish_native_embedding_pass`):
    1. reads file (`:2803`),
    2. parses via `EmbeddingInputArtifact::from_json_str` (`:2810`),
    3. asserts `parser` & `project_id` match the cell (`:2811-2816`),
    4. coerces each `Value` row to `Map<String, Value>` (`:2819-2830`),
    5. calls `vector_sync::documents_from_categories(&categories, parser_name, &artifact.root_scope, max_chars)`
       (`:2831-2836`),
    6. builds `cleanup` set from `files_selected ∪ files_deleted`
       (`:2837-2852`) — falls back to the docs' `file_path` when
       `!scanned_directory && both sets empty`,
    7. resolves a pass-wide embedder (`:2854-2871`),
    8. calls `vector_store::sync_vector_documents(...)` (`:2873+`).

12. **`vector_store::sync_vector_documents` is the single writer seam** —
    `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/vector_store.rs:406-463`
    takes `&impl VectorWriteOps` (`:44-58` trait) with two impls —
    `RemoteQdrantStore` (`:60-97`) and `LocalQdrantStore` (`:99-134`).
    Pass-flow: ensure collection (`:445`), ensure scope indexes (`:446`),
    embed (`:431`), upsert in QDRANT_UPSERT_BATCH=128 chunks (`:75-84`),
    delete stale via `stale_filter` (`:460`), finalize (`:461`). Embedder
    identity pinned by `embedding_model_name` (`:493-500`,
    `--embed-model` → `CODE_EMBEDDING_MODEL` → `jinaai/jina-embeddings-v3`).

13. **Cache contracts present today (`.cache/`)** —
    sampled at `/Users/hieplq1.aip/Migration/procsample/.cache/`:
    - `incremental_sync/<proj>_<snapshot>.json` — sync run state.
    - `incremental_sync_manifests/<proj>_<hash>/<snapshot>/<parser>_{changed,deleted,embedding_changed,embedding_deleted}_<token>_<pid>_<hash>.json`
      — file path lists per cell.
    - `incremental_sync_summaries/dev_<pid>_<tid>_<hash>.json` — per-run
      orchestration summary (see F14).
    - `incremental_sync_locks/<snapshot>.lock.metadata.json` — lock metadata.
    - `inventories/<proj>_<snapshot>.json` — content-fingerprint snapshot
      (size/mtime/sha256 per file). **Currently empty in the sample** — F15.
    - `message_scan_artifacts/<proj>_<hash>/<proj>/<parser>_messages.json`
      — message-scan lane facts (cross-parser, see `registry.rs:441-446`).
    - `parse_quality_artifacts/<proj>_<hash>/<snapshot>/<token>_<pid>_<hash>/<parser>.json`
      — parse-quality reports.
    - `graph-write-journal/<snapshot>/<parser>.sqlite3` — per-parser
      graph-write journal (mentioned in summary F14 `journal_path`).

14. **Summary schema (the `incremental_sync_summaries/...json`)** —
    from the live sample at
    `/Users/hieplq1.aip/Migration/procsample/.cache/incremental_sync_summaries/dev_01505e12e56b56c2_29507_5a082f7fe9964253979c10db8a366614.json`,
    one `parsers[]` entry carries (for cplus):
    - `parser`, `role: "primary"`, `changed`, `impacted`, `scan`, `deleted`,
    - `incremental_supported`, `status`, `error`, `started_at`, `finished_at`, `duration_seconds`,
    - `changed_manifest` (path), `deleted_manifest` (path),
    - `qdrant_collection` (= `procsample_bcb95d2ac8__cplus_functions`),
    - `writes_vectors: true`, `seeded_by`,
    - **`vector_status: "success"`** and **`vector_count: null`** for legacy 17,
    - `message_scan_enabled`, `ignore_cache`, `message_qdrant_collection`,
    - `command` (the full argv that ran the analyzer — every flag visible),
    - `parse_quality_artifact` for cplus, `journal_path` for java/sql.
    No `embedding_input_artifact` path, no embedded categories — the
    summary only records the *primary* pass; the embedding pass records
    *its own* summary under `vector_embeddings[]` (set in
    `orchestrator.rs:2193, 2195`).

15. **Inventories dir is empty in the live run** —
    `/Users/hieplq1.aip/Migration/procsample/.cache/inventories/` is present
    but contains zero files. The inventory file (`:1-27` schema, content
    fingerprints) is created by the orchestrator's pre-scan
    (`inventory::load_inventory_generation` called at
    `orchestrator.rs:743-749`), but this run evidently skipped or pruned it
    before the per-run snapshot landed. **Unknown** whether this dir is
    populated for full-scan / long-running syncs — needs a fresh sample.

16. **Embedding pass summary cell (`vector_embeddings[]`)** —
    `orchestrator.rs:2151-2195` sets `vector_info` with: `parser`, `role: "embedding"`,
    `changed`, `impacted`, `scan`, `deleted`, `incremental_supported`,
    `status`, `error`, `started_at`, `finished_at`, `duration_seconds`,
    `changed_manifest`, `deleted_manifest`, `qdrant_collection`,
    `embedding_backend: "orchestrator" | "python-children"`,
    `embedding_input_artifact` (path, only set when `native_parser`),
    `writes_vectors`, `vector_status: "pending" | "disabled"`,
    `vector_count: null | i64`, `graph_status: "disabled"`,
    `message_scan_enabled`, `ignore_cache`, `message_qdrant_collection`,
    `command` (full argv at `:2252-2254`).
    For legacy 17, this entry is created but the embedding pass never
    actually runs the vector write — so `vector_count` stays `null`.

17. **No `vector_status = "failed"` / `"disabled"` branch is wrong — the
    embedding pass IS entered, just yields no vectors** — `vector_status`
    is set to `"pending"` (`:2176-2179`) at entry, then to `"success"`
    (`:2261-2263`) or `"failed"` (`:2319`) after the child runs. For
    legacy 17 the child runs in embedding mode (graph-disabled primary
    analyzer pass), but `vector_count` is never written. The summary does
    NOT log "0 vectors" — it logs `null`. Downstream dashboards interpret
    `null` as "didn't finish" rather than "finished with 0".

---

## Data flow

### Today (legacy 17, observed)

```
[graph pass, orchestrator:1597-1732]
   └─ run analyzer-* (Rust binary)                       registry.rs:531-721 build_analyzer_cmd
       └─ LanguageCodeWriter::write_all(WriteAllPayload) e.g. analyzer-cplus/src/analyzer.rs:1588-1621
           └─ writes nodes/relations/calls to FalkorDB  cortex-graph-writer/src/language_writer.rs
           └─ prints `[SCAN_RESULT] parser=cplus files=N functions=N classes=N resources=N`
              (no `vectors=` key)                       analyzer-cplus/src/analyzer.rs:1529
       └─ writes parse-quality artifact                  analyzer-cplus/src/main.rs (cplus only)
       └─ writes message-scan artifact                   registry.rs:441-446 enables, message_scan_artifacts/

[embedding pass, orchestrator:2069-2377]
   └─ shell loop over PARSER_ITERATION_ORDER             orchestrator.rs:2103
       └─ for legacy 17: native_parser = false           orchestrator.rs:2115-2116
       └─ re-launch same analyzer-* binary
          with --graph-provider / no graph store open
          + extra vector/embed args (no-ops in Rust child) cli.rs:215-233
       └─ child prints `[SCAN_RESULT]` line              analyzer-cplus/src/analyzer.rs:1529
       └─ orchestrator parses with regex `vectors=(\d+)` orchestrator.rs:2782-2785
       └─ regex misses → vector_count stays null         orchestrator.rs:2310-2312
       └─ NO write to Qdrant / local JSON engine. Done.

Result: 0 vectors written for legacy 17. vector_count=null in summary.
```

### Target (what `documents_from_categories` + `sync_vector_documents` need)

```
[primary pass — same code as today, but with new flag]
   └─ run analyzer-* with --embedding-input-output <manifest_root>/<parser>_embedding_input_<artifact_token>.json
   └─ same LanguageCodeWriter::write_all(WriteAllPayload { … })
   └─ after write_all, call WriteAllPayload::embedding_categories()            language_writer.rs:2069-2111
       ↳ returns Vec<(name, Vec<Value>)> — declared order, empty categories dropped,
         relations/calls excluded (will be re-skipped in documents_from_categories).
   └─ wrap as EmbeddingEmission { parser, project_id, root_scope,
                                  full_replace, scanned_directory,
                                  files_selected, files_deleted,
                                  categories }
   └─ call maybe_emit_embedding_artifact(path, emission)                       embedding_artifact.rs:145-176
       ↳ atomic 0600 write to the orchestrator-chosen path; no graph pass change.

[embedding pass, for legacy 17]
   └─ native_parser = true (drop the SHARED_VECTOR_CLI_PARSERS gate)            TBD — see Risks R2
       OR set it true conditionally based on artifact presence
   └─ re-launch analyzer-* with --embedding-input-output pointing at the
      primary-pass artifact path → SAME file → re-run is a no-op rewrite
       ↳ the artifact from the primary pass IS reusable IF the analyzer's
         parse output is identical and full_replace/scanned_directory semantics
         carry through (this requires analyzer changes; see Seam B).
   └─ child re-emits the same artifact; orchestrator reads it
   └─ finish_native_embedding_pass reads → documents_from_categories → sync_vector_documents
```

The orchestrator-side read path (`finish_native_embedding_pass`,
`orchestrator.rs:2793-2884`) is already correct; the only gap is that
nothing emits the artifact for legacy 17.

---

## Cache contracts — what exists vs. what's missing

| File | Schema | Read today | Has facts we need? |
|---|---|---|---|
| `incremental_sync/<proj>_<snapshot>.json` | sync run state | orchestrator pre-scan | No — metadata only |
| `incremental_sync_manifests/<proj>_<hash>/<snapshot>/<parser>_changed_…json` | `Vec<String>` of file paths | orchestrator incremental selection | No — file list only |
| `incremental_sync_summaries/dev_*.json` | orchestrator run summary | dashboard / `cortex-dev` | No — *records the gap* (vector_count=null) |
| `incremental_sync_locks/*.metadata.json` | lock metadata | orchestrator | No |
| `inventories/<proj>_<snapshot>.json` | per-file size/mtime/sha256 | inventory pre-scan | **No row facts** — just file fingerprints |
| `message_scan_artifacts/.../<parser>_messages.json` | message row facts | embedding pass + MCP reader | **Partial** — only message rows (i18n, i18n keys, string literals). Doesn't include function/class bodies, code, qualified_name, start_line, etc. that `documents_from_categories` would consume. |
| `parse_quality_artifacts/.../<parser>.json` | parse quality report | cplus only | No — diagnostic report |
| `graph-write-journal/<snapshot>/<parser>.sqlite3` | streaming journal | orchestrator replay | **Maybe** — replay contains the raw rows streamed to the writer (see `journal_replay.rs`). **Unknown** until inspected: does it carry the full row payload or only graph-write commands? |
| (NEW) `<parser>_embedding_input_<artifact_token>.json` | `EmbeddingInputArtifact` (F10) | `finish_native_embedding_pass` | **YES — this is exactly what the orchestrator wants** |

**Key gap:** there is no existing cache file in `.cache/` that already
holds the per-row analyzer payload (id/symbol_id, file_path, qualified_name,
code/comment/summary, start_line/end_line) for legacy 17. The rows exist
*only in the analyzer process* before being streamed into
`LanguageCodeWriter::write_all`. The `graph-write-journal/*.sqlite3` is the
only on-disk side effect — and it has not been audited (unknown: see R1).

---

## `EmbeddingInputArtifact` format (exact)

From `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-analyzer-framework/src/embedding_artifact.rs:29-45` +
orchestrator read at `/Users/hieplq1.aip/AI/cortex-harness/rust/crates/cortex-sync/src/orchestrator.rs:2803-2852`:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `u64` | MUST be `1` (`:21`); mismatch hard-fails. |
| `generated_at` | `String` | UTC ISO-8601 (`:91-75`). |
| `parser` | `String` | MUST match orchestrator's `parser_name` (`:2811`). |
| `project_id` | `String` | MUST match (`:2811`). |
| `root_scope` | `String` | Path the analyzer scanned. |
| `full_replace` | `bool` | `!args.incremental` (per `goanalyzer.rs:236`). |
| `scanned_directory` | `bool` | Whether `args.scanned_directory` was true (`:237`). |
| `files_selected` | `Vec<String>` | rel_posix paths. |
| `files_deleted` | `Vec<String>` | rel_posix paths. |
| `categories` | `Vec<(String, Vec<Value>)>` | Each row Value MUST be an object (`orchestrator.rs:2824-2827`). Categories are emitted in declaration order (`:2081-2109`); empty categories are dropped. |

The `categories` payload is fed into `documents_from_categories`
(`vector_sync.rs:369-402`), which:
- skips `relations`/`calls` (`:377-379`),
- singularizes category for default node_type: `category[:-1]` when ends with `s`
  (so `aliases` → `aliase`, the Python-quirk surfaced at `:380-384`,
  test pinned at `:617-630`),
- substitutes `id` → `symbol_id` when missing (`:387-390`),
- defaults `node_type` from `kind` then `default_type` (`:391-397`),
- calls `document_from_payload` (`:275-360`) which:
  - requires `symbol_id`/`id` AND `project_id` (`:282-285`),
  - prefers `file_path` over `path` (`:286-287`),
  - composes text as `"<node_type>: <qualified_name>\nfile: <file_path>\n[summary/comment/note/code]"`
    (`:318-325`), then redacts (`:325`) and truncates (`:251-263`),
  - builds payload with `project_id_normalized`, `project_name`, `language`,
    `repo`, `file_path`, `name`, `qualified_name`, `parser`, `root_scope`,
    `text`, `start_line`/`end_line` (`:326-358`),
  - computes deterministic point id via `deterministic_point_id`
    (`vector_sync.rs:179-205`, joining `(parser|project_id|root_scope|symbol_id)`).

---

## Seam analysis — where the orchestrator gets facts

### Option A — query back from FalkorDB after the graph pass

- **Where:** post-graph, pre-embedding. Open FalkorDB, run `MATCH (n:Function) WHERE n.parser = $p RETURN n.* LIMIT …` per legacy parser.
- **Pros:** zero analyzer changes; one-shot read.
- **Cons:**
  - `WriteAllPayload` rows are projected into FalkorDB's labeled node
    property graph, **not** 1:1 with the row shape `documents_from_categories`
    needs. FalkorDB lacks `code`/`comment`/`summary`/`note` text fields for
    most labels (the writer writes them via `LanguageCodeWriter::write_*`,
    but the property keys may be collapsed to one Cypher key per label —
    see `language_writer.rs` write functions). Reconstructing an
    `EmbeddingInputArtifact` from the graph would require a reverse mapping
    that's parser-specific. **High risk.**
  - Round-trips through FalkorDB inflate embedding-pass wall-time (parse +
    write + read + write again vs. one pass that emits the artifact inline).
  - Dragging the embedder into a second I/O round-trip duplicates the model
    load concern (today: 8.3x cold-start win from one-pass embedder,
    `vector_store.rs:502-507`).
  - Re-uses pass-wide `pass_embedder` (`:2854-2871`) only if the
    `embedder_slot` survives across passes — currently it does (allocated
    in the embedding pass at `:2102`, reused at `:2276/2288`), so this is
    OK. **Minor.**
- **Verdict:** viable but expensive; risks parser-specific reverse mappings.

### Option B — emit the artifact in the primary pass, read it back in the embedding pass

- **Where:** add `EmbeddingInputOutput` wiring to each legacy analyzer's
  primary pass. After `write_all`, call `WriteAllPayload::embedding_categories()`
  + `maybe_emit_embedding_artifact(...)`. Orchestrator passes
  `--embedding-input-output <path>` to BOTH the primary and embedding
  launches (today: only the embedding launch gets the flag,
  `orchestrator.rs:2248-2251`).
- **Pros:**
  - Single source of truth (the analyzer's row buffer).
  - Re-uses existing `WriteAllPayload` rows (no graph round-trip).
  - Embedding pass becomes "no-op re-emit + read" — embedding pass child
    cost is just `O(file_io)`, the orchestrator owns the embed/upsert.
  - Mirrors the 5-of-7 shared parser wiring exactly
    (`analyzer-rust/src/main.rs:204`, `analyzer-perl/src/main.rs:466`, etc.).
    Same `EmbeddingEmission` struct, same `maybe_emit_embedding_artifact`
    call, same atomic 0600 file envelope.
- **Cons:**
  - Touches 14-17 analyzer crates (every legacy parser needs
    ~10 lines added near its `write_all` call). Phase-roll pain.
  - The primary-pass child now writes an extra file (small overhead,
    testable with a fixture).
  - Re-running the same analyzer in the embedding pass duplicates
    parse work — **unless** the orchestrator re-uses the primary-pass
    artifact file. Today, the embedding pass launches the child with a
    fresh `--embedding-input-output` path (`:2148-2150`); if both passes
    use the **same path**, the primary pass writes it and the embedding
    pass's child overwrites it (same data ⇒ idempotent), then the
    orchestrator reads it. Alternatively, the orchestrator skips
    re-invoking the child for the embedding pass when the artifact
    already exists — this is a non-trivial orchestration change.
- **Verdict:** the smallest, lowest-risk path. Aligns with the shared-7
  wiring. No graph layer changes.

### Option C — re-invoke the legacy analyzer in the embedding pass with a new flag

- **Where:** the orchestrator already launches the child again in the
  embedding pass (`:2218-2257`) with the `embedding-input-output` flag set.
  Just teach the legacy analyzer to honor the flag.
- **Pros:**
  - **Identical to Option B in implementation** — the analyzer code
    change is the same. The orchestrator change is the same — pass the
    flag in the primary pass too, or skip the embedding-pass re-launch
    if the primary-pass artifact exists.
  - Preserves the orchestrator's "two passes, each child knows its role"
    shape.
- **Cons:**
  - Same as Option B. The difference is purely cosmetic — whether the
    orchestrator re-launches the child or reads the primary-pass artifact
    directly. Re-launching costs a second `analyzer-*` invocation (parse
    + write_all again) for the same data.
- **Verdict:** strictly worse than B unless the orchestrator re-launch
  path can be short-circuited. Treat as a sub-case of B.

### Hybrid D — use the graph-write-journal as the fact source

- **Where:** `/Users/hieplq1.aip/Migration/procsample/.cache/graph-write-journal/<snapshot>/<parser>.sqlite3`
  (see F13). Read the journal, replay rows into `EmbeddingEmission`.
- **Pros:** zero analyzer changes if the journal captures full rows.
- **Cons:** **Unknown** whether the journal contains the full row payload
  or only the streaming-writer's `write_*` commands — needs to inspect
  `journal_replay.rs`. Even if full, replaying the journal is a second
  I/O pass and risks drift between writer-format and vector-format
  expectations. The `must_not`/`has_id` filter semantics gap on the local
  engine (research/repository-findings.md A2) would still bite.
- **Verdict:** parallel exploration to confirm the journal payload shape;
  not the primary path.

---

## Risks / open questions

| # | Severity | Item |
|---|---|---|
| R1 | unknown | **graph-write-journal payload shape** — does `journal_replay.rs` carry the full row (id, file_path, qualified_name, code, start_line) or only the projected graph commands? If full, Hybrid D becomes viable (cheaper than B). **Must inspect before committing to B.** |
| R2 | high | **`SHARED_VECTOR_CLI_PARSERS` gate in embedding pass** (`orchestrator.rs:2115-2116`) — Option B requires either (a) widening the gate to all 24 parsers (asserts every parser has the artifact emitter — risky for `analyzer-dart`, see F4), or (b) replacing it with `native_parser = artifact_will_be_emitted(...)` keyed on a per-parser capability table. **Pick one before coding.** |
| R3 | high | **Primary-pass re-write conflict** — the primary pass writes to FalkorDB; the embedding pass writes to Qdrant. They share `WriteAllPayload`. Re-using the same artifact file means both passes produce the SAME file. **Need a rule:** is the primary-pass invocation the only emitter (and the embedding pass reads it without re-launching the child), or does the embedding pass still re-launch and re-emit? **Pick one.** |
| R4 | medium | **EmbeddingInputArtifact is `0600` with plaintext source** (`embedding_artifact.rs:99-140`, red-team S6). On disk in `manifest_root` — confirm `manifest_root` lives under the project's `.cache/` and inherits the right umask. **Spot-check the path today.** |
| R5 | medium | **`full_replace` semantics divergence** — `goanalyzer.rs:236` uses `!args.incremental`. Legacy analyzers' `incremental` flag handling may differ (cplus accepts `--incremental` via `AnalyzerArgs`, others may not). **Verify per legacy analyzer.** |
| R6 | medium | **`files_selected` / `files_deleted` semantics** — these drive the `cleanup` set in `finish_native_embedding_pass` (`orchestrator.rs:2837-2852`). Today the 5 emitters pass `selected_rel` (`goanalyzer.rs:228-229`) and `deleted_manifest` (`goanalyzer.rs:239`). Legacy analyzers' incremental manifest path resolution varies (cplus uses `load_manifest_paths`, others may not). **Verify per analyzer.** |
| R7 | medium | **`scanned_directory` flag** — passed `true` for go (`goanalyzer.rs:237`). Legacy analyzers that don't have a `--scanned-directory` flag need a default; `args.scanned_directory()` accessor may return a default value. **Inspect cli.rs.** |
| R8 | medium | **`documents_from_categories` requires `project_id` per row** — `vector_sync.rs:283-285` errors on missing project_id. `analyzer-cplus/src/analyzer.rs:64-79` shows `project_row` injects it. **Verify each legacy analyzer's `project_row` (or equivalent) injects it** — likely OK because the cplus pattern is reused, but the JVM overlay family (`analyzer-jvm-overlays/`) and `analyzer-web-overlays/` (aspnet) use different conventions (`analyzer-struts.rs`, `aspnet/writer.rs`); they may emit only graph-specific facts (StrutsFacts, AspNetFacts), **not** the full WriteAllPayload rows `documents_from_categories` expects. **Inspect their `write_*` calls before assuming uniform shape.** |
| R9 | low | **`pass_embedder` reuse across passes** — `embedder_slot` is allocated in the embedding pass (`orchestrator.rs:2102`, `:2854-2871`). If we short-circuit the embedding-pass child launch (Option B), the embedder still loads once — no change. |
| R10 | low | **`vector_status` field on summary** — currently `"pending"` (`:2176-2179`) → `"success"` (`:2261-2263`) or `"failed"` (`:2319`). For legacy 17 the embedding-pass child succeeds (so `status="success"`), but `vector_count` stays null. Downstream tooling interprets `null` ≠ `0`. **Add an explicit `"success"` with `vector_count=0` cell when no artifact, or distinguish `"success-no-vectors"` vs `"success-empty-vectors"`.** |
| R11 | low | **`messages_scan_artifacts/<proj>/<parser>_messages.json`** is the existing on-disk fact source for some legacy parsers (cplus, java, sql, …) — but it's the **message** lane only. Embedding pass could in theory read this for `i18n`/`strings` categories, but `documents_from_categories` skips `messages`. **Out of scope here**, but worth noting as a future lane. |
| R12 | low | **`dart` analyzer does NOT emit the artifact** (F4) but IS in `SHARED_VECTOR_CLI_PARSERS`. If we widen the gate (R2), dart's `vector_status` regresses to `null`. **Confirm dart's WriteAllPayload usage and add the missing emitter call in the same change, or exclude dart from the widening.** |
| R13 | low | **Cortex-vector-retrieval-py reads** (`cortex-retrieval-py/`) — the changes touch the writer side; retrieval code path is unaffected. **Spot-check that no fixture pins the `"vectors=0 vector_status=disabled"` literal.** |

---

## Recommended approach (3 options, one each)

1. **B + widening the embedding-pass gate** — add `EmbeddingEmission` +
   `maybe_emit_embedding_artifact(...)` calls in every legacy analyzer's
   primary pass after `write_all`, reuse the artifact in the embedding pass
   (either by reading the primary-pass file or by re-launching the child
   with the same `--embedding-input-output` path), and widen
   `SHARED_VECTOR_CLI_PARSERS` to all `writes_vectors: true` parsers (24,
   including dart — fix dart's emitter first, see R12). **Lowest risk,
   matches existing pattern, one writer path.**

2. **B + per-parser capability table** — same as #1 but replace the
   `SHARED_VECTOR_CLI_PARSERS` constant with a `vec![<parser>]` of parsers
   that have wired the emitter (start with the 5 currently wired, plus the
   legacy 17 as they land). Lets us ship legacy parsers incrementally
   without coupling to dart. **Same code shape, more gradual rollout.**

3. **Hybrid D** — confirm `graph-write-journal` carries full rows (R1); if
   yes, swap the analyzer-side wiring for a journal-replay step in the
   embedding pass (no analyzer source changes, but a new replay module).
   **Defer until R1 is resolved; revisit if Option B proves brittle.**