# link-tiny v2: Indexed SQLite Store, IDF/Co-occurrence Gating & Sub-15ms MCP Queries — 2026-10-24

## Context
On large multi-folder repositories (`~158.7k` symbols, `~113.5k` paragraphs across `~464` documents), `link-tiny` v1 suffered from:
1. **Combinatorial Link Explosion (`16,394,024` links / `8.4 GB` `links.jsonl`)**: Generic UI/framework boilerplate (`Dispose`, `InitializeComponent`, `components`, `Frame1`, `on_submit_click`) and unfiltered YAKE/GLiNER prose entity tokens (`"Source"`, `"CODE"`, `"Screen"`) matched across hundreds of files × thousands of spreadsheet rows with flat `score = 1.0`.
2. **Slow Graph Extraction & Missing Endpoints**: `read_doc_entity_names` paged through `2,229,483` `HAS_ENTITY` edges (`36.2s`), and `_read_labeled_symbols_paged` queried only `n.source_file` for endpoints, dropping `HttpEndpoint` (`60`) and `Route` (`60`) nodes that store `n.file_path`.
3. **High `doc_missing` SHA-Join Rate (`99.7%` miss)**: `.cortext-harness/sync-state/*.json` lacked files from partial doc syncs, and extensionless flattened `Paragraph.source_id` values / macOS NFD Unicode paths missed `DocShaIndex`.
4. **Unresponsive `link_mcp` Queries**: Every `find_code_for_doc`, `find_doc_for_code`, and `trace_status` call scanned the entire multi-gigabyte `links.jsonl` file, required exact string equality on `doc_id`/`path`, and fired up to 20 sequential HTTP sessions to `mind_mcp` for snippets.

## Change
- **`link-tiny/link_store.py:165-895`**: Added a co-located SQLite WAL store (`links.db`) with normalized tables (`docs`, `paragraphs`, `symbols`, `links`) and B-Tree indexes (`idx_docs_norm`, `idx_docs_flat`, `idx_docs_stem`, `idx_sym_name`, `idx_sym_key`, `idx_sym_path`, `idx_sym_base`, `idx_sym_stem`, `idx_links_doc_score`, `idx_links_sym_score`, `idx_links_status`). Added multi-resolution query waterfalls (`query_code_for_doc`, `query_doc_for_code`), `query_status_counts`, `save_paragraph_snippets`, and `compact_store_on_reconcile`.
- **`link-tiny/normalizers.py:36-188`**: Expanded `STOPWORDS` with UI/framework/SQL single-word boilerplate and added `is_linkable_entity_token` to filter out non-identifier prose words/phrases from doc-graph `Entity` mentions.
- **`link-tiny/link_engine.py:310-1080`**:
  - Implemented 2-stage filtered entity extraction in `read_doc_entity_names` (reads distinct `Entity` nodes first, filters against `is_linkable_entity_token` + `match_index`, and queries `HAS_ENTITY` only for matched entity IDs).
  - Updated `_read_labeled_symbols_paged` to project both `n.file_path` and `n.source_file` so `HttpEndpoint` and `Route` nodes are retained (`100%`).
  - Added `load_doc_folder_hashes`, multi-scope chronological merge in `_read_folder_inventory_entries`, and NFC/NFD + extensionless flattened lookup in `DocShaIndex`.
  - Implemented `compute_scored_pairs` with per-key `file_df` document frequency, a file-context co-occurrence affinity gate for `file_df > MAX_UNSCOPED_SYMBOL_DF` (`8`), IDF-weighted scoring with affinity bonus, per-token candidate cap (`10`), and per-`(doc_id, symbol)` paragraph cap (`3`).
  - Pre-redacted 240-char paragraph snippets at link build time and persisted them into `links.db`.
- **`link-tiny/link_worker.py:48-190`**: Added `_doc_source_folders` and supplemented `baseline_doc_shas` with `load_doc_folder_hashes`.
- **`link-tiny/mcp_server.py:236-435`**: Replaced full-JSONL `_project_read_model` scans with `_project_metadata` + indexed SQLite queries (`query_code_for_doc`, `query_doc_for_code`, `query_status_counts`) and pre-stored redacted snippets with automatic `paragraph_snippet` HTTP fallback.

## Impact
- **Link Count & Disk Size**: Reduced active links on the live 158.8k-symbol / 464-doc deployment from `16,394,024` (`8.4 GB`) to `159,715` (`88.1 MB` `links.jsonl` + `96.6 MB` `links.db`) — a **102.6x reduction** in link count and **99% reduction** in raw log size.
- **Build & Extraction Speed**: Full `--reconcile` completes in **`36.8s`** (`1.7s` on `cortext`). `HttpEndpoint` (`60/60`) and `Route` (`60/60`) keep rate increased from `0%` to `100%`. `doc_matched` SHA join increased from `0.25%` to `97.7%` (`100%` on `cortext`).
- **MCP Query Latency**: Warm `trace_status` responds in **`1.37 ms`**, `find_doc_for_code` in **`3.64 – 13.77 ms`**, and `find_code_for_doc` in **`11.29 ms`** with zero upstream HTTP calls required when snippets are present in `links.db`.
- **Risk Level**: Low — preserves the one-way dependency boundary (`tests/test_link_acceptance.py::test_acceptance_isolation`), lease-free read-only graph opening, and MCP v2 envelope contract (`165/165` unit + acceptance tests passing).

## Decision
- **Co-located SQLite WAL (`links.db`) alongside `links.jsonl`**: Provides `O(log N)` B-Tree lookups and multi-resolution waterfall queries (`exact` -> `flattened` -> `basename/suffix` -> `stem` -> `substring`) for `link_mcp` while keeping `link_store.load_links` / `latest_links` 100% backward-compatible with existing tests.
- **File-Context Co-occurrence Gate for High-DF Symbols (`file_df > 8`)**: Preserves exact-name matching (`score = 1.0`) for unique/low-DF domain symbols (`file_df <= 2`) while requiring high-DF symbols (e.g., `on_submit_click` in 578 files) to share file-stem context with the document or a low-DF symbol in the same paragraph.

## References
- plan: ./docs/plans/261024-1305-link-doc-code-v2-redesign/plan.md
- contract: ./docs/LINK_CONTRACT.md
