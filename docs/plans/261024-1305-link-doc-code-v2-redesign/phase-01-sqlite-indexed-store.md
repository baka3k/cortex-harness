# Phase 01 — Indexed SQLite WAL Store (`link_store.py`)

## Goal

Replace the unindexed multi-gigabyte `links.jsonl` scan with a normalized, indexed SQLite WAL store (`links.db`) under `{instance_root}/links/{project_id}/`, while keeping `state.json` and the existing `link_store` API (`append_links`, `latest_links`, `mark_stale`, `load_state`, `save_state`, `coverage_stats`) backward-compatible for unit tests and callers.

## Tasks

1. **Schema design (`links.db`)**:
   - Open with `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA temp_store=MEMORY; PRAGMA foreign_keys=ON;`.
   - `docs` table:
     ```sql
     CREATE TABLE IF NOT EXISTS docs (
         doc_uid INTEGER PRIMARY KEY,
         doc_id TEXT NOT NULL UNIQUE,
         norm_doc_id TEXT NOT NULL,
         stem TEXT NOT NULL,
         source_sha TEXT
     );
     CREATE INDEX IF NOT EXISTS idx_docs_norm ON docs(norm_doc_id);
     CREATE INDEX IF NOT EXISTS idx_docs_stem ON docs(stem);
     ```
   - `paragraphs` table:
     ```sql
     CREATE TABLE IF NOT EXISTS paragraphs (
         para_uid INTEGER PRIMARY KEY,
         doc_uid INTEGER NOT NULL REFERENCES docs(doc_uid),
         paragraph_id TEXT NOT NULL,
         snippet TEXT,
         UNIQUE(doc_uid, paragraph_id)
     );
     CREATE INDEX IF NOT EXISTS idx_para_doc ON paragraphs(doc_uid);
     ```
   - `symbols` table:
     ```sql
     CREATE TABLE IF NOT EXISTS symbols (
         sym_uid INTEGER PRIMARY KEY,
         path TEXT NOT NULL,
         norm_path TEXT NOT NULL,
         basename TEXT NOT NULL,
         file_stem TEXT NOT NULL,
         name TEXT NOT NULL,
         norm_name TEXT NOT NULL,
         match_key TEXT NOT NULL,
         qualified_name TEXT NOT NULL DEFAULT '',
         target_type TEXT NOT NULL DEFAULT 'symbol',
         label TEXT NOT NULL DEFAULT '',
         parser_type TEXT NOT NULL DEFAULT '',
         start_line INTEGER,
         end_line INTEGER,
         repo TEXT NOT NULL DEFAULT '',
         source_sha TEXT,
         file_df INTEGER NOT NULL DEFAULT 1,
         UNIQUE(norm_path, name, start_line, end_line, target_type)
     );
     CREATE INDEX IF NOT EXISTS idx_sym_name ON symbols(norm_name);
     CREATE INDEX IF NOT EXISTS idx_sym_key ON symbols(match_key);
     CREATE INDEX IF NOT EXISTS idx_sym_path ON symbols(norm_path);
     CREATE INDEX IF NOT EXISTS idx_sym_base ON symbols(basename);
     CREATE INDEX IF NOT EXISTS idx_sym_stem ON symbols(file_stem);
     ```
   - `links` table:
     ```sql
     CREATE TABLE IF NOT EXISTS links (
         para_uid INTEGER NOT NULL REFERENCES paragraphs(para_uid),
         sym_uid INTEGER NOT NULL REFERENCES symbols(sym_uid),
         doc_uid INTEGER NOT NULL REFERENCES docs(doc_uid),
         relation TEXT NOT NULL DEFAULT 'implements',
         provenance TEXT NOT NULL,
         score REAL NOT NULL,
         status TEXT NOT NULL DEFAULT 'active',
         created_at TEXT NOT NULL,
         staled_at TEXT,
         PRIMARY KEY (para_uid, sym_uid)
     );
     CREATE INDEX IF NOT EXISTS idx_links_doc_score ON links(doc_uid, status, score DESC);
     CREATE INDEX IF NOT EXISTS idx_links_sym_score ON links(sym_uid, status, score DESC);
     CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);
     ```
2. **Batch writer & delta mutations**:
   - `upsert_symbols_batch(project_dir, symbols)` and `upsert_paragraphs_batch(project_dir, paragraphs)` using `executemany` inside a single transaction.
   - `upsert_links_batch(project_dir, records)`: upserts active records (`ON CONFLICT(para_uid, sym_uid) DO UPDATE SET status='active', score=excluded.score, provenance=excluded.provenance, staled_at=NULL`).
   - `mark_stale_by_scope(project_dir, *, doc_ids=..., code_paths=..., reconcile=...)`: updates `status='stale', staled_at=...` via indexed SQL `UPDATE` in milliseconds instead of scanning millions of JSON lines in Python.
3. **Compatibility layer**:
   - Keep `append_links`, `latest_links`, `load_links`, `mark_stale` working seamlessly backed by `links.db` (and auto-migrating small legacy `links.jsonl` files when `< 50 MB`, or archiving oversized multi-GB `links.jsonl` on `--reconcile`).
   - Compute `coverage_stats` directly with SQL `COUNT(DISTINCT ...)` aggregations (`< 5 ms`).

## Files

- Modify: `link-tiny/link_store.py`, `link-tiny/tests/test_link_store.py`

## Test Scope

Mapped acceptance criteria + size: **M** (~8 test functions).

| Behavior | Test |
| --- | --- |
| Schema creation + WAL mode + batch upsert round-trip | `test_sqlite_store_roundtrip` |
| Upserting duplicate `(para, sym)` updates status/score in place without growing rows | `test_upsert_idempotent` |
| SQL-based `mark_stale` transitions matching links and preserves non-matching active links | `test_mark_stale_sql` |
| `coverage_stats` via SQL matches Python calculation on fixture | `test_sql_coverage_stats` |
| Read-only connection (`mode=ro`) succeeds concurrently while writer holds connection | `test_wal_concurrent_read` |
