# Phase 09 — Incremental-Sync Orchestrator Parity (`cortex-sync`)

**Date:** 2026-09-14
**Scope:** port the MAIN-LINE flow of `code-tiny/tools/sync/incremental_sync.py` (3,978 lines) to a Rust binary
`cortex-sync` (`rust/crates/cortex-sync/`), plus a mixed-corpus parity gate.

**Result: ALL GATES PASS** (final run: `rust/target/release/cortex-sync`, exit 0, `all_pass=True`).

---

## 1. What was built

| File | Purpose |
|---|---|
| `rust/crates/cortex-sync/` | Binary `cortex-sync` — orchestrator port (13 modules: cli, registry, gitdiff, inventory, state, syncscope, walk, routing, tsdetect, frameworks, journalenv, graphops, orchestrator, util) |
| `scripts/rust_parity/sync_orchestrator_parity.py` | Parity gate: mixed corpus (ts-analyzer + shell-application fixtures + 5 stock .py files), git 2-commit history, 12 orchestrator runs (Python reference vs Rust), summary/log/manifest/graph comparison |

Reused (as dependencies, not modified): `cortex-graph-core` (canonical JSON, RunMetadata, schema fingerprint),
`cortex-graph-writer` (FalkorDbStore, schema preflight `ensure_schema`), `cortex-falkordb` (RESP client).

## 2. Parity gates — numbers

Corpus: `tests/fixtures/ts-analyzer` (7 TS files) + `tests/fixtures/shell-application` (4 files) + 5 stock
`.py` files, `git init`, commit 1; commit 2 modifies `stock/module_1.py`, adds `scripts/new_entry.sh`,
deletes `ts-app/src/store/counters.ts`. Runs use `--parsers python,shell,ts --config /dev/null` (no qdrant),
sanitized env with `FALKORDB_URI=127.0.0.1:6379`, per-backend cache dirs and graphs
(`p09_sync_py` / `p09_sync_rs` via `FALKORDB_GRAPH`).

| Gate | Check | Result |
|---|---|---|
| (a) | `[SCAN_RESULT]` stdout lines byte-identical, run 1 (full): 3 == 3 | **PASS** |
| (a) | `[SCAN_RESULT]` stdout lines byte-identical, run 2 (incremental): 3 == 3 | **PASS** |
| (b) | Summary JSON equality after masking, run 1: 0 diffs | **PASS** |
| (b) | Summary JSON equality after masking, run 2: 0 diffs | **PASS** |
| (c) | Changed manifests equal across backends (3 parsers x 2 runs); run 2 changed sets == commit-2 diff (`python=[stock/module_1.py]`, `shell=[scripts/new_entry.sh]`); deleted manifests equal and match (`ts=[ts-app/src/store/counters.ts]`) | **PASS** |
| (d) | FalkorDB graph diff `p09_sync_py` vs `p09_sync_rs` after run 2: nodes 94 == 94, edges 126 == 126, **diff_total = 0** outside the `dual_write_diff` mask | **PASS** |
| (e) | Change-detection matrix: `committed` and `hash` incremental runs over the same git state — changed sets match Python per mode per parser; summary diffs 0 | **PASS** |
| — | Exit codes 0 on all 12 happy-path runs (both backends) | **PASS** |

Run-2 evidence (both backends identical): `impact_expansion_used=True`, `expanded_impacted=1`,
`diff changed=2 deleted=1`, `change_detection_effective=hybrid`, `bootstrap_full_scan=False`,
`outcome=scanned`, vector status `disabled` (no qdrant ⇒ embedding pass skipped, exactly like Python).

Masked in comparison (volatile by design, identical semantics both backends):
`run_id`, `correlation_id`, `started_at`, `finished_at`, `duration_seconds`, `wait_seconds`,
`updated_at`, `lock.owner` (pid/time diagnostics), and the `artifact_token` suffix `{snapshot12}_{pid}_{uuid8}`
which both orchestrators embed in generated manifest/journal/parse-quality file names.

## 3. Ported main-line surface

1. **CLI**: full `parse_args` surface (`--root --config --project-id --project-name --project-code
   --before-sha --after-sha --parsers --python-bin --cache-dir --change-detection --lock-timeout-seconds
   --reconcile --submodules --ignore-cache --strict --summary-path --reliability-mode --allow-full-fallback
   --neo4j-* --graph-provider --ladybug-* --falkordb-* --no-graph --qdrant-url --embed-* --sync-messages
   --message-* --full-scan --sync-mode --parse-quality* --verbose`) with the same env-var defaults and
   post-parse validations.
2. **Analyzer registry**: `ANALYZERS` (24), `FRAMEWORK_ANALYZERS` (12, sorted by `order`), `PROJECT_TOPOLOGY_ANALYZER`,
   parser iteration order, `_RUST_ANALYZER_BINARIES` swap rule (`CORTEX_RUST_ANALYZER=rust` +
   `CORTEX_RUST_ANALYZER_BIN_DIR`, default `rust/target/release`, Python fallback when the binary is missing),
   `_build_analyzer_cmd` (byte-equal child command lines verified by gate b/c), `_code_collection_name`,
   `_message_collection_name`, `_selected_parsers`, TS frontend/backend dynamic resolution via the ported
   `ts_project_detector` (same `[ts-detect]` line).
3. **Change detection**: hybrid/committed/hash via the ported `git_diff.py` (`--name-status -z
   --find-renames`, worktree staged/unstaged/untracked, submodule scope discovery), SHA-256 source
   inventory with mtime short-circuit, snapshot ids, baseline generation files under
   `.cache/incremental_sync` + `.cache/inventories`, bootstrap/recovery full-scan decisions,
   manifests `{"files": [...]}` under `.cache/incremental_sync_manifests/<scope>/` written before each child.
4. **Subprocess pass**: streaming `_run` ([SCAN_RESULT] capture, tails), `_build_analyzer_env`,
   `_graph_phase_env` (empty-vector sentinels), `_embedding_phase_env`, impact-expansion Cypher queries,
   topology bootstrap probe, Project/Repository setup + `ensure_schema` preflight, framework overlay loop,
   topology overlay loop, embedding pass (Plan B: the orchestrator launches the same analyzer subprocesses;
   the embedder itself stays in the child).
5. **Summary**: full schema (run/discovering→publishing phases, services, diff, impact, primary/framework/
   topology/vector entries, state_before/after, lock, change_sources, repositories, coverage_warnings,
   reconciliation, parse_quality, run_result) and happy-path log lines
   (`[state] no changes detected; state marked clean`, `[state] summary changed=... deleted=... impacted=... parsers=...`,
   `[state] incremental sync completed successfully`, `[upsert] parser=X mode=full reason=...`,
   `[impact] parser=...`, `[overlay] ...`, `[embedding] starting graph-disabled primary analyzer pass`,
   `[parse-quality] run artifact: ...`).
6. **Journal shadow lane**: `configure_journal_env` ported including the
   `cortex_harness.storage.targets` effective-graph-target fingerprint and `RunMetadata` canonical JSON
   (`cortex-graph-core::schema_manifest` fingerprint) — required for byte-identical child journal env in
   shadow mode (`shared-shadow` default lane), where finalize/status are no-ops exactly like Python.

## 4. Exclusions (Python-plane — the Rust orchestrator shells out to the Python orchestrator)

When any of these paths would execute, `cortex-sync` prints
`[cortex-sync] python-plane delegation: <reason>` and re-executes
`<python_bin> code-tiny/tools/sync/incremental_sync.py <identical args>` (exit code propagated):

1. **Required journal lanes** (`CORTEX_GRAPH_JOURNAL_MODE` in {`required`,`shared-required`} configured, or a
   `cplus` lane with work under the default lane map): SQLite journal store, resume/quarantine,
   `finalize_journal_from_env` drain, `_resume_configured_journal`. Shadow lanes (the default for
   python/shell/ts/js/php/perl/java/...) run natively.
2. **Embedded FalkorDB storage resolution** (`resolve_storage().falkordb_code_path` when no
   `FALKORDB_URI`/`--falkordb-path`), and the ladybug provider path.
3. **Failure deep paths**: `FailureClass` failure classification/debug-artifact redaction machinery —
   component failures are recorded with a minimal `parser_isolation`/`analyzer_child_failed` record and the
   run exits 1 (continue-and-raise-later behavior is kept; the classification fields are not).
4. **Message scan internals**: the `--enable-message-scan` / `--message-output-dir` /
   `--message-qdrant-collection` flags and default collection names are passed through exactly, but the
   message-scan subsystem itself remains in the child analyzers (unexercised without `--sync-messages`-capable
   embedding runs).
5. **Sync-workers registry doctor checks, qdrant layout cache**: not present on the ported main line at all.
6. **Harness `dev.json` local-storage overlay**: `load_harness_config` ported for provider normalization and
   graph env propagation; the `CORTEX_DATA_HOME` storage overlay is Python-plane (no-op for empty configs,
   e.g. `--config /dev/null`).

## 5. Accepted divergences (invisible to the gates)

1. **Framework module detectors**: `SpringProjectDetector` / `ServletJspProjectDetector` /
   `MyBatisProjectDetector` / aspnet detectors / full `detect_flutter_project` are approximated by
   candidate-extension gating + strong-candidate name rules + build-file content walks. Overlay *execution*
   is additionally gated by `--parsers` membership (framework overlays only run when explicitly requested),
   same as Python; the corpus-level routing output (`detector_evidence` for module-detector lanes) may
   contain fewer evidence strings than Python when Java/.NET module structures exist.
2. **`apply_project_registry_defaults`**: the ProjectRegistry lookup is approximated as "not registered"
   (warning + fallback to `--project-id`/env graph name). Only projects registered under
   `<root>/.cortext-harness/config` would see a difference in default `--falkordb-graph`.
3. **Component failure records** on child failure: minimal schema (see exclusion 3); Python adds
   failure-class prioritized re-raise (Rust re-raises the first recorded child failure).
4. **Lock-busy `run_result`**: Python marks outcome `failed_retryable` (LOCK class); Rust writes
   `failed_terminal` with a null failure record (exit code 2 and summary status/outcome `lock_busy` match).
5. **JP1 sniff decoding**: `decode_legacy_bytes` cp932 branch degrades to latin-1 fallback (structural
   `unit=`/`ty=`/`{` checks are ASCII; no parity impact, .txt JP1 files absent from the corpus).
6. **`--python-bin` default**: Python uses `sys.executable`; Rust resolves
   `CORTEX_SYNC_PYTHON_BIN` → `<repo>/.venv/bin/python` → `python3`.
7. **serde_json key order** in the summary file is alphabetical (Python dict insertion order); the gate
   compares parsed structure, not bytes.

## 6. Suspected upstream bugs / observations

1. `_graph_target_cli_args` never forwards `--falkordb-uri` to children — children only receive the URI via
   the propagated `FALKORDB_URI` env. Correct as long as `_build_analyzer_env` runs (it does), but a child
   invoked outside the orchestrator env cannot reconstruct the parent's target from CLI args alone.
2. `incremental_sync._SKIP_DIRS` prunes `bin`/`obj`/`target` unconditionally while
   `_SOURCE_EXTENSIONS` admits `.xml`/`.json`/`.properties` broadly — a source file literally named under a
   skipped dir is invisible to `_walk_all_source_files`, yet the same file arriving via a *committed* git
   diff survives `committed` change detection until the `_is_source_candidate` parent-dir filter drops it.
   The two filters are consistent today, but only by duplication.
3. `journal_status_from_env` returns `None` for `shared-required` lanes only when `config.shadow` — the
   "shared-required" mode is *not* shadow (finalize drains), so the summary `journal` aggregate silently
   depends on lane mode; the Rust port avoids half-implementing this by delegating required lanes entirely.

## 7. Reproduce

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
cargo build --release -p cortex-sync
.venv/bin/python scripts/rust_parity/sync_orchestrator_parity.py [--keep]
# → prints per-gate JSON; exit 0 when all gates pass
```

FalkorDB must be reachable at `127.0.0.1:6379` (gates (c)/(d) run the incremental path with impact
expansion against per-backend graphs).
