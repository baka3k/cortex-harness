# Phase 04 — Tests and regression guards

## Objective

Lock in both the Windows fix and the macOS no-change guarantee.

## Test additions (extend existing suites, no new harness)

1. **Remote overlay** — `tests/test_storage_config.py`:
   - `storage_overlay` with `backend_mode=REMOTE` + `falkordb_uri` → contains
     `FALKORDB_URI`, excludes `FALKORDB_PATH/FALKORDB_CODE_PATH/FALKORDB_DOC_PATH`.
   - Local mode → overlay output identical to a golden dict captured before the
     change (regression guard for macOS).
2. **dev.py arg building** — `tests/test_dev_sync_reliability.py` (or a new
   `test_dev_sync_remote_args.py` next to it):
   - `_neo4j_args_code` / `_env_to_neo4j_args`: `FALKORDB_URI` env →
     `--falkordb-uri` args, no `--falkordb-path`; without URI → unchanged list.
   - `_storage_env_for_process` picks up top-level `storage_backend`/`remote`.
   - `_pause_mcp_for_sync`: remote env (no `FALKORDB_PATH`) does not stop any
     process (mock `_mcp_pids` and assert not called).
3. **incremental_sync CLI/env** — `tests/test_incremental_sync_graph_setup.py`:
   - `parse_args` accepts `--falkordb-uri/--falkordb-password/--falkordb-ssl`;
   - `_build_analyzer_env` propagates URI keys and omits `FALKORDB_PATH`;
   - FalkorDB config builders emit `uri` config with `path=None` when URI set.
4. **graph cli helper** — code-tiny tests (`tools/graph` suite):
   - `prepare_graph_args` + `create_graph_driver_from_args` with URI env/args →
     driver config carries `uri`, no local-path synthesis;
   - `create_from_env` prefers `FALKORDB_URI`.
5. **Summary write** — `tests/test_incremental_sync_result_contract.py`:
   - `_write_summary` on win32 (and dir-fsync mocked PermissionError on POSIX)
     succeeds and does not re-rename; target file contains valid JSON.
6. **Device normalization** — win32+`device: mps` → `cpu` (or `cuda` when
   available); darwin+mps passthrough (platform-gated/skipif).

## Execution matrix

- Windows (this machine): full touched-suite `pytest` green + Phase 03 E2E.
- macOS: CI or next session runs the same suites; local-mode goldens prove no
  behavioral delta (no macOS box is available from this session — tracked as an
  explicit follow-up checklist item, not a silent assumption).

## Documentation

- Update `ReadMe.md` / `INSTALLER_GUIDE.md` Windows quick-start section:
  `dev infra-up` + `storage_backend: remote (local Docker)` + `dev sync code`.
- Note in `docs/DATABASE_INTEGRATION.md`: Windows = remote-URI mode against a
  local container; embedded FalkorDBLite is POSIX-only.
