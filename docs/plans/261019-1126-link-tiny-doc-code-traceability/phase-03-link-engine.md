# Phase 03 — link_engine.py: read-only graph reads + exact-name pipeline

## Goal

The 5-step pipeline: delta → candidate generation → score → stale
propagation → write. Reads both graphs directly (read-only, no writer
lease — the `260828-1508` sibling-read pattern) and produces records for
`link_store`. Pure library: takes opened graph handles + paths in, returns
writes through `link_store`. Knows nothing about MCP or CLI.

## Tasks

1. **Graph readers** (digest §1/§2/§4): read doc side (`{p}_doc`:
   `Document{id,name}`, `Paragraph{source_id, paragraph_id, text}`,
   `Entity{name, type, name_norm}` — note: `Chunk` is never written, do not
   query for it) and code side (`{p}`: `Function/Class…{name,
   qualified_name, file_path, start_line, end_line, language}` + framework
   endpoint labels `ApiEndpoint`/`HttpEndpoint`/`Route`/`ControllerAction`).
   Both opened **read-only with zero primary lease** — via the sibling-open
   mechanism, or the RT-3 bounded additive opener if the driver lacks one.
   File paths normalized to POSIX at record creation (RT-6).
2. **Delta (step 1)**: per-file SHAs come from the sync baseline
   `{project_dir}/.cortext-harness/sync-state/{md5(folder)}.json`
   (`file_hashes`) + `.cache/inventories/{snapshot_id}.json` (authoritative
   SHA) + newest `.cache/incremental_sync_manifests` tokens (changed file
   lists, digest §3). Compare against `state.json` → worklist of
   new/changed/deleted per side. Unchanged pairs copy forward untouched.
3. **Candidates (step 2)**: for each doc entity / identifier token →
   `normalizers.match_candidates` against code symbols (`name` +
   `qualified_name` segments) and endpoint labels/paths. Same name in N
   files → N records (never auto-select — plan risk R5). **Stoplist**
   (RT-5): common verbs (`init`, `setup`, `run`, `handle`, …), language
   keywords, and tokens below a minimum length produce no candidates.
4. **Score + provenance (step 3)**: exact match → `score 1.0, provenance
   "exact-name"`. No other provenance in Phase 1 (scope challenge #1).
5. **Stale (step 4)**: changed code file → append stale copies of links
   into that file, re-match; changed doc (SHA from baseline) → same on doc
   side; deleted either side → stale, no re-match.
6. **Write + telemetry (step 5)**: records via `link_store`, then
   `state.json` with SHAs + `coverage` stats.
7. Run as a worker process with the instance lock conventions so it never
   runs concurrently with itself per instance.

## Files

- New: `link-tiny/link_engine.py`, `link-tiny/tests/test_link_engine.py`
- Existing files touched: none (engine imports storage helpers via
  documented paths; if a helper must be exported, it is imported — not
  moved).

## Boundaries (do NOT)

- No writes to either graph, no Qdrant writes, no lease acquisition on
  primary stores.
- No fuzzy/semantic matching (gated on coverage telemetry — scope
  challenge #1).
- No MCP imports.

## Test scope

Mapped acceptance criteria + size: **L** (~10–12 test functions; pipeline
carries the core business rules — Always tier).

| Behavior (map) | Test |
| --- | --- |
| Exact-name match produces `implements` link with provenance+score (typical) | `test_exact_name_link` |
| Qualified name in doc matches short symbol and vice versa (typical) | `test_qualified_bidirectional` (parameterized) |
| Same symbol name in 2 files → 2 records, no auto-selection (R5 rule) | `test_ambiguous_emits_all` |
| Delta: unchanged since last run → zero new records, state updated (boundary) | `test_delta_noop` |
| Code file SHA changed → old links stale + re-matched (data-integrity) | `test_code_drift_stale_rematch` |
| Doc deleted → links stale, no re-match (boundary) | `test_doc_deleted_stale` |
| Doc prose with zero identifier tokens → no records, counted in coverage as unmatched (invalid input) | `test_prose_no_tokens` |
| Reader opens graphs without writer lease; writer can proceed (concurrency guard) | `test_readonly_no_lease` |
| Coverage stats match a hand-computed fixture (telemetry correctness) | `test_coverage_fixture` |
| End-to-end on fixture mini-project: golden links.jsonl (regression anchor) | `test_golden_pipeline` |
