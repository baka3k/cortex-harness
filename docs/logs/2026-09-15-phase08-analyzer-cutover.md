# Phase-08 analyzer-layer cutover — 2026-09-15

## Context

Plan `plans/260915-analyzer-layer-rust-cutover/phase-08.md` is the
final phase of the analyzer-layer Rust migration. It gates the umbrella
plan `260913-2130-rust-full-migration`'s Wave-D by flipping
`CORTEX_RUST_ANALYZER` to default-Rust, then deleting the Python analyzer
entry points (~84,780 LOC). User chose "follow plan literally" (overrides
the phase-06 carve-out's "narrow delete to shared-7 lineage").

## Change

### Rust side (`cortex-sync`)

- `rust/crates/cortex-sync/src/registry.rs:354` — `AUTO_FLIP_DEFAULT: bool = true`
  (was `false`); new `retire_hint()` bakes the cutover commit SHA into
  every retire message; `rust_analyzer_binary()` now raises a hard
  `Err(_RetiredError)` for any non-Rust value or missing-binary-when-unset.
- `rust/crates/cortex-sync/build.rs` (new) — `CORTEX_BUILD_COMMIT` →
  `cargo:rustc-env`, surfaced in retire errors.
- `rust/crates/cortex-sync/src/orchestrator.rs:1974` — embedding-pass
  `force_python` pin removed (Python children were retired); legacy 17
  parsers now report `vectors=0 vector_status=disabled` (acknowledged
  regression, see plan §3).
- `rust/crates/cortex-sync/src/registry_tests.rs` — rewritten for
  phase-08 semantics (unset + binary-present → Rust; unset + missing →
  retire; `python`/other → retire; `rust` + missing → hard error + build
  hint); 11 flip-matrix cells × 8 conditions.
- `cargo test --release -p cortex-sync --lib` — 68 passed, 0 failed.
- `cargo clippy --release -p cortex-sync --all-targets -- -D warnings` —
  clean. `cargo check --workspace --release` — clean.

### Python side

- `code-tiny/tools/sync/incremental_sync.py:1442` — new `_RetiredError`
  mirrors `cortex-sync` registry; `_rust_analyzer_binary` raises instead
  of returning `None`.
- `cortex_harness/sync_processes.py:83` — comment-only update (the
  `_analyzer.py` filter is now a no-op).
- `cortex_harness/_phase08_skip.py` (new, +200) — module-stub installer
  + `@phase08_retired` decorator; stub `_SkipSentinel` raises
  `unittest.SkipTest` with the phase-08 reason on every attribute access /
  call / context-manager entry.
- `conftest.py` (new) — `install_stubs()` at pytest collection time so
  tests depending on retired modules skip loudly instead of crashing.

### Deletion (~84,780 LOC, 184 files)

- 24 primary analyzer entry points: cobol, dart (entry was in flutter/),
  cplus (entry), delphi, java, kotlin, android, vb×4, python, go, perl,
  shell, jp1, rust, swift, js, ts, php, csharp (entry + adapter), sql,
  plsql
- 12 overlays: spring, servlet_jsp, mybatis, struts, aspnet_core,
  aspnet_framework, web_framework (3 entries share one file),
  database_schema (2 entries share one file), flutter (whole dir)
- topology_analyzer.py
- Parser-specific Python support modules orphaned by the entries
  (cobol/{cfg,models,parser,parser_runtime,pipeline,qdrant,resolver,semantics}.py,
  jp1/{models,parser,pipeline}.py, perl/{models,parser_runtime,perl_parser,pipeline,resolver}.py,
  shell/{mapping,models,parser,pipeline}.py, mybatis/*, spring/*,
  servlet_jsp/*, struts/*, aspnet_*/{artifact_parsers,detector,pipeline,resolver}.py,
  web_framework/{models,pipeline}.py, database_schema/{models,pipeline}.py,
  android/*, ts/{agents/*,pipeline/*,types/*,utils/*,context/*}, plus more)
- `code-tiny/run_migration.py` (one-shot helper for already-archived
  analyzers)

### Kept (per retention list)

- `tools/common/`, `tools/sync/` (delegation target), `tools/graph/`
- `tools/cplus/` clang plane (clang_parser, semantic_worker, etc. —
  `cplus_analyzer.py` entry is the exception, deleted)
- `tools/csharp/{models.py, roslyn_integration.py, roslyn_worker/,
  framework_items/, CLAUDE.md}`
- `tools/vb/roslyn_worker/`, `tools/vb/README.md`, `tools/vb/vb_path_classifier.py`
- `tools/jp1/{README.md, sniff.py}`
- `tools/ts/{ts_analyzer.py, ts_project_detector.py}` (common
  `TYPE_CHECKING` import + sync import — restored post-cutover)
- `tools/project_topology/{contracts,detector,models,parsers/,pipeline,
  registry,resolver}.py` (graph writer dependency — restored post-cutover)
- `scripts/rust_parity/` (with new archive header: "PY side archived,
  fixtures = golden")
- `cortex_harness/dev.py` dicts (parity-reference)

### Skill / docs updates

- `code-tiny/skills/code-graph-ingest/SKILL.md` + `references/{analyzers,
  examples}.md` — rewritten to point at the `analyzer-<lang>` Rust
  binaries instead of the deleted `python tools/<lang>/<lang>_analyzer.py`
  invocations.
- `scripts/rust_parity/*.py` (71 files) — phase-08 archive notice header
  injected.

### Test disposition

- 24 test files patched: try/except ImportError wraps every retired
  import + `@phase08_retired` decorator on each TestCase class.
- `tests/test_incremental_sync_cobol.py`, `tests/test_common_analyzer_registry.py`,
  `code-tiny/tests/test_analyzer_provider_wiring.py` — three explicit
  methods reclassified as documented loud-skip (the Python-backend
  assertion, the script_path is_file() check, and the analyzer
  provider-wiring check).
- `pytest tests/ code-tiny/tests/`: **1446 passed, 184 skipped (loud),
  70 documented failures** (parity scripts that invoke the Python
  side; cobol runtime tests that need the bundled `.so`; archive-
  behaviour assertions). No silent `importorskip` — every skip carries
  the phase-08 reason in the pytest report.

## Impact

- **All sync orchestrator entry points** (`cortex-sync`, `dev sync code`)
  now route through Rust binaries by default. Default flip is irreversible
  without `git revert <cutover-sha>` (the SHA is in every retire message).
- **Operators using `CORTEX_RUST_ANALYZER=python`** (the rollback flag
  from phase-01..07) now get a hard "retired" error instead of a silent
  fallback. Any deployment still relying on `=python` for a specific
  reason must surface that dependency before the deploy.
- **Embedding for legacy 17 parsers** (cobol, delphi, java, kotlin,
  android, vb×4, python, js, ts, php, sql, plsql, cplus, csharp) reports
  `vectors=0 vector_status=disabled` until each parser's embedding-input
  artifact emission is ported to the Rust binary (follow-up plan
  `plans/260915-analyzer-layer-rust-cutover/reports/phase08-followup-legacy17-embedding.md`).
- **Test surface** for the retired Python code is preserved as 184
  loud-skips so the pytest report always names the regression.
- **Risk level: HIGH** (one commit, ~84k LOC deletion, flip + delete
  coupled). Mitigated by retire messages naming the cutover SHA + the
  documented `git revert` rollback path.

## Decision

- **Follow plan literally** (user chose over the carve-out): the plan's
  literal delete list includes the legacy 17 parsers even though
  phase-06 marked them as carve-out candidates. The user accepted the
  acknowledged "embedding regression for 17 parsers" risk.
- **One final commit** rather than staged: matches the plan's
  "1 commit cuối" structure (red-team finding 3) and the umbrella's
  expected ops gate sequence (dogfood + rollback drill run after
  the cutover).
- **Coupling the embed pin removal with the delete**: the
  `force_python=true` pin in `orchestrator.rs` would have crashed every
  sync with a missing-binary error post-cutover, since the Python
  children it pinned to were deleted. Removed the pin and let the
  embedding pass fall through to the Rust child (which reports
  `vectors=0` for the legacy 17 lineage).
- **Build.rs for the retire SHA**: avoids hard-coding the cutover
  commit in source; CI / operators can pin `CORTEX_BUILD_COMMIT=`
  explicitly or rely on the `git rev-parse` fallback.

## References

- plan: `./plans/260915-analyzer-layer-rust-cutover/plan.md`,
  `./plans/260915-analyzer-layer-rust-cutover/phase-08.md`,
  `./plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md`
- commits: `99e9092` (cutover), `0e49def` (post-commit stub fix)
- umbrella: `./plans/260913-2130-rust-full-migration/plan.md`
- carve-out (overridden): `plans/260915-analyzer-layer-rust-cutover/phase-06.md:11-16`
- red-team finding 3: `plans/260915-analyzer-layer-rust-cutover/reports/red-team-rev1.md`
- retention list: `plans/260915-analyzer-layer-rust-cutover/phase-08.md` §3
- pending follow-up: `plans/260915-analyzer-layer-rust-cutover/reports/phase08-followup-legacy17-embedding.md` (TODO)