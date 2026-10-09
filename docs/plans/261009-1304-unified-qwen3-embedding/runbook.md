# Runbook — Re-index onto Qwen/Qwen3-Embedding-0.6B

Plan: [plan.md](plan.md) (261009-1304). Contract summary:
[UNIFIED_INGEST_QUERY_CONTRACT.md](../../../docs/UNIFIED_INGEST_QUERY_CONTRACT.md)
(§ Embedding-model swap playbook).

Safety invariants (red-team F9/F2/D11):

* **Never drop without an export.** `db_transfer export` is the only
  rollback path.
* **Pilot before fleet.** One small project end-to-end, A/B gate passed,
  then repeat per project.
* **`{base}_mess` collections are hash-vector** (message_scan) — the model
  swap does not touch them, and the reset tools never drop them unless
  `--include-messages` is passed explicitly.
* The per-project resets **delete the embedding-model sentinel**; a
  collection holding only the sentinel also re-stamps automatically, so
  the runbook cannot deadlock against a stale marker.

## 0. Prepare

```bash
# 1. `make build` / `make install` pre-download the model snapshot
#    automatically (best-effort: offline hosts just get a WARN, the build
#    still succeeds). Manual download if you prefer:
hf download Qwen/Qwen3-Embedding-0.6B
#    Opt out of the build-time prefetch: export CORTEX_SKIP_MODEL_PREFETCH=1
#    Offline hosts: export CODE_EMBEDDING_MODEL_PATH / EMBEDDING_MODEL_PATH
#    to the snapshot directory instead.

# 2. Dependency floors must be installed (requirements.txt):
#    transformers>=4.51,<4.56 ; sentence-transformers>=2.7.0
# embed_runtime fails fast with an actionable error when loading Qwen3
# on an older transformers.

# 3. Existing `dev init` configs keep the OLD model. Re-init now offers
#    the unified model as the default for stored legacy literals
#    (jinaai/jina-embeddings-v3, BAAI/bge-m3) — press Enter to migrate.
#    Deliberate custom models / local paths are preserved. Or export:
export EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

List the projects to migrate (registry is the source of truth):

```bash
cat .cortext-harness/registry/*.json   # or the registry UI your team uses
```

## 1. Pilot — doc side (project X)

```bash
# a. Backup (position recorded in verification-report.md)
python cortex_harness/db_transfer.py export --role doc --project-id X --output backups/doc-X-$(date +%F).cortexdb

# b. Reset (clears project points AND the sentinel; keeps the collection)
python doc-tiny/0_reset_all.py --project-id X --dry-run
python doc-tiny/0_reset_all.py --project-id X --force

# c. Re-ingest with the new default
dev sync doc all
```

## 2. Pilot — code side (project X)

```bash
# a. Backup
python cortex_harness/db_transfer.py export --role code --project-id X --output backups/code-X-$(date +%F).cortexdb

# b. Reset (drops collection {X}; {X}_mess kept unless --include-messages)
python code-tiny/scripts/reset_code_collection.py --project-id X --dry-run
python code-tiny/scripts/reset_code_collection.py --project-id X --force

# c. Re-ingest
dev sync code all
```

## 3. A/B gate (see verification-report.md for the template)

Fixed query set: ≥10 EN + ≥5 VI + ≥5 JA + ≥5 identifier/code queries.

* Before-swap baseline: run against the exported (old-model) collection.
* After-swap: `unified_mcp semantic_search` (code) +
  `mind_mcp semantic_search` / `hybrid_search` (doc).
* Metric: hit-correctness ratio per language bucket — **must not regress**.
* Cross-space check: cosine(function vector, doc-paragraph vector) through
  Qwen3 must return meaningful neighbors (the entire point of unification).
* Timing: record embed latency on MPS/CUDA (`[semantic_search] model=…
  vector_len=…` logs); keep within the accepted envelope.
* Optional harness: `scripts/validate_retrieval.py`, `tests/benchmark_*.py`.

**Gate fail → rollback:** re-import the backups and restart the MCP
servers with the old model env. **Do NOT reset or re-embed after the
import** — that would delete the very vectors you just restored, and
rollback would then depend on the legacy model still being downloadable.

```bash
python cortex_harness/db_transfer.py import --role doc  --archive backups/doc-X-<date>.cortexdb --overwrite 1
python cortex_harness/db_transfer.py import --role code --archive backups/code-X-<date>.cortexdb --overwrite 1
export EMBEDDING_MODEL=BAAI/bge-m3              # doc env (or re-init)
export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3
# Restart the MCP servers so the env change takes effect, then verify with
# the baseline query set. The imported collection keeps its legacy marker,
# so old-model queries run clean and a future Qwen3 ingest still raises.
```

Limitation: `db_transfer` bundles **local-file lanes only** — remote-mode
projects must export from the Qdrant server side (snapshot/API) before
dropping; the reset script refuses to silently no-op on a backend that
lacks the collection.

## 4. Fleet roll-out (gate passed)

For each remaining project: repeat §1–§2 (skip §3's full A/B; run the
standard sanity queries). If incremental sync wedges on stale state:

```bash
rm -rf .cortext-harness/sync-state/   # clears change tracking only, then full sync
```

## 5. Post-checks

* Query a stamped collection with the wrong model env → response contains
  `embedding_model_mismatch` (code: `errors[]`; doc: response field) — do
  not ignore it.
* Ingesting into a different-model collection raises
  `EmbeddingModelMismatchError` naming the reset command — expected guard,
  not a bug.
* `rg "jina-embeddings-v3|bge-m3"` (excluding `*.bak`, `docs/plans`,
  `docs/logs`) only matches legacy-option docs, policy code and tests.
