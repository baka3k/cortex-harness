# Phase 02 — link_store.py: append-only store + state.json + stale propagation

## Goal

The only module that writes link data. Append-only JSONL with logical
deletes (`status: "stale"`), plus the `state.json` snapshot that makes
delta runs and staleness detection possible. Pure file I/O — no graph
access, no MCP.

## Tasks

1. `link_store.py`:
   - `append_links(project_dir, records)` — JSONL append, one record per
     line, schema per plan (project_id, relation, doc, code, score,
     provenance, status, created_at). Validates required fields
     (provenance + score mandatory — invariant 4).
   - `load_links(project_dir, status=None)` — read path used by both the
     engine (previous state) and `mcp_server.py`.
   - `load_state(project_dir)` / `save_state(project_dir, state)` —
     `state.json`: `{schema_version, built_at, doc_shas: {...},
     code_shas: {...}, counts, coverage}`.
   - `mark_stale(project_dir, predicate)` — appends stale copies of records
     matching a predicate (doc sha changed / code sha changed / doc or code
     deleted). Never rewrites or removes existing lines.
   - `coverage_stats(records)` — % docs with ≥1 active link, % symbols
     referenced, provenance distribution (the Phase-3 gate telemetry).
   - Path normalization at the store boundary: all `path` fields stored in
     POSIX form (RT-6) so link identity is OS-stable.
2. Atomicity: writes go to `.tmp` + rename (mirror `layout.py`'s tmp-file
   pattern for manifest writes).
3. Schema version field on both files from day one (`schema_version: 1`).

## Files

- New: `link-tiny/link_store.py`, `link-tiny/tests/test_link_store.py`
- Existing files touched: none.

## Boundaries (do NOT)

- No rewrite-in-place of `links.jsonl` ever (not even compaction — a future
  `--compact` flag is out of scope for Phase 1).
- No graph or MCP imports in this module.

## Test scope

Mapped acceptance criteria + size: **M** (~8 test functions).

| Behavior (map) | Test |
| --- | --- |
| Append then load round-trips records intact (typical) | `test_append_load_roundtrip` |
| Record missing provenance/score rejected (invalid) | `test_reject_missing_provenance` |
| mark_stale appends stale copies, original lines untouched (data-integrity — Always tier) | `test_stale_appends_not_rewrites` |
| Repeated append after crash mid-write leaves valid JSONL (boundary: last line truncated → skipped with warning, file readable) | `test_tolerate_truncated_tail` |
| state.json save/load roundtrip + schema_version (typical) | `test_state_roundtrip` |
| coverage_stats on empty / partial / full link sets (boundary) | `test_coverage_stats` (parameterized) |
| Concurrent appends serialized by OS-level file lock within instance (digest §3/§4 lock conventions) | `test_append_lock` |
