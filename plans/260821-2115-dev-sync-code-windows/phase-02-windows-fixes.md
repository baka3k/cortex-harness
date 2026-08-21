# Phase 02 — Windows platform fixes

## Objective

Remove the two Windows-only defects that turn any child failure into a masked,
confusing error — independent of remote plumbing and safe for POSIX.

## Changes

### 1. `_write_summary` directory fsync (code-tiny/tools/sync/incremental_sync.py)

Current: replace → chmod → `os.open(parent_dir, os.O_RDONLY)` + `os.fsync` inside
the `PermissionError` retry loop. On Windows the directory open raises
`PermissionError`, the retry re-runs `os.replace` on an already-renamed tmp file,
and the caller sees `[WinError 2]` instead of the true failure.

Fix:

- Restructure so `os.replace`+`os.chmod` keep the PermissionError-retry, but the
  directory fsync runs once, AFTER a successful replace, wrapped in
  `if os.name != "nt":` (POSIX keeps the durability guarantee; Windows has no
  directory-fsync API — accepted, documented in a comment).
- Keep tmp naming and `O_EXCL` semantics unchanged.

### 2. Embedding device normalization

- `cortex_harness/dev.py` `_code_env_for_process` (and `_doc_env_for_process`
  for symmetry): when the resolved `device` is `mps` but the platform is win32,
  rewrite `EMBED_DEVICE` to `cuda` when `torch.cuda.is_available()` else `cpu`,
  with a one-line `[info]` note in verbose output. macOS (`mps` on darwin) and
  explicit `cpu`/`cuda` values pass through untouched.
- Optional hardening (do only if cheap): `python_analyzer._resolve_embed_device`
  validates a requested `mps` via `torch.backends.mps.is_available()` and falls
  back to `cuda`/`cpu`. Not required for the harness-driven flow.

## Verification

- Direct unit test on Windows: `_write_summary` round-trips a payload with no
  exception; on POSIX the dir-fsync branch still executes (mock-guarded).
- `EMBED_DEVICE` env assertion: win32+mps→cpu (no cuda on this box), darwin
  passthrough asserted by platform-gated test.
