# Research digest — cutover analyzer layer (per-language parsers) sang Rust làm default

Date: 2026-09-15. All paths relative to repo root `/Users/user/AI/cortex-harness/` unless absolute. Line numbers verified against working tree (git `4e21731`, no uncommitted changes to files cited except as noted by `git status`).

## Findings

### A. Orchestrator topology and flip semantics (the core correction to the initial hypothesis)

1. **`dev sync code` (cortex-dev, Rust binary) spawns `cortex-sync` as THE orchestrator — no Python fallback at that seam.** `rust/crates/cortex-dev/src/cmds/sync.rs:520-541`: binary resolution `CORTEX_SYNC_BIN` → `rust/target/release/cortex-sync` → `rust/target/debug/cortex-sync`; error text: "no Python fallback — `cortex-sync` IS the orchestrator now".
2. **`cortex-sync` registry lags the phase-14 flip.** `rust/crates/cortex-sync/src/registry.rs:257-267` `rust_analyzer_binaries()` maps only 7 parsers (python, shell, ts, js, php, perl, java). `registry.rs:272-276` `rust_analyzer_binary()`: requires env `CORTEX_RUST_ANALYZER` to be exactly `rust`; unset (or any other value) → `None` → Python script. Missing binary → `None` (silent Python fallback, `registry.rs:284-289`).
3. **The Python orchestrator `code-tiny/tools/sync/incremental_sync.py` already has phase-14 auto-flip and a 22-parser map.** `incremental_sync.py:1360-1383` `_RUST_ANALYZER_BINARIES` = 22 entries (adds kotlin, android, go, rust, swift, delphi, cobol, jp1, vbnet, vb6, vba, vbscript, cplus, sql, plsql). `incremental_sync.py:1386-1413` `_rust_analyzer_binary()`: UNSET → Rust when binary exists (auto-flip); `=python` → rollback; `=rust` → legacy; other → no swap. Regression test: `tests/test_phase14_rust_analyzer_flip.py` (docstring lines 1-6).
4. **Discrepancy:** `docs/cutover-runbook.md:27` documents `CORTEX_RUST_ANALYZER` unset = "auto-flip: dùng analyzer Rust cho parser đã port khi binary tồn tại" — true only for the Python orchestrator; false for `cortex-sync` (finding 2). `phase-14.md:39-40` confirms the flip was implemented for the Python file ("flip defaults: `CORTEX_RUST_ANALYZER` unset → auto-Rust (binary present) / `=python` rollback").
5. **Net effect today (default run, no env, macOS):** every one of the 24 primary parsers, 12 framework overlays, and project_topology is spawned as `python_bin <script>.py` by cortex-sync. The 32 `analyzer-*` binaries in `rust/target/release/` are only used when `CORTEX_RUST_ANALYZER=rust` and only for the 7 mapped parsers.

### B. Full gap table

Backend key: PY = python script via `--python-bin`; RS = Rust binary; C# = dotnet subprocess (inside a PY entry).

**Primary parsers — `analyzers()` (`registry.rs:80-120`, 24 entries, iteration order `registry.rs:124-128`):**

| parser | script (PY path) | Rust binary ([[bin]]) | producing crate | in cortex-sync map (7)? | in py-sync map (22)? |
|---|---|---|---|---|---|
| cobol | tools/cobol/cobol_analyzer.py | analyzer-cobol | analyzer-cobol | no | yes |
| dart | tools/flutter/flutter_analyzer.py `--mode dart` (registry.rs:87-92) | **none** | — | no | **no** |
| cplus | tools/cplus/cplus_analyzer.py | analyzer-cplus | analyzer-cplus | no | yes |
| delphi | tools/delphi/delphi_analyzer.py | analyzer-delphi | analyzer-delphi | no | yes |
| java | tools/java/java_analyzer.py | analyzer-java | analyzer-java | yes | yes |
| kotlin | tools/kotlin/kotlin_analyzer.py | analyzer-kotlin | analyzer-kotlin | no | yes |
| android | tools/android/android_kotlin_analyzer.py | analyzer-android | analyzer-android | no | yes |
| vbnet | tools/vb/vbnet_analyzer.py | analyzer-vbnet | analyzer-vb (bin vbnet.rs) | no | yes |
| vb6 | tools/vb/vb6_analyzer.py | analyzer-vb6 | analyzer-vb | no | yes |
| vba | tools/vb/vba_analyzer.py | analyzer-vba | analyzer-vb | no | yes |
| vbscript | tools/vb/vbscript_analyzer.py | analyzer-vbscript | analyzer-vb | no | yes |
| python | tools/python/python_analyzer.py | analyzer-python | analyzer-python | yes | yes |
| go | tools/go/go_analyzer.py | analyzer-go | analyzer-go | no | yes |
| perl | tools/perl/perl_analyzer.py | analyzer-perl | analyzer-perl | yes | yes |
| shell | tools/shell/shell_analyzer.py | analyzer-shell | analyzer-shell | yes | yes |
| jp1 | tools/jp1/jp1_analyzer.py | analyzer-jp1 | analyzer-jp1 | no | yes |
| rust | tools/rust/rust_analyzer.py | analyzer-rust | analyzer-rust | no | yes |
| swift | tools/swift/swift_analyzer.py | analyzer-swift | analyzer-swift | no | yes |
| js | tools/js/js_analyzer.py | analyzer-js | analyzer-js | yes | yes |
| ts | tools/ts/ts_analyzer.py (+ dynamic ts backend resolution, orchestrator.rs:1916) | analyzer-ts | analyzer-ts | yes | yes |
| php | tools/php/php_analyzer.py | analyzer-php | analyzer-php | yes | yes |
| csharp | tools/csharp/csharp_analyzer.py → Roslyn C# worker (finding 6) | **none** | — | no | **no** |
| sql | tools/sql/sql_analyzer.py | analyzer-sql | analyzer-sql-family | no | yes |
| plsql | tools/plsql/plsql_analyzer.py | analyzer-plsql | analyzer-sql-family | no | yes |

**Framework overlays — `framework_analyzers()` (`registry.rs:137-253`, 12 entries).** cortex-sync has NO framework→binary map at all; overlays are always spawned PY via `AnalyzerConfig::with_script` (orchestrator.rs:1669). Rust binaries exist for 11/12:

| framework | script | Rust binary | crate |
|---|---|---|---|
| spring | tools/spring/spring_analyzer.py | analyzer-spring | analyzer-jvm-overlays (Cargo.toml:9-19) |
| servlet_jsp | tools/servlet_jsp/servlet_jsp_analyzer.py | analyzer-servlet-jsp | analyzer-jvm-overlays |
| mybatis | tools/mybatis/mybatis_analyzer.py | analyzer-mybatis | analyzer-sql-family |
| struts | tools/struts/struts_analyzer.py | analyzer-struts | analyzer-jvm-overlays |
| flutter | tools/flutter/flutter_analyzer.py `--mode flutter` (registry.rs:176-184) | **none** (same script as dart) | — |
| aspnet_framework | tools/aspnet_framework/aspnet_framework_analyzer.py | analyzer-aspnet-framework | analyzer-web-overlays (runs real Roslyn worker subprocess — phase-08.md tail) |
| aspnet_core | tools/aspnet_core/aspnet_core_analyzer.py | analyzer-aspnet-core | analyzer-web-overlays |
| fastapi_django | tools/web_framework/web_framework_analyzer.py `--framework fastapi_django` | analyzer-fastapi-django | analyzer-web-overlays |
| express_js | same script `--framework express_js` | analyzer-express-js | analyzer-web-overlays |
| laravel | same script `--framework laravel` | analyzer-laravel | analyzer-web-overlays |
| database_sql | tools/database_schema/database_schema_analyzer.py `--dialect sql` | analyzer-database-schema (shared) | analyzer-sql-family |
| database_plsql | same script `--dialect plsql` | analyzer-database-schema (shared) | analyzer-sql-family |

**project_topology:** `registry.rs:130-135` → `tools/project_topology/topology_analyzer.py` (1,835 LOC). No analyzer binary; only the *writer* half is ported (`rust/crates/cortex-graph-writer/src/topology.rs`, exported per `cortex-graph-writer/src/lib.rs:10-11,29,34`). Spawned PY at `orchestrator.rs:1801-1831` (run at 1878). Mission hint "orchestrator.rs ~1671" is actually the framework overlay cmd build (frameworks, not topology); the topology spawn is at 1802.

**Binary-name check:** all 22 names in the Python map byte-match `[[bin]]` names; underscore-vs-dash only affects framework keys (`servlet_jsp` → `analyzer-servlet-jsp`). No binary is missing for the 22.

### C. Confirmed parser-specific findings

6. **csharp is NOT "already non-Python subprocess".** Chain: cortex-sync spawns `python_bin tools/csharp/csharp_analyzer.py` (registry.rs:112) → `tools/csharp/roslyn_integration.py` `RoslynFirstRunner` (csharp_analyzer.py:50-63, 1156-1163) → `tools/csharp/roslyn_adapter.py` spawns `dotnet <dll> --manifest request.json` (roslyn_adapter.py:4, 39-99), tree-sitter fallback per file. Worker is a C# project `tools/csharp/roslyn_worker/` (CSharpRoslynWorker.csproj). No analyzer-csharp crate; absent from both rust maps. plan.md:39 non-goal: "**C# Roslyn analyzer** — đã là process ngoài bằng C#; orchestrator Rust chỉ invoke subprocess" — this refers to the Roslyn plane; the analyzer *entry* remains a 2,059-LOC Python script (dir total 3,109 LOC). Treating csharp as "done" would leave a Python child in the spawn path.
7. **dart/flutter has no Rust port and none scoped.** Registry entry `registry.rs:87-92` (dart, `--mode dart`) and framework entry `registry.rs:176-184` (flutter, `--mode flutter`, default from env `FLUTTER_ANALYZER_MODE`, flutter_analyzer.py:168). `tools/flutter/` = 1,659 LOC (dart_parser.py 641, flutter_analyzer.py, normalizer, detector, models, cache, protocol, pipeline). Implementation is tree-sitter-dart + project-local symbol resolution + LanguageCodeWriter (plans/260714-1603-flutter-analyzer-parser/plan.md lines 10-16: "Python uses the precompiled `tree-sitter-dart` grammar…"). Plan status: `in_progress` (plan.md:3). Phase-09 report only "approximated" `detect_flutter_project` in the orchestrator (phase09-orchestrator-parity.md:112); no Rust analyzer-dart crate exists in `rust/crates/`.
8. **dart is in the vector-CLI set** (`SHARED_VECTOR_CLI_PARSERS` = dart, go, jp1, perl, rust, shell, swift, `registry.rs:292-293`), so a dart Rust decision affects the embedding pass too.
9. **cplus keeps a Python subprocess plane by design** (clang): phase-07.md tail "clang plane giữ Python subprocess theo key decision #8"; `tools/cplus/` includes clang_parser.py (821), semantic_worker.py (706), proc_analyzer.py (833).

### D. Framework overlay spawn bug in cortex-sync (new finding)

10. **cortex-sync drops framework `extra_args`.** `orchestrator.rs:1669` builds `AnalyzerConfig::with_script(framework_name, &framework_config.script_path)`; `with_script` sets `extra_args: vec![]` (`registry.rs:56-65`); `build_analyzer_cmd` then appends nothing (`registry.rs:436`). The Python side passes `framework_config.extra_args` (`incremental_sync.py:3190-3196`).
11. **Consequence:** `web_framework_analyzer.py:33` declares `--framework` **required** (choices fastapi_django/express_js/laravel) and `database_schema_analyzer.py:33` declares `--dialect` **required** (choices sql/plsql) — spawned by cortex-sync without the flag these exit(2) on argparse. *Inferred* (not observed in a run): this path was never exercised — the phase-09 parity corpus (ts+shell+py fixtures; `phase09-orchestrator-parity.md:23-25`) contains no web-framework/database evidence, and overlay execution is evidence-gated (orchestrator.rs:1630-1639 "no framework evidence in changed/deleted paths"). spring/servlet_jsp/struts/mybatis/aspnet*/flutter scripts have no required framework flags (checked spring_analyzer.py:52-57; flutter `--mode` has a default, flutter_analyzer.py:168).

### E. delegate_to_python reachability (whole-run delegation)

12. Two triggers, both re-exec `<python_bin> code-tiny/tools/sync/incremental_sync.py <identical args>` (`orchestrator.rs:2541-2559`):
    - **Graph target resolution** — `orchestrator.rs:168-177` → `graphops::prepare_graph_args` (`graphops.rs:66-126`): Err when provider is `ladybug` (graphops.rs:85-88, "embedded storage resolution … Python-plane") or falkordb with neither `FALKORDB_URI` nor `--falkordb-path` (graphops.rs:104-107). Neo4j with full creds → native (73-83). Provider default: falkordb on non-Windows, **ladybug on Windows** (`cli.rs:30-41`).
    - **Required journal lane** — `orchestrator.rs:1107-1126`: `CORTEX_GRAPH_JOURNAL_MODE ∈ {required, shared-required}` (`journalenv.rs:24-26`) OR mode unset + `cplus` selected + cplus files changed. Note the lane default map: cplus lane defaults to `shared-required` when mode unset (`journalenv.rs:49-65`) — so **any cplus change in a default run delegates the entire sync to the Python orchestrator**.
13. **Required-lane consumption is still Python-plane.** `cortex-dev/src/journalx.rs:297-340` `recover_required_lane` is documented "FORCED-PYTHON": spawns `python -m tools.graph.journal.consumer`. Phase-02.md:27-31 gate unchecked: "cần cầu PyO3 cho `Journal` (mẫu `cortex-retrieval-py`) + adapter Python trong `GraphWriteJournalRuntime`" — i.e. SQLite journal store logic is ported (`journal_manifest.rs`) but the live consumer/bridge is not wired. `journalx`/`dev journal` covers status/purge natively, not the sync-run drain.
14. **Silver lining for cutover:** a delegated run still gets Rust *analyzers* for the 22 mapped parsers because the Python orchestrator's auto-flip is active (finding 3). So "rust orchestrator default" and "rust analyzers default" are separable; the analyzer-layer cutover survives graph-target/lane delegation, but a rust-default run CAN still land on the Python orchestrator today.

### F. Embedding / vector path

15. **Architecture:** embedding is a second, graph-disabled analyzer pass inside cortex-sync (`orchestrator.rs:1907-2033`, log line "[embedding] starting graph-disabled primary analyzer pass"), re-spawning the SAME child per parser with `--qdrant-collection` plus `--embed-model/--device/--batch-size/--max-embed-chars` for `SHARED_VECTOR_CLI_PARSERS` (`registry.rs:473-490`; pass-through at orchestrator.rs:2007-2010). The embedder lives in the child ("Plan B", phase09-orchestrator-parity.md:71).
16. **Rust analyzer binaries do NOT embed.** `rust/crates/cortex-analyzer-framework/src/cli.rs:91-111`: `--embed-model` "giữ contract; embedding tách khỏi analyzer Rust", `--qdrant-url` "backend Rust không embed (key decision #3)"; flags accepted-and-ignored. plan.md:71-73 decision #3 states embeddings were to be orchestrated separately ("Python embedder worker đầu tiên, ONNX ort sau") — but the implemented orchestrator embedding pass re-runs analyzer children, and a Rust child would silently write no vectors.
17. **Vector parity with Rust children is UNVERIFIED:** the phase-09 orchestrator gate ran "(no qdrant)" (`phase09-orchestrator-parity.md:25`), vector status `disabled` (line 42); "qdrant layout cache" listed as not ported (line 104). So under `CORTEX_RUST_ANALYZER=rust` + qdrant-enabled sync, vector writes for the 7 currently-mapped parsers are dropped today. Nothing in `build_analyzer_cmd` forces the Python child for the embedding pass (same `rust_analyzer_binary()` resolution, `registry.rs:415`).
18. **cortex-embed (ONNX) status:** spike plan `260914-1706-onnx-embedding-spike/plan.md:3` status "partially-done". Runbook (`docs/cutover-runbook.md:41-48`): numeric parity reached (cosine worst 0.9999994 on 840 vectors; GLiNER 848/848) but `CORTEX_EMBED_BACKEND=onnx` NOT default: (1) tool-result scores shift at the 7th digit → phase-12/13 golden re-baseline pending; (2) +25-31 ms/tool-call latency unexplained. Code plane remains jina-v3 via Python sidecar (spike plan.md table: "Code … Rust **không embed**; MCP Rust trả vector lane RỖNG có chủ đích").

### G. Env flags and plumbing to flip

19. **`CORTEX_RUST_ANALYZER` read sites (code):** `cortex-sync/src/registry.rs:273` and `incremental_sync.py:1396` only. Referenced (not read) in `cortex-dev/src/env.rs:809` (comment), `cortex-embed/src/backend.rs:3` (pattern analogy), tests, scripts/rust_parity/sync_orchestrator_parity.py, docs. Not set by Makefile/dev.sh/entrypoints.
20. **`CORTEX_RUST_ANALYZER_BIN_DIR`:** registry.rs:279-283 (default `rust/target/release`); incremental_sync.py:1408-1411 (same default).
21. **`--python-bin` / python resolution:**
    - cortex-dev passes `--python-bin sync_python_bin()` to cortex-sync (`cmds/sync.rs:876-877, 1020-1021`); `env.rs:807-811` = `util::harness_python(<repo>/code-tiny)` (project venv → harness venv → ambient).
    - cortex-sync: `cli.rs:43-56, 296-299` (Args default) and `cli.rs:430-449` `resolve_python_bin` for the delegation path (order: `--python-bin` → `CORTEX_SYNC_PYTHON_BIN` → `<repo>/.venv/bin/python` → `python3`).
    - **If `--python-bin` is removed:** cortex-sync still finds a venv python, so nothing breaks immediately — python_bin remains needed for (a) the 2 unported primary parsers (dart, csharp), (b) project_topology, (c) framework overlays unless mapped, (d) whole-run delegation, (e) `CORTEX_RUST_ANALYZER=python` rollback. Removing the flag only becomes safe when every child is a binary and delegation paths are closed/error loudly; keeping it is cheap and is the documented rollback seam (env.rs:806-809).

### H. Verification assets to reuse

22. **Per-analyzer parity harness:** `scripts/rust_parity/analyzer_parity*.py` (30 gates: one per analyzer/overlay incl. `_p08_common`/`sqlfamily_common`/`phase08_overlay_harness`), pattern per `analyzer_parity.py:1-21`: dual-run PY vs RS on two FalkorDB graphs, same CLI contract, dump nodes+edges+properties, mask (`_dst/_edge_id/_graph_id/_src/created_at/last_updated/summary_updated_at/updated_at[/_start_id/_end_id]`), graph diff 0 + `[SCAN_RESULT]` byte-identical + incremental cleanup counts. Run: `.venv/bin/python scripts/rust_parity/analyzer_parity_<x>.py` from repo root.
23. **Orchestrator + realworld gates:** `scripts/rust_parity/sync_orchestrator_parity.py` (12 runs, gates a-e; needs falkordblite at 127.0.0.1:6379, `phase09-orchestrator-parity.md:145-155`), `realworld_stock_test.py` (realworld-stock-test.md, 2026-09-13), `dual_write_diff.py` (mask source).
24. **Parity evidence:** `plans/260913-2130-rust-full-migration/reports/` — 29 analyzer/overlay reports `phase04…phase08`, mtimes Sep 14 2026 (04:47–16:24), each phase doc's status line "PASS toàn bộ" (phase-04/05/06/07/08.md tails; e.g. phase04-analyzer-parity.md "FAILURES: không có — PASS toàn bộ"). Quirks recorded in reports: go/jp1 need `CORTEX_DIAGNOSTIC_SKIP_UNRESOLVED_RELATIONS=1` both sides; vbnet exercises real Roslyn worker with `DOTNET_ROLL_FORWARD=LatestMajor`; 2 spring/struts writer quirks kept.
25. **Dogfood gate (runbook):** `docs/cutover-runbook.md` §2 daily loop (`dev sync code … all`, verify children are `rust/target/release/analyzer-<lang>` binaries, node/rel counts, `dev journal status`, 7 consecutive clean days), §3 flip step (unset envs = Rust default; update ReadMe/INSTALLER_GUIDE), §4 rollback (`export CORTEX_RUST_ANALYZER=python`), §5 archive order: `analyzer-<lang>` blocks archived first, after 2 stable releases; keep Python-generated parity fixtures as evidence.
26. **testtool** (`code-tiny/testtool/suites/*.json`) is MCP-tool-level, not analyzer parity — not directly reusable for this cutover's analyzer gate.

### I. Python LOC that would be retired (measured, `wc -l` on `code-tiny/tools/*/`)

27. Total `code-tiny/tools/` = 106,408 LOC. Analyzer layer (excluding `sync/` 5,252, `common/` 15,933, `graph/` 443) ≈ **84,780 LOC** across: cplus 16,571, servlet_jsp 7,335, android 6,738, ts 5,810, mybatis 4,703, vb 3,141, csharp 3,109, delphi 2,745, java 2,613, plsql 2,564, kotlin 2,397, sql 2,280, python 2,186, js 2,043, perl 2,035, cobol 1,979, spring 1,941, php 1,860, **project_topology 1,835**, **flutter 1,659**, struts 1,645, rust 1,442, go 1,407, swift 1,329, shell 908, aspnet_framework 713, aspnet_core 666, jp1 397, database_schema 341, web_framework 388. (Matches plan.md's "~84k analyzers + overlays" estimate, plan.md:26.)
28. **External references that outlive the scripts:** `cortex_harness/dev.py:74,106` (ANALYZERS/FRAMEWORK dicts — dev.py is parity-reference only now per runbook header lines 14-16); `cortex_harness/sync_processes.py`; tests `test_sync_processes.py`, `test_incremental_sync_{cobol,phase_modes,continue_on_error,worktree}.py`; all of `scripts/rust_parity/` (kept deliberately as evidence, runbook §5.2). `code-tiny/mcp/` and `rust/crates/cortex-mcp` reference no analyzer scripts. No importer outside these sets found (grep 2026-09-15).
29. **Stale doc:** runbook §5.3 (cutover-runbook.md:167-169) says keep `incremental_sync.py` because it "vẫn là entry `dev sync code`" — no longer true since dev spawns the cortex-sync binary (finding 1); it remains the delegation target + rollback reference.

### J. Plan interplay

30. **`260913-2130-rust-full-migration`** — status `plan.md:3`: "code-complete (P01-P14 xong toàn bộ; còn gate vận hành: dogfood 1 tuần trước khi flip default — runbook docs/cutover-runbook.md)". Phases 04-08 = analyzers/overlays (all PASS, finding 24). **Unfinished wave-D gate** (phase-08.md tail, unchecked): "`dev sync code all` … chạy với TOÀN BỘ analyzers Rust qua `CORTEX_RUST_ANALYZER=rust` trên stock — summary khớp Python run" — no report file for it exists (checked reports/ listing). This cutover plan is effectively that gate, upgraded: flip default in cortex-sync itself, not via env.
31. **`260914-1706-onnx-embedding-spike`** — "partially-done"; not blocked-by this plan, but gates the vector story: until re-baseline + latency items close, `CORTEX_EMBED_BACKEND` stays `python` and the embedding pass must keep a Python-capable child (finding 18).
32. **`260714-1603-flutter-analyzer-parser`** — `in_progress`, Python-only by design; dart is the one primary parser with no Rust binary and no scoped port (finding 7). Either this cutover scopes an analyzer-dart crate or dart is carved out with an explicit allow-Python list.
33. **`260914-2259-dev-make-python-cutover`** — "ready (red-team rev 2 …)"; entrypoint/dev-layer cutover already landed binary-only (runbook header; commit c321a6b "review PASS 9/10"). This analyzer cutover is the next seam down and should inherit its rollback conventions (`CORTEX_DEV_BIN`-style binary pinning, no python entrypoint).

## Conventions to follow

- Strangler-fig behind env flags: `CORTEX_RUST_ANALYZER` unset = auto-Rust-when-binary (phase-14 semantics), `=python` = rollback, `=rust` = legacy force; missing binary falls back silently (incremental_sync.py:1389-1402 is the normative contract; mirror it in registry.rs).
- Per-analyzer parity gate before enabling its binary: graph diff 0 outside the `dual_write_diff` mask + `[SCAN_RESULT]` byte-identical + incremental cleanup counts (analyzer_parity.py:8-14).
- Grammar pins live in `rust/grammar-versions.toml`; clippy `-D warnings` clean; rust-analyzer-core canonical JSON must stay sorted (see code-complete log "Bug đáng nhớ nhất").
- Status lines live in each plan's frontmatter; parity evidence goes to `plans/<plan>/reports/`.
- Child CLI contract is byte-stable (`--root --project-id … --changed-files-manifest`, journal env via `configure_journal_env`); both backends must accept identical flags (plan.md decision #2).

## Risks / open questions

1. **[verified] Vector-write drop under rust children:** Rust analyzers ignore qdrant/embed flags (cli.rs:91-111) and the embedding pass reuses `rust_analyzer_binary()`; parity never ran with qdrant (phase09 report:25,42). Unknown: whether any production dogfood ran embedding+rust together. Must be resolved before defaulting.
2. **[verified bug, unexercised run] framework overlay `extra_args` dropped** by cortex-sync (orchestrator.rs:1669 + registry.rs:56-65) → required `--framework`/`--dialect` crash for fastapi_django/express_js/laravel/database_sql/database_plsql overlays. Inferred-never-hit: no evidence-gated corpus exercised them (phase09 corpus).
3. **[verified] cplus default lane delegates the whole run** to the Python orchestrator whenever cplus files change with journal mode unset (orchestrator.rs:1116-1119 + journalenv.rs:60-61); required-lane consumer is FORCED-PYTHON (journalx.rs:297-340); phase-02 PyO3 journal bridge still pending (phase-02.md:27-31). So a "rust-default, never-silently-Python" bar cannot be met for cplus-heavy repos without native required-lane drain or a policy change.
4. **[verified] ladybug provider / embedded FalkorDB storage delegations** (graphops.rs:85-88, 104-107); Windows default provider is ladybug (cli.rs:36) — on Windows every sync delegates today. Unknown: whether Windows dogfood is in scope for the runbook's 7-day gate.
5. **[verified gap] dart & csharp have no Rust binary**; registry parity count 7 vs 22 (registry.rs:257-267 vs incremental_sync.py:1360-1383). Unknown: whether this plan scopes analyzer-dart and analyzer-csharp (csharp = Python entry + Roslyn worker; porting means rewriting the entry in Rust or accepting it as permanent allow-Python).
6. **[verified] Wave-D "all analyzers rust through orchestrator on stock" gate was never run/recorded** (phase-08.md unchecked; no report). The 29 per-analyzer PASSes don't cover orchestrator-level composition (routing, journal lanes per parser, summary aggregation) with rust children.
7. **[unknown] `--enable-message-scan` internals** stay in child analyzers (phase09 report:100-103); message-scan behavior with rust children unverified (only `message_scan.py` Python exists).
8. **[inferred] Runbook/plan docs drift:** runbook:27 auto-flip claim (cortex-sync doesn't implement it), runbook:167 stale "incremental_sync.py is the dev sync entry", plan.md decision #3 embedding design vs implemented Plan B (finding 15-16). Doc updates belong in this cutover's scope.

## Recommended approaches

1. **Mirror phase-14 flip into cortex-sync**: extend `rust_analyzer_binaries()` to the 22-parser set, add a framework→(binary, extra_args) map + topology policy, and fix `with_script` to carry `extra_args` (or build overlays from `framework_config`), keeping `=python` rollback and missing-binary fallback semantics identical to incremental_sync.py:1386-1413.
2. **Gate the flip on composition parity, not just per-analyzer parity**: run the stock dual-run (rust orchestrator + rust children) with `sync_orchestrator_parity.py`/`realworld_stock_test.py` extended for qdrant counts and cplus/ladybug delegation policy, closing the unchecked Wave-D gate and the vector-drop risk (force Python children for the embedding pass or wire cortex-embed first).
3. **Carve out the unported two explicitly**: keep dart (no port exists; flutter plan in_progress) and csharp (Roslyn-entry) on an allow-Python list with a loud startup note, and leave `--python-bin`/delegation intact as the documented rollback seam until they are either ported or policy-decided.
