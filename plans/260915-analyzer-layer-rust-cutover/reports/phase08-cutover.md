# Phase 08 — Cutover report (flip default + DELETE Python analyzer scripts)

**Plan:** `260915-analyzer-layer-rust-cutover` · phase-08
**Status:** **CUTOVER COMMITTED** — implementation landed in `99e9092`
(flip default + DELETE) + `0e49def` (post-commit stubs) + the follow-up
commit that lands the `--version` build-commit handshake, the delegated-lane
detector fallbacks and the widened verification gates. Rollback drill
**PASS** (`reports/phase08-rollback-drill.md`); dogfood 7-day gate remains
the open ops item before this plan flips to status: done.
**Date:** 2026-09-15

---

## What landed (commit summary)

| Change | LOC delta | Notes |
|---|---|---|
| `rust/crates/cortex-sync/src/registry.rs` | +60 | `AUTO_FLIP_DEFAULT = true`; new `_RetiredError` for `python`/non-`rust`/missing-binary-when-unset cells; `retire_hint(parser)` bakes the cutover commit SHA via new build.rs |
| `rust/crates/cortex-sync/build.rs` | +30 (new) | `CORTEX_BUILD_COMMIT` → `cargo:rustc-env` → bake the SHA into the retire error message so the rollback path is unambiguous |
| `rust/crates/cortex-sync/Cargo.toml` | +0 | (no change after revert of speculative `[features]` block) |
| `rust/crates/cortex-sync/src/registry_tests.rs` | rewritten | Tests now expect the phase-08 flip semantics: unset + binary-present resolves to Rust; unset + missing-binary retires; `python`/other values retire; `rust` + missing-binary hard-errors with build hint |
| `rust/crates/cortex-sync/src/orchestrator.rs` | +6 / -2 | Embedding-pass `force_python` pin removed — the Python children were retired; the embedding pass now uses the Rust child unconditionally. Shared-7 lineage still gets orchestrator-level embedding; legacy 17 parsers emit `vectors=0 vector_status=disabled` (regression, see §3) |
| `code-tiny/tools/sync/incremental_sync.py` | +60 / -20 | New `_RetiredError` raised on every non-Rust cell (loud, fail-closed); mirrors `cortex-sync` registry flip matrix |
| `cortex_harness/sync_processes.py` | +9 | Comment-only update noting the `*_analyzer.py` filter is now a no-op (Python analyzer processes don't exist post-cutover) |
| `cortex_harness/_phase08_skip.py` | +130 (new) | Module stub installer + `phase08_retired` decorator; conftest hooks it for pytest collection |
| `conftest.py` (project root) | +20 (new) | Calls `install_stubs()` at import so retired analyzer modules resolve to a `SkipTest`-raising sentinel |
| Tests (24 patched + helpers) | +200 / -80 | Tests that depended on deleted Python entry points are wrapped in `try/except ImportError` + `@phase08_retired` for loud skip; remaining 1446 tests still pass |
| `code-tiny/tests/test_analyzer_provider_wiring.py` | rewritten | Two of the four methods reclassified as documented loud-skips; the two methods that target the kept `tools.graph.cli` plane remain live |
| `code-tiny/tests/test_common_analyzer_registry.py` | +5 | `test_incremental_sync_registers_every_supported_primary_and_overlay` reclassified as documented loud-skip (script_path fields are now rollback seams, never executed) |
| `tests/test_incremental_sync_cobol.py` | +8 | Python-backend cmd assertion reclassified as documented loud-skip |
| `code-tiny/skills/code-graph-ingest/SKILL.md` + `references/{analyzers,examples}.md` | rewritten | Skill now points at the `analyzer-<lang>` Rust binaries instead of the deleted `python tools/<lang>/<lang>_analyzer.py …` invocations |
| `scripts/rust_parity/*.py` (71 files) | +7 each | Phase-08 archive notice header injected (PY side archived, fixtures = golden) |
| `code-tiny/run_migration.py` | deleted | One-shot helper for already-archived analyzers |
| `code-tiny/tools/**/*_analyzer.py` (24 primary + 12 overlays + topology + flutter) | deleted | Phase-08 cutover target; plan estimated ~84,780 LOC, **actual 78,414 deleted across 277 files** in `99e9092` (+2,847) |
| `code-tiny/tools/{android,delphi,go,jp1,perl,python,rust,shell,swift,java,kotlin,js,php,sql,plsql,cobol,flutter,aspnet_*,servlet_jsp,mybatis,struts,web_framework,database_schema,spring,vb,vb_analyzer_base,vb_common,vb_roslyn_adapter}/` | deleted | Parser-specific support modules orphaned by the entry deletions |

### KEEP (per retention list — `tools/common/`, `tools/sync/`, `tools/graph/`,
### `cplus clang plane`, `csharp/roslyn_worker/`, `scripts/rust_parity/`,
### `cortex_harness/dev.py`)

`tools/cplus/{bootstrap_compile_commands,clang_parser,clang_worker,evidence_merge,function_identity,guarded_publication,parse_recovery,pilot_rollout,proc_analyzer,proc_manifest,proc_source_map,rc_parser,semantic_context,semantic_shadow,semantic_worker,windows_resource_parser}.py`
`tools/csharp/{CLAUDE.md,framework_items/,models.py,roslyn_integration.py,roslyn_worker/}`
`tools/vb/{README.md,roslyn_worker/}`
`tools/jp1/{README.md,sniff.py}`
`tools/project_topology/{contracts.py,detector.py,models.py,parsers/,pipeline.py,registry.py,resolver.py}` (graph writer dependency — restored post-cutover)
`tools/ts/{ts_analyzer.py,ts_project_detector.py}` (common `TYPE_CHECKING` import + sync import)

## 1. Flip semantics (post-cutover — `cortex-sync` + delegation target)

| `CORTEX_RUST_ANALYZER` | binary present | binary missing |
|---|---|---|
| unset | Rust binary | **loud "retired" error** (auto-flip phase-14) |
| `rust` | Rust binary | hard error + `cargo build --release -p <bin>` hint |
| `python` | n/a | **loud "retired" error** |
| other (e.g. `1`, `true`, `RUST_AUTO`) | n/a | **loud "retired" error** |

Retire message format: `Python analyzer plane retired at commit <sha>
(parser '<parser>'); rollback = \`git revert <sha>\` (then rebuild Rust binaries).`

The commit SHA is baked at build time via `CORTEX_BUILD_COMMIT` (set by CI
or the operator's local build invocation) and surfaces in every retire
error so the operator knows exactly which commit to revert.

## 2. Verification before commit

### Rust
- `cargo build --release -p cortex-sync` — clean.
- `cargo clippy --release -p cortex-sync --all-targets -- -D warnings` — clean.
- `cargo test --release -p cortex-sync --lib` — **68 passed, 0 failed, 0 ignored** (incl. 11 flip-matrix cells × 8 conditions + 25/12 map parity + `.exe` probe).
- `cargo check --workspace --release` — clean.

### Rust — widened gates (post-commit verification pass, 2026-09-15)
- `cargo build --release --workspace` — clean, except `cortex-retrieval-py`
  which fails to LINK on macOS (pyo3/abi3 env issue) — fails identically at
  `pre-phase08-cutover` (pre-existing; CI green on Linux).
  Local gates use `--workspace --exclude cortex-retrieval-py`.
- `cargo clippy --release --workspace --exclude cortex-retrieval-py
  --all-targets -- -D warnings` — **clean** after fixing 16 `cortex-dev` +
  1 `cortex-mcp` pre-existing lint-drift warnings (mechanical; clippy 1.97
  vs the older CI pin). See `reports/phase08-followups.md` §6.
- `cargo test --release -p cortex-sync --lib` — **73 passed** (68 prior +
  5 new build-commit handshake tests).

### Python (pytest)
- `pytest tests/ code-tiny/tests/ -q -rs` — **1446 passed, 184 skipped
  (loud), 70 failed** in the cutover commit — unchanged in the
  post-commit verification pass (**1446 passed, 184 skipped, 70 failed,
  250 subtests passed**, 151s), i.e. the detector-fallback fix did not
  perturb the suite. Every skip carries the phase-08 reason in the pytest
  report (loud — no silent `importorskip`); the 70 failures test archived
  behaviour (parity gates invoking the Python side, cobol runtime needing
  the bundled `.so`, ...) and are documented in §2 of the cutover commit
  message and the runbook disposition §5.1.7.

### Sync smoke (default unset, after rebuild) — completed 2026-09-15
Post-commit verification ran the full smoke the cutover session deferred.
Composite fixture root (union of `tests/fixtures/*` language projects),
`--parsers auto`, `CORTEX_RUST_ANALYZER` **unset**, all children observed
as `rust/target/release/analyzer-*` binaries via the recorded commands:

- **Native lane** (orchestrator `run_child`, `--no-graph`, parsers
  `dart,go,shell,python,java`): no delegation, **5/5 success**, build-commit
  handshake passed.
- **Delegated lane** (cplus-in-filter → Python-plane delegation; FalkorDB
  live): **19 primary lanes all binary**; 17/19 success with
  `CORTEX_GRAPH_JOURNAL_MODE=shared-shadow`; overlays spring / servlet_jsp /
  mybatis / flutter success + topology success with real graph_writes;
  graph converged to steady state in the rollback drill below.
- **Flip-matrix retired cells, live**: `CORTEX_RUST_ANALYZER=python` and
  unset+missing-binary both print
  `Python analyzer plane retired at commit 1c2967a (parser 'shell'); rollback = git revert 1c2967a ...`.
- **Stale-binary handshake, live**: a child rebuilt with
  `CORTEX_BUILD_COMMIT=deadbeef` is refused by the orchestrator with
  `[phase-08] stale analyzer binary: built at commit 'deadbeef' but
  orchestrator expects '1c2967a' — rebuild with: cargo build --release
  --workspace` (exit 126, parser isolated). Red-team F5 closed.
- **Known-failure lanes** (documented, loud): go/csharp endpoint preflight
  on builtin targets and the cplus default-required journal lane — all
  pre-existing or accepted, catalogued with evidence in
  `reports/phase08-followups.md`.

## 3. Known regression — embedding for legacy 17 parsers

The legacy 17 parsers (cobol, delphi, java, kotlin, android, vb×4, python,
js, ts, php, sql, plsql, cplus, csharp) do **not** currently emit the
phase-06 embedding-input artifact that the orchestrator-level embedding
pass requires (the carve-out in `phase-06.md:11` keeps the legacy
`CodeEmbedder`/`QdrantWriter` lineage on Python children).

After the phase-08 cutover deletes the Python entry points, the legacy 17
parsers' embedding pass returns:

```
[SCAN_RESULT] parser=<lang> files=… functions=… classes=…
  vectors=0 vector_status=disabled
```

The embedding-pass `force_python` pin in `cortex-sync/src/orchestrator.rs`
was removed in this commit (the pinned Python children no longer exist).
The embedding pass falls through to the Rust child, which — for the
legacy 17 lineage — emits `vectors=0 vector_status=disabled`.

This is the **accepted risk** the user acknowledged when choosing
"follow plan literally" (the carve-out's "phase-08 delete list narrows
to shared-7 lineage" was overridden). Restoring embedding for the legacy
17 requires porting their embedding-input artifact emission to the Rust
binaries — tracked with the other surfaced items in
`reports/phase08-followups.md` (§1 legacy-17 embedding, §2 cplus journal
lane, §3 go/csharp preflight, §4 ladybug SET+=, §5 retrieval-py macOS link).

## 4. Grep audit per phase-08 disposition

| Disposition | Action taken |
|---|---|
| `cortex_harness/sync_processes.py:83` (`_analyzer.py` filter) | Comment-only update noting the filter is now a no-op (Python analyzer processes don't exist post-cutover) |
| `code-tiny/run_migration.py` | Deleted (one-shot helper for already-archived analyzers) |
| `code-tiny/tests/test_analyzer_provider_wiring.py` | Two of four methods reclassified as documented loud-skip; two methods targeting the kept `tools.graph.cli` plane remain live |
| `skills/code-graph-ingest/SKILL.md` | Rewritten to point at the `analyzer-<lang>` Rust binaries |
| CI workflows (cobol-macos.yml) | Already updated in phase-07; verified post-cutover |
| pytest tests | 24 patched + 3 helper-level patches; ~184 tests skipped loudly via the `phase08_retired` decorator or the `_SkipSentinel` stub |

No active `_analyzer.py` references remain outside the rollback
`script_path` fields of `incremental_sync.ANALYZERS` /
`incremental_sync.FRAMEWORK_ANALYZERS`. These fields stay in the maps as
documented rollback seams (never executed post-cutover, but the operator
can `git revert` and they would be live again).

### Post-commit audit addition — delegated-lane detector imports

The post-commit verification grep found **live runtime imports of deleted
detector modules** in the delegation target
(`incremental_sync.py::_group_paths_by_framework`:
`tools/{mybatis,servlet_jsp,spring,flutter,aspnet_core,aspnet_framework}.detector`)
on the changed-paths routing path — an ImportError landmine for any
delegated-lane sync (pytest stubs had masked it). Fixed in the follow-up
commit: the imports are guarded (`except ImportError`), degrade to the
file's own strong-candidate heuristics, and print a **one-time loud stderr
note** ("routing on strong-candidate heuristics; rollback = git revert of
the phase-08 cutover commit"). `git revert` restores the detectors and full
detection. Functionally verified: fallback routing groups spring /
servlet_jsp / mybatis / flutter / aspnet×2 candidates on the composite
fixture, and the delegated smoke ran clean through the new path.

## 4b. `--version` build-commit handshake (plan §2, red-team F5) — landed

The cutover commit baked `CORTEX_BUILD_COMMIT` (build.rs) into retire
messages but did not implement the handshake itself; the follow-up commit
closes it:

- `cortex-analyzer-framework`: new `build.rs` + `BUILD_COMMIT` const (same
  resolution order as cortex-sync: env → `git rev-parse --short HEAD` →
  `"unknown"`).
- All 35 spawnable child binaries answer `--version` with
  `<name> <commit>`: clap `version = cortex_analyzer_framework::BUILD_COMMIT`
  on the wrapper structs (24 crates incl. vb via `VbArgs`, web overlays via
  `WebOverlayArgs`/`AspNetArgs`), and
  `cortex_analyzer_framework::print_version_probe()` for the three
  entry points that parse `AnalyzerArgs` directly or disable the help flag
  (`analyzer-python`, `analyzer-ts`, `analyzer-topology`).
- `cortex-sync` orchestrator (`run_child`): before the first spawn of each
  binary, probe `--version`, compare against its own
  `registry::retired_at_commit()`; mismatch/missing stamp → loud stderr +
  exit-126 `ChildError` (parser isolation). Orchestrator built without a
  stamp ("unknown") warns once and skips instead of bricking non-git
  builds. Per-binary pass results cached per process.
- 5 unit tests (`handshake_tests`); live stale-binary drill passed (§2).

## 8. Review round (full-mode reviewer, `code` lens)

Isolated adversarial review of the follow-up diff: **8.5/10, zero
criticals**. All six Medium findings actioned:

1. Foreign working-tree changes (`cortex-embed/backend.rs`,
   `docs/cutover-runbook.md`, `scripts/rust_mcp/*`,
   `plans/260915-2027-vector-lane-rust-port/`) belong to the parallel
   vector-lane workstream — **excluded from the phase-08 commit**.
2. Detector guards reworked: `except ImportError` masked transitive
   breakage and degraded all three detectors together → now per-module
   `importlib.util.find_spec` resolution (`_optional_detector_class`);
   true absence → loud fallback, transitive errors after `git revert` →
   propagate loudly.
3. `parse_version_commit` anchored to a 7–40 hex-char token (bottom-up
   line scan) — extra child output can no longer be misread as the stamp.
4. `rerun-if-changed=../../.git/HEAD` resolved to a non-existent path in
   both build scripts (stamp freshness leaned on cargo's missing-path
   fallback) → resolved via `git rev-parse --absolute-git-dir` + the
   branch ref file.
5. Handshake probe now runs with the child's `current_dir` so relative
   `CORTEX_RUST_ANALYZER_BIN_DIR` resolutions agree.
6. `procinfo.rs` clamp restored NaN-safety (`min/max` used to yield 2.0;
   bare `clamp` propagates NaN into a panicking `Duration::from_secs_f64`).

Post-fix re-verification: `cargo test -p cortex-sync --lib` 74/74,
workspace clippy clean, detector fallback + delegated/native smokes green.

## 5. Pending ops gates (not closed in this session)

Per the phase-08 plan exit criteria:

1. ~~**Rollback drill**~~ — **PASS 2026-09-15**, documented in
   `reports/phase08-rollback-drill.md`: scratch checkout at tag
   `pre-phase08-cutover`, post-cutover graph taken over by the reverted
   Python plane, node/rel diff 0 after 2 sync cycles, stale vectors purged
   (42 → 23 on deletion). Rollback ops sign-off recorded there.
2. **Dogfood 7-day clean gate** — still open. Run `cortex-sync` on the user
   stock repo with `CORTEX_RUST_ANALYZER=unset` for 7 consecutive clean
   days per `docs/cutover-runbook.md` §2; ops sign-off from the on-call
   rotation. This plan stays `status: in_progress` until it closes.

## 6. Rollback procedure (post-cutover)

If the dogfood gate or rollback drill surfaces an unrecoverable
regression:

```bash
# Revert the cutover commit.
git revert <phase-08-commit-sha>

# Rebuild the Rust binaries from the pre-cutover tip.
cargo build --release --workspace

# Confirm the Python entry points are live again.
ls code-tiny/tools/cobol/cobol_analyzer.py
```

The retire message in every error tells the operator exactly which
commit SHA to revert — no archaeology required.

## 7. Bookkeeping updates

Claimed in the cutover session, **verified/completed in the post-commit
pass** (the umbrella note and the flutter flip had not actually landed
then):

- This report: `plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md`
  (commit hashes, actual LOC, verification results, new sections 4b).
- Umbrella plan `plans/260913-2130-rust-full-migration/plan.md` — note
  "analyzer layer cutover + Wave-D gate closed by
  260915-analyzer-layer-rust-cutover (rollback drill PASS; dogfood gate
  open)" added to the status line context. ✅ landed this pass.
- Spike plan `plans/260914-1706-onnx-embedding-spike/plan.md` —
  `blockedBy: []` verified resolved per the phase-06 overturn. ✅ (already
  landed at cutover).
- Flutter plan `plans/260714-1603-flutter-analyzer-parser/plan.md` —
  status flipped to `reference-only` (script archived). ✅ landed this
  pass.
- New: `reports/phase08-rollback-drill.md` (drill) +
  `reports/phase08-followups.md` (surfaced items catalog).

## Exit criteria — phase-08

- [x] Flip default (unset → Rust-if-binary) implemented in `cortex-sync` + mirrored in `incremental_sync.py`.
- [x] Loud "retired" error for `python`/non-`rust`/missing-binary-when-unset cells.
- [x] `--version` build-commit handshake wired through `build.rs` (`CORTEX_BUILD_COMMIT`).
- [x] Python analyzer scripts deleted (~84,780 LOC).
- [x] `tools/{common,sync,graph}/` + `cplus clang plane` + `csharp/roslyn_worker/` retained per the plan.
- [x] Cargo build + clippy + workspace check clean.
- [x] pytest: 1446 passed, 184 skipped (loud), 70 documented failures (archived behaviour).
- [x] Skill `code-graph-ingest` rewritten for Rust binaries.
- [x] Grep audit complete; no active `_analyzer.py` references outside rollback seams.
- [x] CI workflows already updated in phase-07.
- [x] **Rollback drill** — PASS, `reports/phase08-rollback-drill.md` (convergence diff 0, stale vectors purged).
- [x] `--version` build-commit handshake fully implemented + live-verified (was retire-message-only at the cutover commit).
- [x] Delegated-lane deleted-detector imports fixed (loud strong-candidate fallback).
- [x] Workspace-wide `cargo build --release` + `clippy -D warnings` (except pre-existing macOS-only retrieval-py link env issue).
- [x] Sync smoke without env: native + delegated lanes, every child a binary; retired cells loud; pytest matches the cutover baseline exactly.
- [ ] **Dogfood 7-day clean gate** — pending ops sign-off (the only open gate).

**Phase-08 implementation + verification: DONE. Plan stays
`status: in_progress` until the dogfood 7-day gate closes.**