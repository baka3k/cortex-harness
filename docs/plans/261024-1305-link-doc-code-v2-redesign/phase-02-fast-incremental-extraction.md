# Phase 02 — Fast Incremental Extraction & Full Label Coverage (`link_engine.py`)

## Goal

Eliminate the 45–60 query `_paged_rows` bottleneck on every sync, fix the `99.7%` `doc_sha` miss rate, recover the `0%` kept `HttpEndpoint`/`Route` nodes, and include `File` and `Control` labels so screen/module names and VB6 controls are linkable.

## Tasks

1. **True Incremental Pre-Check (Delta First)**:
   - Compare baseline `doc_shas` and `code_folder_shas` against the stored SHAs in `links.db` (`docs.source_sha` and `symbols.source_sha`) + `state.json` **before** querying FalkorDB/LadybugDB.
   - **Fast-path 0 (No changes)**: When `!reconcile` and both doc and code SHA maps are identical to `state.json`, return `BuildReport` immediately from `state.json` (`< 100 ms`, 0 graph queries).
   - **Fast-path 1 (Doc-only delta)**: When only a subset of docs changed (`changed_doc_ids`) and code SHAs are unchanged, load the symbol index directly from `links.db` (`symbols` table) and query the doc graph only for `WHERE p.source_id IN $changed_doc_ids`.
   - **Fast-path 2 (Code-only delta)**: When only a subset of code files changed (`changed_code_paths`), refresh `symbols` in `links.db` only for those `file_path` values, recompute `file_df`, and re-link affected paragraphs.
2. **Streamlined Graph Readers (Replacing 2D `_paged_rows`)**:
   - **Doc reader**:
     - Combine `Paragraph` and `Entity` extraction or use simple 1D keyset pagination (`WHERE p.project_id_normalized = $pid AND id(p) > $after_id RETURN id(p) AS _id, p.source_id, p.paragraph_id, p.text ORDER BY id(p) LIMIT 5000`), avoiding the separate `count_cypher` + `range_cypher` + `group_cypher` loop.
     - For `HAS_ENTITY`, fetch `(e:Entity)<-[:HAS_ENTITY]-(p:Paragraph)` paged by `id(p) > $after_id` or read `entity_mentions` directly from Qdrant `{project_id}_doc` scroll when local Qdrant is available.
     - Pre-compute the `.sensitive-terms`-redacted 240-char snippet per paragraph during extraction so it is stored in `paragraphs.snippet`.
   - **Code reader**:
     - Add `"File"` and `"Control"` to `CODE_SYMBOL_LABELS`:
       - For `File`: `name = Path(row["path"]).stem`, `file_path = row["path"]`, `target_type = "file"`. This allows documents naming screen/program IDs (e.g. `OrderSummaryForm`) to link directly to `OrderSummaryForm.vb` / `.cs`!
       - For `Control`: VB6 designer controls (`name`, `file_path`, `line_number` as `start_line`).
     - Fix `CODE_ENDPOINT_LABELS` (`ApiEndpoint`, `HttpEndpoint`, `Route`, `ControllerAction`, `ServletEndpoint`):
       - Query `coalesce(n.name, n.route, n.url, n.path, n.http_path, n.symbol_id) AS name` and `coalesce(n.source_file, n.file_path, n.path, n.module_id) AS file_path`.
     - Skip any label where `count(n) == 0` immediately without running range/page queries.
3. **Fix Extensionless & Folder-Prefixed `DocShaIndex` (`99.7%` -> `100%` match)**:
   - In `build_doc_sha_index(doc_shas)`, index each baseline relative path `rel` under:
     1. `posix_path(rel)` and `.casefold()`
     2. `flatten_doc_rel(rel)` and `.casefold()` (`a/b/c.xlsx` → `a__b__c.xlsx`)
     3. `flatten_doc_rel(without_suffix(rel))` and `.casefold()` (`a/b/c.xlsx` → `a__b__c`)
     4. `Path(rel).name.casefold()` (unique filename with extension)
     5. `Path(rel).stem.casefold()` (unique stem without extension, e.g. `Migration_Review_OrderSummaryForm`)

## Files

- Modify: `link-tiny/link_engine.py`, `link-tiny/tests/test_link_engine.py`, `link-tiny/tests/test_sha_join.py`

## Test Scope

Mapped acceptance criteria + size: **M** (~8 test functions).

| Behavior | Test |
| --- | --- |
| Extensionless and mixed-case `doc_id` resolves `source_sha` via `DocShaIndex` | `test_doc_sha_extensionless_and_casefold` |
| `HttpEndpoint` and `Route` nodes with `route`/`file_path` or `module_id` are kept (`kept == expected`) | `test_endpoint_labels_kept` |
| `File` and `Control` nodes are extracted and indexed by stem/name | `test_file_and_control_extraction` |
| Incremental no-op run executes 0 graph queries and returns in `< 100 ms` | `test_incremental_zero_queries_on_noop` |
| Doc-only incremental change queries only changed `source_id` | `test_incremental_doc_only_delta` |
