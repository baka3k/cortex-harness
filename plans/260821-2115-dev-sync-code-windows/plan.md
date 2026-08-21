---
title: "dev sync code on Windows — Remote FalkorDB Plumbing + Platform Fixes"
status: active
created: 2026-08-21
updated: 2026-08-21
mode: hi-plan (fast)
scope: cortex_harness/dev.py, cortex_harness/storage/config.py, code-tiny/tools/sync/incremental_sync.py, code-tiny/tools/graph/cli.py + core/factory.py, code-tiny/tools/common/harness_config.py, tests, Windows dev.json setup
relatedPlans:
  - 260817-storage-backend-adapter
  - 260818-infra-up-remote-support
  - 260820-infra-up-docker-idempotent
  - 260820-dev-init-backend-selection
  - 260813-2152-code-sync-phase-modes
blockedBy: []
blocks: []
---

# dev sync code on Windows — Remote FalkorDB Plumbing + Platform Fixes

## Goal

`dev sync code` must complete end-to-end on Windows, while macOS behavior stays
byte-for-byte identical (local embedded FalkorDBLite continues to work there and
no running macOS job is disturbed).

## Verified root causes (reproduced on Windows 2026-08-21)

1. **Embedded backend impossible on win32.** `falkordblite` (redislite) has no
   Windows support; commit 8754475 already pinned `falkordblite ; sys_platform != 'win32'`
   and `falkordb ; sys_platform == 'win32'`. But `dev sync code` in the default
   local mode passes `--falkordb-path <data.rdb>` and every child opens the
   embedded backend → child fails with
   `graph schema/project setup failed before streaming: Local FalkorDB backend
   requires the 'falkordblite' package` (exit non-zero, 1 error in summary).
2. **Sync pipeline has no remote FalkorDB path.** `storage_backend: remote` +
   `remote.falkordb_uri` exist in config (`resolve_storage`/`validate_backend_config`,
   `StorageFactory` REMOTE branch, `FalkorDBDriver(uri=...)` network branch all
   work), but the sync chain never consults them:
   - `_storage_env_for_process` passes only `cfg["code"]["env"]` to
     `resolve_storage`, so top-level `storage_backend`/`remote` are invisible.
   - `storage_overlay` always emits `FALKORDB_PATH` (never `FALKORDB_URI`).
   - `_neo4j_args_code` / `_env_to_neo4j_args` always emit `--falkordb-path`.
   - `incremental_sync.py` has no `--falkordb-uri`; `_query_impacted_files`,
     `_project_topology_bootstrap_needed`, `_resume_configured_journal`, and the
     analyzer env builder are path-only.
   - `code-tiny/tools/graph/cli.py` `prepare_graph_args` /
     `create_graph_driver_from_args` and `GraphDriverFactory.create_from_env`
     force/synthesize a local path.
3. **Windows crash in `_write_summary` (incremental_sync.py:1368).** The atomic
   write does `os.open(dir, os.O_RDONLY)` + `os.fsync(fd)` for durability; on
   Windows opening a directory raises `PermissionError`. The retry loop then
   calls `os.replace` again — the tmp file was already renamed on attempt 1 —
   surfacing as `[WinError 2] ... .tmp -> .json` and masking the real error.
   (Verified in isolation: replace OK, dir open fails.) The same
   POSIX-only pattern existed in `reliability.atomic_write_run_result`,
   `journal/artifacts._fsync_directory`, `storage/generation.py`, and
   `servlet_jsp/cache.py`.
4. **`device: mps` leaks to Windows.** The macOS-era dev.json sets
   `device: mps` → `EMBED_DEVICE=mps`; `_resolve_embed_device` returns a
   non-`auto` request unchanged, and sentence-transformers has no MPS on win32.
   (`device: cuda` on a CPU-only torch build has the same problem.)

### Additional Windows defects found during E2E verification

5. **Concurrent sync runs assassinate each other (the visible "hang").**
   `_sync_process_scope`'s startup sweep (`stop_sync_processes`) terminated ANY
   live `dev sync <owner>` launcher — with two agent terminals on one machine,
   each new run killed the previous one right after the folder prompt (silent
   exit code 15 = psutil TerminateProcess on win32). Fixed with a per-owner
   advisory lock (`portalocker`, OS-released on death) + launcher-free
   lifecycle sweeps; `dev sync <owner> stop` still kills launchers explicitly.
6. **`os.fchmod` missing on Windows** in `parse_quality.atomic_write_json` and
   `servlet_jsp/cache.py` (+ unlink-while-open in their cleanup paths).
7. **Backslash graph identity.** `python_analyzer.parse_python_file` used raw
   `os.path.relpath` for File ids (backslashes on win32) while import targets
   and relation endpoints use forward slashes → identity mismatch crashes
   (`cannot infer target_label`). The android analyzer had the same class of
   bug for File/Directory rows plus missing `project_id_normalized` on its
   custom node queries (relation preflight audits match on it).
8. **cplus tail/inferred relations lacked `project_id`** (latent on all
   platforms — this repo's fixtures trigger it): the final `write_all` call
   passes no projects/files rows so no default scope can be inferred.
9. **tree-sitter-perl 1.2.1 capsule cannot be constructed on win32**
   (int pointer → `Language()` overflow; works on POSIX because C
   `unsigned long` is 64-bit there). The analyzer now degrades to a warned
   skip (exit 0) when the parser capability is unavailable on the platform.

## Architecture decision

Windows follows the direction already chosen in commit 8754475 and the active
storage/infra plans: **local-mode FalkorDB stays the default everywhere; when a
project selects `storage_backend: "remote"` (Windows uses a FalkorDB Docker
container pinned to 127.0.0.1 on the same machine), the sync chain connects via
`FALKORDB_URI` with the `falkordb` client.** No new backend, no protocol change.

Isolation contract (macOS safety):

- Every code change is gated on remote mode / env presence; macOS dev.json stays
  `storage_backend: local` → embedded path, identical args, identical packages.
- Docker containers are per-machine (`127.0.0.1` pinned ports, named volumes);
  Windows never points at a host other than localhost.
- Qdrant stays local-file on Windows (qdrant-client local mode is
  platform-independent), so only the graph store changes.

## Phases

1. [Phase 01 - Remote FalkorDB plumbing through the sync chain](phase-01-remote-plumbing.md)
2. [Phase 02 - Windows platform fixes](phase-02-windows-fixes.md)
3. [Phase 03 - Windows environment setup + end-to-end verification](phase-03-setup-e2e.md)
4. [Phase 04 - Tests and regression guards](phase-04-tests.md)

## Success criteria

- On Windows: `dev sync code` exits 0, writes the graph to the local Docker
  FalkorDB (`redis://127.0.0.1:6379`, graph `cortext`), embeddings to local-file
  Qdrant, and writes a clean summary JSON (no WinError 2).
- On macOS: `dev sync code` on a local-mode project produces the exact same
  command lines and behavior as before this plan (regression-checked via
  existing tests + arg-building unit tests).
- No code path on either platform writes to a non-localhost remote unless a user
  explicitly configures one.
- `pytest tests/` green on Windows for the touched suites; existing storage
  backend tests still pass unchanged.

## Out of scope

- `dev sync doc` remote support (same pattern can follow later).
- Making `falkordblite` work on Windows (upstream redislite limitation).
- Provisioning graphs/collections on remote servers automatically
  (`dev infra-up --provision` already covers it).
