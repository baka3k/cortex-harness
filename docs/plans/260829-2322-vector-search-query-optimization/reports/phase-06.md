# Phase 06 — Ingest embedder reuse + batch defaults + upsert wait — report

Date: 2026-08-30

## What shipped

- **Embedder reuse** — `sync_vector_documents` default `embedder_factory`
  is now `embed_runtime.get_sentence_transformer` (process-wide cache
  keyed `(model, device)`; `trust_remote_code` param added there to keep
  the call signature compatible). Analyzer processes still load once per
  process, but doc-vectorize and tooling that run many syncs per process
  no longer reload the model per sync. Injected `embedder_factory` keeps
  priority for tests.
- **Upsert wait policy** — intermediate batches `wait=False`, only the
  final batch `wait=True` (durability boundary = function end). Rationale
  per red team #8: upserts target the live collection (no staging gate on
  this path — that belongs to pending plan `260807-0929`), so partial
  visibility mid-sync already exists today; a crash mid-sync is healed by
  the next sync's `_delete_stale` + re-upsert, and per-batch waiting only
  added latency.
- **Batch defaults** — dev-init `code.env.BATCH_SIZE` and
  `doc.env.BATCH_SIZE` default `"1"` → `"8"` (`MAX_EMBED_CHARS` 500 kept:
  content shape, not perf). Analyzer `--batch-size` argparse fallbacks
  `EMBED_BATCH_SIZE` env → else **8** (was 4) across 19 analyzer
  entrypoints (python, go, java, js, ts, php, perl, vb, kotlin,
  android_kotlin, android_java, csharp, delphi, plsql, sql, rust,
  flutter, swift, incremental_sync); cplus was already 8.
  `qdrant_batch_size` 128 untouched.

## Gate evidence (G6)

Real jina-v3 on auto-detected MPS, 500-char synthetic docs,
`normalize_embeddings=True` (sync's exact settings):

| Config | Throughput |
|--------|-----------|
| batch=1 (old default) | 39.8–44.0 docs/s |
| batch=8 (new default) | 92.2–98.4 docs/s (**2.3×**) |
| batch=16 (measured for reference) | 99.9 docs/s (2.5×) |

**G6 settles at 2.3×, not the tentative 3×** — the plan's escape clause
applies: with `MAX_EMBED_CHARS=500` the per-document encode cost bounds
the batching gain; on CPU-only hosts the ratio is typically larger, on
MPS it saturates around 2.5× (batch=16). Recommendation recorded for a
future default bump to 16 (memory-permitting) rather than changing it
silently here. Total sync wall additionally gains from wait=False
intermediate batches and (for multi-sync processes) zero model reloads.

## Tests

- `tests/test_primary_vector_sync.py::UpsertWaitPolicyTests` — explicit
  wait contract (5 docs / batch 2 → `[False, False, True]`; single batch
  → `True`); default factory resolves through the shared runtime with
  `(model, device, trust_remote_code=False)`; injected factory keeps
  priority.
- `tests/test_dev_init_storage_backend.py::
  test_blank_input_defaults_batch8_and_auto_device` — fresh init writes
  `BATCH_SIZE=8` (code + doc) and `device=auto`.
- `tests/test_qdrant_collection_tuning.py::ScopeIndexTests` updated:
  single-batch sync upserts with `wait=True`.

## Notes

- Mid-sync `kill -9` smoke (acceptance item): the self-heal path is the
  pre-existing `_delete_stale` + re-upsert behavior, asserted by
  `tests/test_primary_vector_sync.py` cleanup tests; a manual live
  kill-and-resync was not performed (no indexed project data in this
  environment — same limitation as the `validate_retrieval` run).
