# User-configurable ignore folders (`ignore.folders`) — 2026-09-09

## Context

Users had no way to exclude custom folders (e.g. generated porting output
`_pc2c/`) from scanning without editing code or JSON by hand. Plan
`260909-0854-ignore-folders-config` adds a user-managed ignore list layered
on top of the built-in defaults (`_SCAN_EXCLUDE` / `COMMON_SCAN_EXCLUDE` /
`_SKIP_DIRS`).

## Change

- **Config + CLI** (`cortex_harness/dev.py`): new top-level config section
  `ignore.folders`; `dev init` prompt (`cortex_harness/dev.py:2960`) with
  re-init defaults and clearable list; `dev ignore add|remove|list` command
  group (`cortex_harness/dev.py:3803`); helper `_ignore_folders`
  (`cortex_harness/dev.py:342`) tolerates malformed config.
- **Orchestrator threading**: `extra_ignores` parameter (default
  `frozenset()`, backward-compatible) through `_is_excluded_path`,
  `_discover_folders`, `_detect_langs`, `_mtime_changed_files`,
  `_find_doc_files`, `_detect_changed_docs`, `_build_file_hashes`,
  `_run_analyzer`, `_sync_folder`, `_sync_doc_folder`; sync commands export
  `CORTEX_EXTRA_IGNORE_DIRS` to spawned subprocesses via
  `_sync_extra_ignores` (`cortex_harness/dev.py:361`); scan-root/ignore
  contradiction warning (`cortex_harness/dev.py:375`).
- **Shared ignore helper** (`code-tiny/tools/common/scan_ignore.py`): cached
  `extra_ignore_dirs()` + `matches_extra_ignore()`; entry points
  `is_excluded_dir` / `has_excluded_parent` / `filter_paths` merge user
  entries; `COMMON_SCAN_EXCLUDE` frozenset never mutated.
- **All analyzer prune sites merged** (32 modules): incremental_sync (both
  walks), owner_manifest, project_topology, aspnet, android (3), ts (4),
  java, go, js, cplus, python, kotlin, php, plsql, delphi, rust, swift,
  csharp, vb, perl, shell, jp1, mybatis (2), spring (2), servlet_jsp, struts,
  flutter dart_parser.
- **doc-tiny** (`doc-tiny/graphrag_ingest_langextract.py:24`):
  `_iter_input_files` now prunes excluded directories; imports code-tiny
  scan_ignore via bounded sibling sys.path insert, falls back to a minimal
  mirror when run standalone.
- **Docs**: `docs/specs/cli.md` (config shape + semantics + env-var note),
  `ReadMe.md` (init prompt + ignore commands), `code-tiny/tools/ReadMe.md`.

## Impact

- Users configure ignores once via `dev init` / `dev ignore`; they apply to
  code sync (orchestrator + incremental_sync + all analyzers) and doc sync
  (incremental + full ingest) on the next run.
- Risk: low. With no `ignore.folders` config the behavior is byte-for-byte
  identical (regression-guarded by `test_no_config_ignore_keeps_behavior_identical`
  and the unset-env tests). Defaults can never be un-ignored.
- Review fix: flutter dart_parser initially matched the file name instead of
  folder parts — corrected to per-part matching.

## Decision

- Env-var channel (`CORTEX_EXTRA_IGNORE_DIRS`) over CLI flags: one export
  covers every subprocess depth (incremental_sync → analyzer children)
  without touching each analyzer's argv; additive-only merge keeps the
  "defaults win" invariant.
- doc-tiny cross-import with fallback mirror instead of duplicating the full
  ~90-entry list: keeps one source of truth in the normal repo layout while
  staying runnable standalone.
- `_build_file_hashes` also takes `extra_ignores` (not in the original plan)
  — otherwise ignored files stored in the hash baseline would be reported as
  deleted on the next incremental doc sync.

## Smoke (procsample, real project)

Re-ran `dev init` on `/Users/user/Migration/procsample` answering the
new prompt with `_pc2c`: config preserved (remote backend, endpoints, code
root) and gained `"ignore": {"folders": ["_pc2c"]}`. Verified on the real
tree: `dev ignore list` → `_pc2c`; env var exported; full-scan `.c` snapshot
shows 31 files with **0 leaks from `_pc2c`**; `dev sync code --dry-run`
exits 0.

## References

- plan: ./plans/260909-0854-ignore-folders-config/plan.md
- commit: d263b07
- tests: tests/test_dev_ignore.py, tests/test_dev_sync_ignore.py,
  tests/test_incremental_sync_ignore.py, doc-tiny/tests/test_ingest_input_ignore.py,
  code-tiny/tools/common/test_scan_ignore.py
