# Phase 05 — Doctor drift report (warn-only)

## Objective

One command answers "registry vs stores: cái nào lệch?" — the operator-facing companion that keeps discovery honest. Report-only in this plan (`--fix` deferred). Lives in `scripts/mcp-lifecycle.py` doctor (natural home per research: `doctor_check` primitive `:1117-1129`, warn-only precedent `required=False` at `:1132`, registry scan `_scan_project_backends` `:442`, surfaced via existing `dev doctor` → `make doctor`).

## Checks (all `required=False`, i.e. `[doctor][warn]` — never fail CI)

1. **Unregistered data shards**: Qdrant `list_collection_names()` shards matching `*_doc` (and FalkorDB `list_graphs()` graphs matching `*_doc`) with no registry entry deriving that name → `warn: shard 'X_doc' holds N points but no registered project maps to it (run dev init / register)`. Qdrant counts via `get_collection_info`; graph emptiness needs a Cypher count — **only counted for graphs that exist** (listed first; a count query on an existing graph materializes nothing new).
2. **Declared-but-absent**: registry-declared `doc_qdrant_collection` / doc graph missing from the store → `warn: registry declares 'X' for project P but it does not exist on <store>`.
3. **Empty junk**: Qdrant collections with `points_count == 0` (and graphs with 0 entities) whose names look like doc shards → `warn: 'X_doc' is empty — likely junk from mis-scoped queries; safe to drop` (naming the deletion is the operator's action; the doctor never deletes).
4. **Malformed config files**: extend `_scan_project_backends` (`:442`) to surface JSON files it currently silently skips → `warn: config file 'broken.json' is not valid JSON — project invisible to the registry`.
5. **Dual-key notice**: registry entries whose `doc.env` sets only legacy `QDRANT_COLLECTION` → info-level note (Phase 02 made both keys valid; visibility for future cleanup).

## Changes

- New check group function in the `doctor_*_checks` family, registered in `invoke_doctor()` / `ACTIONS` (`:1359`, `:2010`).
- Reuses Phase 03's existence primitives (`list_graphs`, `collection_exists`/`list_collection_names`) — no new probing idioms; never calls Cypher/CREATE on an absent name.
- Store endpoints: the doctor's existing remote/local config resolution (`_scan_project_backends`, remote probe config) — no new env vars.
- Windows: `mcp-lifecycle.ps1` has a parallel doctor (`:537`); this phase is Python-only — the ps1 twin diverges on these checks, which is **documented in Phase 06** (win32 users get no drift report; acceptable, disclosed).
- Deletion-friendly output: each warn line is copy-pasteable action (`GRAPH.DELETE`, Qdrant delete collection) without the doctor executing it.

## Acceptance criteria

- AC1: fixture store with an unregistered non-empty shard + a registered-but-absent declared collection + an empty `*_doc` collection + a malformed config JSON → four distinct `[doctor][warn]` lines, exit code still 0 (warn-only semantics).
- AC2: clean state → no drift warnings, `[doctor][ok]` group line present.
- AC3: doctor never issues a write/create/delete against any store (assert on fake clients).
- AC4: `dev doctor` (thin delegator, `cortex_harness/dev.py:2793-2796`) surfaces the new group with caller cwd resolution intact (`:2590-2640`).

## Test scope

- New: `tests/test_doctor_scope_drift.py` — AC1..AC3 (pattern: `tests/test_doctor_remote.py` imports the lifecycle module directly; fake Qdrant/FalkorDB clients with call logs).
- Run: `tests/test_doctor_remote.py`, merge-gate set.
- **Size: M**
