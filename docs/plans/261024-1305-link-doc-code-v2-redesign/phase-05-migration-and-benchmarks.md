# Phase 05 — Live Reconcile Migration, Cleanup & Documentation

## Goal

Reconcile live project link stores from the 8.4 GB v1 `links.jsonl` format to the compact v2 `links.db` SQLite store, verify end-to-end MCP tool queries and latency budgets, and update `docs/LINK_CONTRACT.md` and `link-tiny/README.md`.

## Tasks

1. **Automatic Cleanup of Legacy Oversized `links.jsonl`**:
   - During `dev sync link --reconcile` (or when `links.db` is initialized against a legacy `links.jsonl` larger than `50 MB`), remove/truncate the obsolete multi-GB `links.jsonl` after `links.db` and `state.json` are committed atomically.
2. **End-to-End Verification & Benchmark**:
   - Run `dev sync link --reconcile` on registered projects.
   - Verify:
     - Active link count drops from `~16.4M` to a high-signal set (`~15k–60k`).
     - Disk usage under `{instance}/links/{project_id}/` drops from `8.4 GB` to `< 30 MB`.
     - `sha_join.doc_missing` drops from `99.7%` to `< 1%`.
     - `HttpEndpoint` and `Route` report `kept == expected`.
     - `find_code_for_doc` and `find_doc_for_code` return top-ranked relevant files/docs in `< 10 ms`.
3. **Documentation Updates**:
   - Update `docs/LINK_CONTRACT.md` and `link-tiny/README.md` to document `links.db` (SQLite WAL), the IDF + affinity scoring formula, provenance values (`exact-file-stem`, `exact-qualified`, `exact-scoped`, `exact-name`), and multi-resolution query matching rules.

## Files

- Modify: `link-tiny/link_engine.py`, `link-tiny/link_store.py`, `docs/LINK_CONTRACT.md`, `link-tiny/README.md`

## Test Scope

Mapped acceptance criteria + size: **S** (~4 test functions + live verification).

| Behavior | Test |
| --- | --- |
| `--reconcile` replaces legacy oversized `links.jsonl` with compact `links.db` + `state.json` | `test_reconcile_compacts_legacy_jsonl` |
| Cross-tree isolation (`test_link_acceptance.py`) remains 100% green | `test_acceptance_isolation` |
