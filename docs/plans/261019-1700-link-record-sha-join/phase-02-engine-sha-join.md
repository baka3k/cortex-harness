# Phase 02 — engine sha-join implementation + telemetry (green)

## Goal

Make the engine join SHAs deterministically per the Phase 01 contract,
surface join quality as telemetry, and turn the red fixture suite green —
including the previously impossible proof that a content edit now
stale-propagates.

## Tasks

1. **Doc-side flatten index** (`link_engine.py`): add
   `flatten_doc_rel(rel)` (`/` and `\` → `__`, mirroring dev.py's
   `source_id` construction) and rewrite `_doc_sha_for` to look up
   direct rel → flattened → stem, against an index built once per build
   (not per record).
2. **Repo-aware code join**:
   - `read_code_symbols` selects `n.repo AS repo` in both the symbol and
     endpoint label queries; `EngineSymbol` gains `repo: str = ""`
     (explicitly NOT part of `identity` — record schema and
     `record_key` stay byte-identical);
   - baselines: extend `load_code_inventory_hashes` (or add
     `load_code_folder_inventories`) to return
     `{norm_repo → {folder_rel → sha}}` keyed by each sync folder's
     resolved absolute path (posix, casefolded on lookup only);
   - `build_links` joins `(repo, path)` first; on miss, the unique
     cross-folder fallback (exactly one folder whose project-relative
     key ends with the graph path); ambiguity or miss → `None`.
3. **Telemetry**: `BuildReport.sha_join` dict
   `{doc_matched, doc_missing, code_matched, code_missing, code_ambiguous}`;
   `dev sync link` prints it in the summary JSON; `link_store.save_state`
   persists it in `state.json`; `link_mcp.trace_status` passes it through.
4. **Drift semantics now real**: `_sha_changed` needs no change — with
   SHAs actually joining, verify via tests that (a) an edited baseline
   SHA stales the file's links and re-appends fresh ones, (b) unchanged
   SHAs keep the delta no-op skip, (c) a deleted graph identity still
   stales.
5. Keep the one-way dependency rule: no imports from `dev.py`,
   `code-tiny` pipelines, or `doc-tiny`.

## Files

- Touched: `link-tiny/link_engine.py`, `link-tiny/link_store.py`
  (state passthrough only), `link-tiny/link_worker.py` (report echo),
  `cortex_harness/dev.py` sync-link summary line (telemetry print only),
  `link-tiny/mcp_server.py` (`trace_status_impl` passthrough)
- Tests: `link-tiny/tests/test_sha_join.py` (green),
  `test_link_engine.py` / `test_link_store.py` / `test_mcp_tools.py`
  extended for telemetry

## Test scope

Mapped to acceptance rows 1–4 of the plan. Expected size **M**:
~15 unit tests across the four suites; no live DB required.
