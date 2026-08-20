# dev init — Local DB / Remote Server Selection — 2026-08-20

## Context

Plans `260817-storage-backend-adapter` (local-vs-remote schema + adapter layer) and `260818-infra-up-remote-support` (remote-aware `make infra-up` / `dev doctor`) had landed, but `dev init` still wrote only the local-project schema — `storage_backend` and the `remote` section were never prompted for, so operators who wanted a remote project had to hand-edit `.cortext-harness/config/{env}.json` after init. That gap silently encouraged copy-pasting partial remote configs that `validate_backend_config()` would later reject at runtime. Plan `plans/260820-dev-init-backend-selection/plan.md` closed the loop by adding a backend selection step to the init wizard, including init-level validation against the schema introduced by 260817.

## Change

- `cortex_harness/dev.py:32` adds `validate_backend_config` to the existing `cortex_harness.storage.config` import.
- `cortex_harness/dev.py:2256` adds the `Storage backend` prompt (`local` | `remote`) right after project code / name. The default reads `existing["storage_backend"] or "local"` so re-init never silently downgrades a remote project.
- `cortex_harness/dev.py:2268` opens the `Remote backend` sub-flow only when `storage_backend == "remote"`. It prompts for `qdrant_url`, `qdrant_api_key` (`hide_input=True`), `falkordb_uri`, `falkordb_password` (`hide_input=True`), and `falkordb_ssl` (default `False`). All four string fields are stripped and reused as defaults on re-init.
- `cortex_harness/dev.py:2288` runs each candidate through `validate_backend_config()`; on `ValueError` the wizard asks `Retry remote fields?` (default `Yes`) and exits non-zero if the operator declines, leaving no partial config behind.
- `cortex_harness/dev.py:2308` splits the local-storage branch off the prompt tree: when `remote` is chosen the `CORTEX_STORAGE_INSTANCE` / `CORTEX_DATA_HOME` prompts (and the matching `storage_env`) are skipped entirely, and `cfg["remote"]` is appended only when populated.
- `cortex_harness/dev.py:2435` adds `"storage_backend"` to the top-level `cfg` and `cfg["remote"]` when remote. After `_save_config` it prints a `[info] storage_backend=remote — run 'make infra-up' or 'dev doctor' to verify connectivity` hint.
- New `tests/test_dev_init_storage_backend.py` (9 tests) covers: default local writes no `remote` key, full remote passes `validate_backend_config()`, only-Qdrant / only-FalkorDB are accepted, missing both URLs is rejected (no config written), re-init reuses prior remote defaults, re-init can downgrade remote → local, secrets never leak into `result.output`, and remote path skips the local `CORTEX_STORAGE_INSTANCE` / `CORTEX_DATA_HOME` prompts.
- Existing `tests/test_dev_init_graph_provider.py` adjusted to insert the extra prompt slot (`Storage backend`) into every `CliRunner.invoke(..., input=...)` line so the unchanged assertions still bind to the right prompt. Each of the three modified tests now also asserts `config["storage_backend"] == "local"` (and that no `remote` key leaks into local output).
- `docs/HARNESS_WORKFLOW.md:87` documents the new prompt order, the *Remote backend* sub-table, the plaintext-secret warning, and the `infra-up` / `doctor` follow-up.
- `plans/260817-storage-backend-adapter/plan.md` + `plans/260818-infra-up-remote-support/plan.md` get bidirectional `relatedPlans` entries + cross-plan notes describing how `dev init` now feeds `infra-up` / `doctor` and validating the schema that the adapter layer introduced.

## Impact

Impact level: medium. New projects now have a first-class local-vs-remote choice at init time, and existing remote projects can be re-init'd without losing their endpoints. The init flow stays additive: default output for a local project gains exactly one new key (`storage_backend: "local"`) and never carries an empty `remote` section. Remote prompts use `click.prompt(..., hide_input=True)` for secrets and rely on `RemoteStorageConfig.__repr__` redaction elsewhere (already enforced by `test_remote_credential_redaction`); `result.output` is asserted not to contain the test secret values, so a future regression that echoes an API key or password fails loudly. Init-level `validate_backend_config()` rejects empty remote sections before any file is written, so `make infra-up` and the runtime `resolve_storage()` no longer have to defend against malformed configs originating from the wizard. No change to local-only flows beyond the one extra default-accepting prompt, so existing local projects see no behavioral change.

## Decision

The prompt lives between project metadata and local-storage fields (per the plan) rather than appended at the end of the wizard, so an operator who chose `remote` never sees the `CORTEX_STORAGE_INSTANCE` / `CORTEX_DATA_HOME` prompts that would not apply to them. We deliberately reuse `validate_backend_config()` rather than duplicating the `≥ 1 of qdrant_url / falkordb_uri` rule — that keeps the init-level validator identical to the runtime one, so a config accepted at init can never be rejected later. Secrets stay in plaintext in `cfg["remote"]` for parity with the existing schema (and the 260818 plan's reachability check), and the only safeguard is the `hide_input=True` prompt + the README warning + the secret-leak assertion in `test_secrets_not_echoed_to_output`. Connectivity is not probed at init time: `dev init` writes the config, then hints at `make infra-up` / `dev doctor`; the existing remote-aware `infra-up` is the right place to fail fast on unreachable endpoints because it already reports per-check status.

## Pre-existing observation (out of scope)

`.gitignore:52` only ignores `.cortext-harness/config/dev.json`. With this plan still allowing `dev init --env prod`, a populated remote `prod.json` would not be auto-excluded. Flagged here for a follow-up plan rather than expanding scope mid-feature.

## References

- plan: `plans/260820-dev-init-backend-selection/plan.md:1`
- related plans: `plans/260817-storage-backend-adapter/plan.md:1` (schema + adapter), `plans/260818-infra-up-remote-support/plan.md:1` (lifecycle consumption)
- code: `cortex_harness/dev.py:2252`, `tests/test_dev_init_storage_backend.py:1`, `docs/HARNESS_WORKFLOW.md:87`
- tests: `tests/test_dev_init_graph_provider.py:1` (updated prompt offsets), `tests/test_dev_init_storage_backend.py:1` (9 new)
