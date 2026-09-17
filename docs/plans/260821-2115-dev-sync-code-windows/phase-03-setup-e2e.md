# Phase 03 — Windows environment setup + end-to-end verification

## Objective

Produce a working Windows runtime (local-only) and prove `dev sync code` green,
with explicit checks that nothing macOS-side is touched.

## Setup steps (this machine, C:\ai\cortex-harness)

1. `dev infra-up` — idempotent local Docker services; only `cortex-falkordb`
   (127.0.0.1:6379, named volume `cortex-falkordb-data`) is required. Verify:
   `docker ps --filter name=cortex-falkordb` → running; `redis-cli`-equivalent
   probe via `falkordb` client `PING`.
2. Update `.cortext-harness/config/dev.json` (Windows only — this file is
   machine-local, not committed):
   - top-level `"storage_backend": "remote"`;
   - top-level `"remote": { "falkordb_uri": "redis://127.0.0.1:6379" }`
     (no qdrant_url → Qdrant stays local-file under
     `C:\Users\quang\.cortext-harness\v1\...`);
   - `code.env.device`: `mps` → `cpu` (Phase 02 also guards this at runtime).
   - Keep `CORTEX_STORAGE_INSTANCE`, `FALKORDB_GRAPH: cortext`,
     `QDRANT_COLLECTION: cortext`, embed model unchanged.
3. Sanity: `dev sync code --dry-run` → child command shows `--falkordb-uri
   redis://127.0.0.1:6379` and NO `--falkordb-path`.

## End-to-end run

4. `echo 0 | .venv\Scripts\python.exe cortex_harness\dev.py sync code` (or
   `dev.bat sync code` from cmd) — expect:
   - graph facts written to Docker FalkorDB (spot-check via Browser UI
     `http://127.0.0.1:3000` or a `MATCH (f:File) RETURN count(f)` probe);
   - embeddings in local Qdrant path;
   - summary JSON written cleanly; exit 0; sync-state baseline advanced.
5. Second run — incremental path: no changes → `[ok] No new commits since last
   sync — skipping` (or equivalent), exit 0.
6. Retry path: kill the Docker container mid-run once → `_run_with_retry`
   retries, failure summary is written WITHOUT the WinError-2 mask.

## macOS non-impact verification

- Runtime isolation (structural, verified by inspection + tests):
  - Windows graph store = local Docker container on 127.0.0.1; macOS keeps its
    embedded `~/.cortext-harness/.../data.rdb` — different machines, different
    stores; no shared host/port/volume.
  - dev.json is per-machine (not in git) — the macOS file is never rewritten by
    this work.
- Code isolation: every behavior change is behind `backend_mode == REMOTE` /
  `FALKORDB_URI` presence / `os.name == "nt"` guards. Local-mode arg-building
  unit tests must show byte-identical output before/after (Phase 04).
- Dependency isolation: requirements markers from commit 8754475 untouched —
  macOS keeps `falkordblite`, Windows keeps `falkordb`.
- Explicitly NOT done: pointing Windows at any macOS-hosted endpoint; changing
  default ports; touching the macOS instance manifest or lock files.
