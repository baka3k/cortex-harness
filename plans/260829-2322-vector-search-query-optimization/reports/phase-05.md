# Phase 05 — Qdrant collection tuning + rebuild tooling — report

Date: 2026-08-30

## What shipped

- **Payload indexes** — `primary_vector_sync._ensure_project_scope_index`
  now creates 4 keyword indexes (idempotent, `wait=True`):
  `project_id_normalized`, `parser`, `root_scope`, `file_path` — the
  exact fields `_delete_stale` filters on. Local-mode clients no-op the
  calls; remote builds them online (no collection rebuild needed).
- **HNSW / quantization opt-in** — `local_qdrant.ensure_collection`
  forwards `hnsw_config` / `quantization_config` built from env
  (`QDRANT_HNSW_M`, `QDRANT_HNSW_EF_CONSTRUCT`, `QDRANT_SCALAR_QUANT=1`
  → int8 always_ram). Unset env (default) sends nothing. When the store
  is local-mode, a one-time-per-process warning
  (`tuning kwargs are inert on local mode`) prevents false performance
  expectations; the kwargs still pass through harmlessly.
  Existing collections never change silently (ensure still refuses
  vector-size drift); the migration path is the rebuild script.
- **Query-time `hnsw_ef`** — `qdrant_query_support.search_collection`
  reads `QDRANT_HNSW_EF` (unset → server default); shipped with the
  phase-04 helper, asserted in `HnswEfEnvTests`.
- **Rebuild script** — `code-tiny/scripts/rebuild_vector_collection.py`:
  copy-vector rebuild (scroll `with_vectors=True` → tuned temp collection
  → count validation → delete source → recreate tuned → re-upload →
  **second count assert on target** (red team #12) → drop temp → layout
  cache invalidate). Destructive steps gated behind `--yes`; without it
  the script stops after the validated copy and prints the plan. Warns
  when pointed at a local-mode store or when no tuning env is set.
  No re-embedding at any point.

## Acceptance

- [x] Local: sync issues all 4 index calls without crash; inert warning
      appears exactly once per process (`ScopeIndexTests`,
      `EnsureCollectionTuningTests.test_local_store_warns_inert_once`).
- [x] Local: `ensure_collection` forwards tuning kwargs only when env is
      set (`test_env_unset_sends_no_tuning`,
      `test_env_set_forwards_hnsw_and_quantization`).
- [x] Rebuild script on stub: full copy, abort-before-delete on count
      mismatch, dry-run leaves source untouched, second count assert
      targeted, failed target validation keeps temp for recovery.
- [ ] **Remote acceptance deferred** — no Qdrant server in this
      environment (per plan: does not block phase 06; coordinate with
      plan `260818-infra-up-remote-support` for provisioning). Items to
      re-check with a server: `payload_schema` shows 4 indexes after
      sync; `unscoped-multi --live --remote` benchmark before/after
      rebuild.

## Tests

`tests/test_qdrant_collection_tuning.py` (9 tests) + `HnswEfEnvTests`
(2, in `tests/test_qdrant_query_support.py`).
