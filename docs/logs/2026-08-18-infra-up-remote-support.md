# infra-up Remote Support — Qdrant & FalkorDB Lifecycle — 2026-08-18

## Context

Plan `plans/260818-infra-up-remote-support/plan.md` was created after the storage-backend-adapter plan (`plans/260817-storage-backend-adapter`) added the remote Qdrant + FalkorDB layer. Until now `make infra-up` was a deprecated alias that only invoked `storage-init`; operators running remote projects had no lifecycle command that probed or provisioned their server endpoints. The plan's brief was to un-deprecate `infra-up` so it routes to local storage init for `storage_backend: "local"` projects and validates connectivity (with optional provisioning) for `storage_backend: "remote"` projects, and to extend `make doctor` with the same reachability checks.

## Change

- New module `cortex_harness/storage/remote_probe.py:24` exposes `ProbeResult` / `ProvisionResult` dataclasses and the helpers `probe_qdrant`, `probe_falkordb`, `probe_all`, `provision_qdrant_collection`, `provision_falkordb_graph`, `setup_remote_falkordb_schema`, `force_local_active`, plus the `PROVISION_TAGS` map and `render_provision_line` formatter used by the lifecycle output. `__init__.py` re-exports the public surface at `cortex_harness/storage/__init__.py:58`.
- `scripts/mcp-lifecycle.py:274` adds `_scan_project_backends` (reads `*.cortext-harness/config/*.json`, defaults missing `storage_backend` to `"local"`, silently skips malformed JSON).
- `scripts/mcp-lifecycle.py:308` adds `_resolve_collection_names` (reads `code.env.QDRANT_COLLECTION` and `doc.env.QDRANT_COLLECTION_DOC` / `DOC_FALKORDB_GRAPH`, falls back to convention).
- `scripts/mcp-lifecycle.py:363` adds `_provision_remote_project` orchestrating the Qdrant + FalkorDB resources per remote project, including schema setup delegated to `code-tiny/scripts/setup_constraints.py`.
- `invoke_infra_up` rewritten at `scripts/mcp-lifecycle.py:402` to delegate `ensure_layout` for local projects and probe each remote project; failures exit 1 with per-check status. `invoke_infra_down` rewritten at `scripts/mcp-lifecycle.py:466` to close cached remote clients via `reset_remote_clients`.
- `scripts/mcp-lifecycle.py:712` adds `doctor_remote_checks` (honors `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` as a bypass, reports per-project reachability, treats partial configs — only qdrant_url or only falkordb_uri — as optional skips). Wired into `invoke_doctor` at `scripts/mcp-lifecycle.py:842`.
- `scripts/mcp-lifecycle.py:1447` adds `infra_up_options` so the lifecycle accepts `--provision`; `main()` dispatches it at `scripts/mcp-lifecycle.py:1496`.
- `cortex_harness/dev.py:2021` adds `--provision` to the `infra-up` Click command; `infra-down` docstring refreshed to reflect that it closes remote clients.
- `Makefile:28` forwards `INFRA_ARGS` to the lifecycle script.
- New `tests/test_infra_remote.py` (24 tests) and `tests/test_doctor_remote.py` (6 tests); `tests/test_make_lifecycle.py` and `tests/test_storage_lifecycle.py` updated to remove the deprecation assertions and add coverage for routing, probe failures, the provision flag, and `infra-down` close behavior.

## Impact

Impact level: medium. Operators with remote projects can now run a single command to validate connectivity, create the required Qdrant collections and FalkorDB graphs (with the canonical schema via `setup_constraints.py`), and have `make doctor` report per-project reachability — closing the gap that the storage-backend-adapter plan left at the lifecycle layer. Local projects are unchanged (default `storage_backend: "local"` keeps the existing `ensure_layout` path), so no behavioral risk for the common case. Per-check `SystemExit(1)` semantics preserve `make infra-up` as a gate in CI without blocking local projects when only remote projects are unreachable. No credentials are logged: `RemoteStorageConfig.__repr__` already redacts `qdrant_api_key` / `falkordb_password`, and the new subprocess wrapper passes passwords only as positional CLI args to `setup_constraints.py`.

## Decision

A single shared `remote_probe.py` module was chosen over duplicating probe/provision logic between `mcp-lifecycle.py` and `dev.py` — both call sites need identical reachability semantics and the same collection-graph-naming convention, and a single module lets the doctor output stay consistent with `infra-up` output. The `doctor_remote_checks` function intentionally mirrors the lifecycle probe format (`[ok]` / `[fail]` tags plus URL) so the two commands can be diffed at a glance. Provisioning is opt-in via `--provision` rather than automatic: pre-existing remote projects may already carry hand-tuned collections or be running against a shared server, and silently creating collections on first connect would surprise operators. Force-local env override (`CORTEX_STORAGE_BACKEND_FORCE_LOCAL`) is honored symmetrically in doctor and lifecycle so the rollback path stays observable in both.

## References

- plan: `plans/260818-infra-up-remote-support/plan.md:1`
- related plan: `plans/260817-storage-backend-adapter/plan.md:1` (consumed adapter layer)
- commit: `a5a6272` — `feat(lifecycle): remote-aware infra-up + doctor checks`
- tests: `tests/test_infra_remote.py:1`, `tests/test_doctor_remote.py:1`, `tests/test_make_lifecycle.py:1`, `tests/test_storage_lifecycle.py:1`