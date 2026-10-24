# Phase 04 — Sub-Millisecond Indexed MCP Queries (`mcp_server.py`)

## Goal

Make `find_code_for_doc`, `find_doc_for_code`, and `trace_status` respond in `< 10 ms` by querying `links.db` indexes directly, resolving flexible `doc_id` / `path` / `name` inputs (exact, flattened, suffix, stem, substring), deduplicating symbol results across paragraphs, and serving pre-stored redacted snippets with 0 upstream HTTP calls.

## Tasks

1. **Replace `_project_read_model` Full-Scan with Indexed SQLite Queries**:
   - Remove the `link_store.latest_links(links_dir)` call from `_project_read_model()`.
   - Open `links.db` read-only (`sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`).
   - `trace_status_impl(project_id)` reads `state.json` + SQLite `meta`/`links` status counts in `< 2 ms`.
2. **Multi-Resolution `find_code_for_doc_impl(project_id, doc_id, query, top_k, min_score)`**:
   - **Step A — Resolve `doc_id` (when provided) via 4-step waterfall**:
     1. Exact match: `d.doc_id = ? OR d.norm_doc_id = ?`
     2. Flattened path match: `d.norm_doc_id = ?` (using `flatten_doc_rel(doc_id).casefold()`)
     3. Stem / Basename match: `d.stem = ?` (stripping directory and extension from `doc_id`)
     4. Substring / Token match: `d.norm_doc_id LIKE ?` (e.g. passing `OrderSummaryForm` matches all documents for that screen).
   - **Step B — Resolve `query` filter (when provided)**:
     - Match `query` (and `token_match_key(query)`) against `s.norm_name`, `s.match_key`, `s.norm_path`, `d.norm_doc_id`, or `p.snippet LIKE ?`.
   - **Step C — Symbol-Level Aggregation & Ranking**:
     - Group results by `(s.sym_uid, d.doc_uid)` so if a symbol is mentioned in 15 paragraphs of the same document, it appears **once** in `results` with:
       - `score`: `MAX(l.score)` + small multi-mention boost
       - `paragraph_id`: paragraph with highest score
       - `matched_paragraphs`: list of up to 5 `paragraph_id` values
       - `snippet`: pre-redacted `p.snippet` from `links.db` (falling back to `paragraph_snippet()` HTTP call **only** if `p.snippet` is null and upstream is needed).
3. **Multi-Resolution `find_doc_for_code_impl(project_id, path, name, top_k, min_score)`**:
   - **Step A — Resolve `path` (when provided) via 3-step waterfall**:
     1. Exact POSIX path (`s.norm_path = ?`)
     2. Basename or suffix match (`s.basename = ?` or `s.norm_path LIKE '%/' || ?`, stripping leading repo folder prefix if caller passed `s1/src/...`)
     3. File stem match (`s.file_stem = ?`, so `path="OrderSummaryForm"` or `"OrderSummaryForm.vb"` matches both `.vb` and `.Designer.vb`).
   - **Step B — Resolve `name` (when provided) via 3-step waterfall**:
     1. Exact `s.norm_name = ?` or `LOWER(s.qualified_name) = ?`
     2. Normalized `s.match_key = ?` (`" ".join(token_match_key(name))`)
     3. Suffix segment of `qualified_name` (`token_match_key(name.split(".")[-1])`).
   - **Step C — Document-Level Aggregation & Snippet Enrichment**:
     - Return ranked documents + best matching paragraph + pre-redacted `snippet` directly from `paragraphs.snippet`.

## Files

- Modify: `link-tiny/mcp_server.py`, `link-tiny/link_store.py`, `link-tiny/tests/test_mcp_tools.py`

## Test Scope

Mapped acceptance criteria + size: **M** (~9 test functions).

| Behavior | Test |
| --- | --- |
| `find_code_for_doc` resolves unflattened path (`a/b/spec.xlsx`), stem (`spec`), and screen substring (`OrderSummary`) | `test_find_code_doc_id_waterfall` |
| `find_code_for_doc` deduplicates multiple paragraph mentions of the same symbol and returns pre-stored snippet without HTTP | `test_find_code_symbol_dedup_and_local_snippet` |
| `find_doc_for_code` resolves basename (`Foo.vb`), repo-prefixed path (`s1/src/Foo.vb`), and file-only lookup (`name=None`) | `test_find_doc_path_waterfall` |
| `find_doc_for_code` resolves qualified name (`Mod.grid_unit_col`) and camel/snake variants | `test_find_doc_name_waterfall` |
| Query latency on 50k-link fixture is `< 20 ms` with 0 upstream HTTP requests | `test_query_latency_budget` |
