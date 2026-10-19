# Phase 04 — `dev sync link` CLI group + worker classifier

## Goal

Register the builder in the CLI grammar as the third sync subgroup, with
`--dry-run` / `--project` / `--reconcile`, and make `dev stop` able to
manage the link worker. This is the first (and only code) touch on existing
files — strictly additive.

## Tasks

1. `cortex_harness/dev.py`:
   - `@sync.group("link")` next to `@sync.group("code")` / `@sync.group("doc")`
     (digest §6 for the exact group wiring + lock timeout conventions).
   - Commands: default incremental build; `--dry-run` prints would-be links
     (project, doc ref → code ref, score) without writing; `--project <id>`
     scopes one project (default: all registered/active); `--reconcile`
     forces full rebuild ignoring `state.json`.
   - Run the engine as a worker subprocess registered in the process table
     (same lifecycle as code/doc workers) so Ctrl+C / `dev stop` behaves.
2. `cortex_harness/sync_processes.py`: add `_is_link_worker()` matching the
   link worker command signature and widen `SyncOwner` so the worker is
   classifiable (digest §6 for the record-matching details).
3. `scripts/mcp-lifecycle.py`: add the link server to `SERVERS` (:68–81,
   startswith-matched — verify entry order, RT-4) and confirm
   `requirement_files()` picks up `link-tiny/requirements.txt` (:293).
   Lifecycle smoke test lands with Phase 05's server.
4. Launch entry so `dev start --server link` works is Phase 05's scope —
   this phase only delivers the sync side.

## Files

- Existing (additive): `cortex_harness/dev.py` (~1 group block),
  `cortex_harness/sync_processes.py` (~10 lines).
- New: `link-tiny/tests/test_cli_link.py` (or root `tests/` per digest §8
  conventions — CLI tests live where existing sync CLI tests live).

## Boundaries (do NOT)

- `dev sync code` / `dev sync doc` command bodies: zero changes. The group
  registration must not alter the `sync` group's own behavior.
- No auto-invocation of link from code/doc sync (scope challenge #3).
- No `--server` Choice change yet (Phase 05).

## Test scope

Mapped acceptance criteria + size: **M** (~7 test functions).

| Behavior (map) | Test |
| --- | --- |
| `dev sync link` runs engine for all active projects and writes links (typical, e2e on fixture) | `test_sync_link_default` |
| `--dry-run` writes nothing but prints pairs (data-integrity) | `test_dry_run_no_write` |
| `--project X` touches only X's links dir (scoping rule) | `test_project_scope` |
| `--reconcile` ignores state.json and rebuilds (boundary) | `test_reconcile_full` |
| `dev stop` terminates the link worker (lifecycle) | `test_stop_link_worker` |
| `dev sync code` / `dev sync doc` behavior byte-identical to before (isolation regression — the plan's core promise) | `test_old_sync_untouched` |
| Unknown project id → clear error, no partial writes (invalid) | `test_unknown_project_error` |
