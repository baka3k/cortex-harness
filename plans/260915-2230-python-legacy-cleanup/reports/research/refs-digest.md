# refs-digest — every live reference to a Python (`.py`) file

Research date: 2026-09-15, branch `feat/change-db`, repo `/Users/user/AI/cortex-harness`.
Scope: all references/spawns/invocations of `.py` files from live (non-archived) repo surfaces.
Excluded per brief: `.venv/`, `.qwen/`, `__pycache__/`, `rust/target/`, `node_modules/`, `scripts/archived/`, `tests/fixtures/`, `plans/` (except where a live reference points into them).

Legend: **RUNTIME** = executed during normal dev/make/server flows · **PARITY** = only used by parity/record/compare tooling · **DOC-STALE** = docs/comments describing deleted or bypassed behavior · **INSTALLER** = installer/packaging surface · **MARKER** = file used only as an existence probe, never executed · **DEAD-IN-SPAWN** = code path present but provably unreachable post-phase-08.

Pre-verified existence state of the key targets (as of this branch):

| target | state |
|---|---|
| `code-tiny/tools/sync/incremental_sync.py` | EXISTS (live delegation target) |
| `code-tiny/mcp/unified_mcp.py` | EXISTS |
| `doc-tiny/graphrag_ingest_langextract.py` | EXISTS |
| `doc-tiny/mcp_graph_rag.py` | EXISTS |
| `code-tiny/scripts/setup_constraints.py` | EXISTS |
| `scripts/mcp-lifecycle.py` | EXISTS |
| `scripts/rust_mcp/embed_worker.py`, `vector_worker.py`, `gliner_sidecar.py` | EXIST |
| `harness/scripts/orchestrator.py`, `context_selector.py` | EXIST |
| `cortex_harness/dev.py` | EXISTS (header says "PARITY-REFERENCE-ONLY", line 1) |
| `code-tiny/tools/{csharp/csharp_analyzer.py, project_topology/topology_analyzer.py, jp1/jp1_analyzer.py, ts/ts_backend_analyzer.py}` | MISSING (deleted in analyzer-layer cutover) |
| `code-tiny/tools/ts/ts_analyzer.py` | EXISTS |
| `code-tiny/tools/cplus/` entry `cplus_analyzer.py` | MISSING (only helper modules remain: `clang_worker.py`, `bootstrap_compile_commands.py`, …) |

---

## Findings

### Category 1 — Rust sources (`rust/crates/**/*.rs`)

#### 1a. Actual spawn/exec sites (RUNTIME unless noted)

1. `rust/crates/cortex-sync/src/orchestrator.rs:2935` -> `code-tiny/tools/sync/incremental_sync.py` [RUNTIME — forced delegation path]
   ```rust
   let script = repo_root.join("code-tiny/tools/sync/incremental_sync.py");
   ```
   Context (`delegate_to_python`, lines 2934–2949): prints `[cortex-sync] python-plane delegation: {reason}`, re-execs identical argv via `crate::cli::resolve_python_bin(&raw)`.

2. `rust/crates/cortex-sync/src/orchestrator.rs:342` -> consumer of `DELEGATE_SENTINEL` [RUNTIME]
   ```rust
   if let Some(reason) = message.strip_prefix(DELEGATE_SENTINEL) {
       return delegate_to_python(reason);
   }
   ```

3. `rust/crates/cortex-sync/src/orchestrator.rs:1226` -> sole sentinel emitter [RUNTIME — condition below]
   ```rust
   return Err(format!(
       "{DELEGATE_SENTINEL}required graph journal lane (SQLite store, resume/finalize) is Python-plane"
   ));
   ```
   Condition (lines 1217–1225): `setup_is_required` = `CORTEX_GRAPH_JOURNAL_MODE` ∈ {`required`,`shared-required`} **OR** (mode empty AND parser filter contains `cplus` AND ≥1 changed cplus file).

4. `rust/crates/cortex-sync/src/cli.rs:430-449` -> `resolve_python_bin` [RUNTIME convention]
   ```rust
   pub fn resolve_python_bin(raw: &[String]) -> String {
   ```
   Order: `--python-bin` CLI arg → env `CORTEX_SYNC_PYTHON_BIN` → `<exe>/../../../.venv/bin/python` → `"python3"`.

5. `rust/crates/cortex-dev/src/journalx.rs:333-334` -> `python -m tools.graph.journal.consumer` [RUNTIME — forced]
   ```rust
   let recovery = std::process::Command::new(crate::util::harness_python(&root))
       .args(["-m", "tools.graph.journal.consumer"])
   ```
   (`journalx.rs:306` `recover_required_lane`): fires only when `CORTEX_GRAPH_JOURNAL_MODE` ∈ {`required`,`shared-required`}; cwd = `code-tiny`, `PYTHONPATH` prepended with `code-tiny`. Doc (lines 300–305): "FORCED-PYTHON … has no Rust port — cortex-sync itself delegates required lanes to Python". Caller: `rust/crates/cortex-dev/src/cmds/sync.rs:344-345`.

6. `rust/crates/cortex-dev/src/cmds/harness.rs:91` -> copies harness scripts [RUNTIME — `dev harness init`]
   ```rust
   for script in ["init.sh", "verify.sh", "context_selector.py", "orchestrator.py"] {
   ```
   Source dir `harness/scripts` (`harness.rs:58-60`), dest `<project>/.harness/scripts/`.

7. `rust/crates/cortex-dev/src/cmds/harness.rs:461-497` -> `.harness/scripts/orchestrator.py` [RUNTIME — `dev harness run`]
   ```rust
   let orchestrator = project_path.join(".harness").join("scripts").join("orchestrator.py");
   ...
   let python = crate::util::harness_python(&crate::util::repo_root());
   ```

8. `rust/crates/cortex-dev/src/cmds/harness.rs:502-546` -> `.harness/scripts/context_selector.py` [RUNTIME — `dev harness context`]
   ```rust
   let selector = project_path.join(".harness").join("scripts").join("context_selector.py");
   ```

9. `rust/crates/cortex-dev/src/cmds/docsync.rs:58` and `:155` -> `doc-tiny/graphrag_ingest_langextract.py` [RUNTIME — every `dev sync doc` / `dev sync doc all`]
   ```rust
   let doc_ingestor = crate::util::repo_root().join("doc-tiny/graphrag_ingest_langextract.py");
   ```
   Spawn at `docsync.rs:156-159` builds `python <ingestor> …` with `harness_python(repo/doc-tiny)` (line 80). Hard-exits if missing (lines 59–62).

10. `rust/crates/cortex-dev/src/cmds/lifecycle.rs:93` -> `scripts/mcp-lifecycle.py` [RUNTIME — shim actions]
    ```rust
    let lifecycle = root.join("scripts").join("mcp-lifecycle.py");
    ```
    `run_lifecycle(action, …)` (lines ~90–110): used for actions not yet ported (`infra-up/infra-down/…`); `--provision` pass-through at `lifecycle.rs:293-294`.

11. `rust/crates/cortex-dev/src/cmds/mcp.rs:35,44` -> `mcp/unified_mcp.py` + `mcp_graph_rag.py` [RUNTIME — Python MCP backend]
    ```rust
    rel_cmd0: "mcp/unified_mcp.py",   // code-tiny, port 8788, server_flavor "unified"
    rel_cmd0: "mcp_graph_rag.py",     // doc-tiny, port 8789, server_flavor "mind"
    ```
    Spawn site `mcp.rs:410-560` (`mcp_start_one`, "spawn the legacy Python MCP server"): `Command::new(harness_python(&svc_dir)).arg(entry_script)…`, env = inherit + service `.env` + config overlay, log/pid sidecars in `.cache/mcp` (approx — log dir helper `mcp_log_dir()`).

12. `rust/crates/cortex-dev/src/cmds/mcp.rs:68-98` -> backend flip conditions [RUNTIME convention]
    ```rust
    "python" => (McpBackend::Python, "CORTEX_MCP_BACKEND=python (rollback flag)"...),
    "rust" => … None => … "falling back to python (build: cargo build --release -p cortex-mcp)"…,
    _ => auto: Rust if `cortex-mcp` binary exists else Python.
    ```

13. `rust/crates/cortex-dev/src/cmds/sync.rs:550-551` -> MCP pause/restart service map [RUNTIME]
    ```rust
    ("code", "code-tiny", "unified_mcp.py"),
    ("doc", "doc-tiny", "mcp_graph_rag.py"),
    ```
    Used by `SyncLifecycle` to stop/restart the Python MCP around sync runs (`Drop` impl restarts via `mcp_start_one`).

14. `rust/crates/cortex-dev/src/mcp_state.rs:20-25` -> process markers [RUNTIME — discovery]
    ```rust
    pub const MCP_PROCESS_MARKERS: [&str; 4] = [
        "code-tiny/mcp.sh", "doc-tiny/mcp.sh", "mcp/unified_mcp.py", "mcp_graph_rag.py",
    ];
    ```

15. `rust/crates/cortex-dev/src/procinfo.rs:91` -> `cortex_harness/dev.py` [RUNTIME — classification, not exec]
    ```rust
    let dev_script = crate::env::abspath(&root.join("cortex_harness").join("dev.py"));
    ```
    (`is_dev_sync`: matches argv entries equal to dev.py followed by `sync <owner>` — still recognizes legacy Python sync launchers in `ps` output.)

16. `rust/crates/cortex-dev/src/procinfo.rs:109-123` -> code worker classification [RUNTIME — classification]
    ```rust
    const CODE_WORKER_NAMES: [&str; 3] =
        ["build_owner_manifests.py", "clang_worker.py", "incremental_sync.py"];
    ...
    if CODE_WORKER_NAMES.contains(&name) || name.ends_with("_analyzer.py") {
    ```

17. `rust/crates/cortex-dev/src/procinfo.rs:130-137` -> doc worker classification [RUNTIME — classification]
    ```rust
    .join("graphrag_ingest_langextract.py"),
    ```

18. `rust/crates/cortex-dev/src/util.rs:16,22` -> `cortex_harness/dev.py` [RUNTIME-CRITICAL MARKER]
    ```rust
    if p.join("cortex_harness/dev.py").is_file() {
        return p;
    }
    ```
    `repo_root()` (env `CORTEX_HARNESS_REPO_ROOT` → cwd walk-up → `CARGO_MANIFEST_DIR` fallback) **uses the presence of `cortex_harness/dev.py` as the repo-root litmus test** for the whole cortex-dev binary. Deleting dev.py without changing this breaks every cortex-dev cwd-independent operation.

19. `rust/crates/cortex-dev/src/util.rs:41-66` -> `harness_python` [RUNTIME convention]
    ```rust
    pub fn harness_python(base_dir: &Path) -> String {
    ```
    Order: `<base>/.venv/Scripts/python.exe` → `<base>/bin/python` → repo `.venv/{Scripts/python.exe,bin/python}` → `"python3"`. Header comment: "Harness interpreter for the remaining FORCED-Python paths only (the lifecycle shim actions, `.harness` project scripts, torch device probe, journal-consumer recovery)".

20. `rust/crates/cortex-embed/src/sidecar.rs:70-76` -> `scripts/rust_mcp/embed_worker.py` [RUNTIME when `CORTEX_EMBED_BACKEND=python`]
    ```rust
    None => repo_root_from_manifest()
        .join("scripts").join("rust_mcp").join("embed_worker.py"),
    ```
    Env override `CORTEX_MCP_EMBED_WORKER`; interpreter via `python_binary()` (`sidecar.rs:86-99`): `CORTEX_EMBED_PYTHON` → `CORTEX_MCP_PYTHON` → `CORTEX_DOC_PYTHON` → repo `.venv/bin/python` → `python3`. Persistent NDJSON worker; single respawn on broken pipe/timeout (`sidecar.rs:160-197`).

21. `rust/crates/cortex-embed/src/backend.rs:31,118` -> backend gate [RUNTIME convention]
    ```rust
    pub const BACKEND_ENV: &str = "CORTEX_EMBED_BACKEND";
    ...
    Backend::Python => Ok(Box::new(SidecarEmbedder::new(
    ```
    (`lib.rs:8`: "`CORTEX_EMBED_BACKEND=python|onnx` (default `python` = rollback tức thì)" — per cutover-runbook the re-baseline makes `onnx` default for cortex-mcp mind lane; cortex-embed lib doc still says default python. See Risks.)

22. `rust/crates/cortex-mcp/src/mind/embed.rs:38-52` -> `scripts/rust_mcp/embed_worker.py` [RUNTIME — mind query embedding, python backend]
    ```rust
    let path = repo.join("scripts").join("rust_mcp").join("embed_worker.py");
    ```
    Env overrides `CORTEX_MCP_EMBED_WORKER`, `CORTEX_MCP_PYTHON`. Gate documented at `mind/embed.rs:2-8` ("`CORTEX_EMBED_BACKEND=python|onnx`… `python` (mặc định = Plan B phase-13, giữ nguyên để rollback tức thì)").

23. `rust/crates/cortex-mcp/src/vector_sidecar.rs:26-42` -> `scripts/rust_mcp/vector_worker.py` [RUNTIME — unconditional for local-Qdrant vector lane]
    ```rust
    let path = repo.join("scripts").join("rust_mcp").join("vector_worker.py");
    ```
    Env override `CORTEX_MCP_VECTOR_WORKER`; interpreter `CORTEX_MCP_PYTHON` → `.venv/bin/python` → `python3` (lines 44-56). Header: "ingest stays Python (spike 260914-1706 phase-05 NO-GO). This module talks NDJSON stdio to `scripts/rust_mcp/vector_worker.py`". Consumers: `cortex-mcp/src/graph/vector_lane.rs:143,168,209` and `cortex-mcp/src/mind/qdrant.rs:334` (`crate::vector_sidecar::search`).

24. `rust/crates/cortex-doc/src/providers.rs:153-160,184-186` -> GLiNER sidecar [RUNTIME — `cortex-doc` entity provider `gliner` (the DEFAULT, `docsync.rs:41` / `main.rs:462`)]
    ```rust
    const GLINER_SIDECAR: &str = r#"
    import json, sys
    from gliner import GLiNER
    ...
    let output = Command::new(python_binary())
        .arg("-c")
        .arg(GLINER_SIDECAR)
    ```
    NOTE: this is an **inline `python -c` script, NOT `scripts/rust_mcp/gliner_sidecar.py`**. Interpreter `providers.rs:44-58` `python_binary()`: `CORTEX_DOC_PYTHON` → repo `.venv/bin/python|venv/bin/python` → `python3`.

25. `rust/crates/cortex-doc/src/providers.rs:471-491` -> spaCy sidecar [RUNTIME — provider `spacy` with a model]
    ```rust
    const SPACY_SIDECAR: &str = r#"
    import json, sys
    import spacy
    ...
    let output = Command::new(python_binary())
        .arg("-c")
        .arg(SPACY_SIDECAR)
    ```
    (Ruler-only mode runs natively; only statistical models go through the sidecar.)

26. `rust/crates/cortex-storage/src/remote_probe.rs:369,379,441-446` -> `code-tiny/scripts/setup_constraints.py` [RUNTIME — remote provision]
    ```rust
    message: "setup_constraints.py not found".to_string(),
    ...
    let candidate = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../code-tiny/scripts/setup_constraints.py");
    ```
    Spawned at `remote_probe.rs:415` (`Command::new(&arguments[0]).args(...)` with interpreter = arg `--python-bin`-style value or `"python3"`, line 362) during FalkorDB graph provisioning — reached via `dev infra-up --provision` / `make infra-up INFRA_ARGS=--provision` (`lifecycle.rs:293-294` passes `--provision` to the lifecycle shim).

27. `rust/crates/cortex-sync/src/orchestrator.rs:2607-2609` -> ts analyzer resolution [RUNTIME — but both candidates post-flip]
    ```rust
    crate::registry::repo_root().join("code-tiny/tools/ts/ts_backend_analyzer.py")
    ...
    crate::registry::repo_root().join("code-tiny/tools/ts/ts_analyzer.py")
    ```
    `ts_backend_analyzer.py` is MISSING on disk; `ts_analyzer.py` EXISTS. Result feeds `AnalyzerConfig.script_path`, which post-phase-08 is never executed (see 1b).

#### 1b. Data-only references to Python analyzer entry points (DEAD-IN-SPAWN)

28. `rust/crates/cortex-sync/src/registry.rs:91-247` -> 24 primary + 12 overlay `script_path` values [DEAD-IN-SPAWN]
    Representative lines (all same shape):
    ```rust
    ("cobol", AnalyzerConfig::new("cobol", code_tiny_tools("cobol/cobol_analyzer.py"), true)),
    ("java",  AnalyzerConfig::new("java",  code_tiny_tools("java/java_analyzer.py"),  true)),
    ("python",AnalyzerConfig::new("python",code_tiny_tools("python/python_analyzer.py"), true)),
    ...
    script_path: code_tiny_tools("spring/spring_analyzer.py"),            // line 148
    script_path: code_tiny_tools("project_topology/topology_analyzer.py"),// line 139
    script_path: code_tiny_tools("web_framework/web_framework_analyzer.py"), // lines 211/220/229
    script_path: code_tiny_tools("database_schema/database_schema_analyzer.py"), // lines 238/247
    ```
    Existence check: only a minority of the referenced files still exist (`ts/ts_analyzer.py`); `java/…`, `cobol/…`, `python/…`, `go/…`, `perl/…`, `rust/…`, `swift/…`, `js/…`, `php/…`, `sql/…`, `plsql/…`, `delphi/…`, `shell/…`, `android/…`, `kotlin/…`, `vb/vb*_analyzer.py`, `flutter/…`, `spring/…`, `servlet_jsp/…`, `mybatis/…`, `struts/…`, `aspnet_*/…`, `web_framework/…`, `database_schema/…`, `project_topology/topology_analyzer.py`, `csharp/csharp_analyzer.py`, `cplus/cplus_analyzer.py` are all MISSING. The trees `csharp/`, `jp1/`, `project_topology/`, `vb/`, `cplus/` still hold *support* modules (`models.py`, `roslyn_integration.py`, `sniff.py`, `pipeline.py`, `clang_worker.py`, …).

    Why dead: `registry.rs:553-563` `build_analyzer_cmd` only falls back to `script_path` when `rust_analyzer_binary()` returns `Ok(None)`, but the phase-08 flip matrix (`registry.rs:349-427`) makes `Ok(None)` unreachable:
    ```rust
    const AUTO_FLIP_DEFAULT: bool = true;   // registry.rs:355
    ...
    if !mode.is_empty() && mode != "rust" {
        return Err(retire_hint(&analyzer.parser));   // "Python analyzer plane retired at commit …"
    ```
    Also `orchestrator.rs:2077` pins `config.force_python = false` for the whole embedding pass. Conclusion: `script_path` fields + `python_bin` plumbing are rollback-only data; **no live call path spawns `*_analyzer.py`**. The files themselves are already gone for nearly every parser.

29. `rust/crates/cortex-sync/src/registry.rs:21` -> marker file [MARKER]
    ```rust
    && repo.join("code-tiny/tools/sync/incremental_sync.py").is_file() {
    ```
    `cortex-sync`'s own `repo_root()` (exe walk-up) validates the repo by the presence of `incremental_sync.py`. Deleting that file silently degrades root resolution to `PathBuf::from(".")` unless `CORTEX_REPO_ROOT` is set.

#### 1c. Rust test-code references

30. `rust/crates/cortex-sync/tests/registry_tests.rs:73,317` -> synthetic `/tools/{parser}/{parser}_analyzer.py` [PARITY/test fixture strings]
    ```rust
    script_path: format!("/tools/{parser}/{parser}_analyzer.py"),
    ...
    assert_vec_str(&cmd.args[0..1], &["/tools/java/java_analyzer.py"]);
    ```

31. `rust/crates/cortex-sync/tests/message_scan_parity.rs:95` -> `broker.py` [PARITY/test fixture content — writes a fake Python source file to scan, not a spawn]
    ```rust
    fs::write(py_dir.join("broker.py"), r#"class Broker: …
    ```

32. `rust/crates/cortex-mcp/tests/python_parity.rs:22` -> `scripts/rust_mcp/generate_data.py` [PARITY]
    ```rust
    .unwrap_or_else(|error| panic!("fixture {name} missing (run scripts/rust_mcp/generate_data.py): {error}"));
    ```

33. `rust/crates/cortex-embed/tests/embed_golden.rs:6,258-259` -> parity fixture/bench scripts [PARITY]
    ```rust
    //! thêm test. Fixture sinh bởi `scripts/rust_parity/gen_embed_fixtures.py`.
    ...
    // định P05. Phía Python tương ứng: `bench_embed_python.py` (code plane) và
    // `bench_mind_worker.py` (đường sidecar persistent của mind tools).
    ```

34. `rust/crates/cortex-storage/tests/parity.rs:508-544` -> `"a.py"/"b.py"` payload literals [test data, not references] — listed for completeness, exclude from cleanup decisions.

35. `rust/crates/cortex-mcp/src/dispatch.rs:402`, `cortex-mcp/src/mind/mod.rs:61` -> `https://errors.pydantic.dev/...` [DOC-STALE hint text inside error messages about the retired Python server's pydantic errors — cosmetic].

#### 1d. Port-provenance doc comments in Rust (DOC-STALE in-place)

~300 doc-comment/`//` lines across the workspace say "Port `tools/<x>/<y>.py`" and reference the deleted Python analyzer tree. They are provenance annotations, not invocations. Per-file hit counts (grep `\.py` in `*.rs`, excluding target/): cortex-sync/src/registry.rs 39, cortex-dev/src/cmds/sync.rs 17, cortex-dev/src/env.rs 15, cortex-sync/src/vector_sync.rs 13, cortex-dev/src/procinfo.rs 13, cortex-dev/src/mcp_state.rs 11, cortex-dev/src/cmds/mcp.rs 11, cortex-dev/src/cmds/lifecycle.rs 11, cortex-analyzer-framework/src/scan.rs 10, cortex-dev/src/util.rs 9, cortex-sync/src/orchestrator.rs 8, cortex-mcp/src/server.rs 8, cortex-dev/src/journalx.rs 8, cortex-dev/src/cmds/harness.rs 8, cortex-mcp/src/lib.rs 7, cortex-dev/src/db_transfer.rs 6, and 3–5 each across analyzer-* crates (every analyzer crate's module headers, e.g. `analyzer-topology/src/registry.rs:1` `//! Port tools/project_topology/registry.py …`). Representative quote:
```rust
// rust/crates/analyzer-topology/src/registry.rs:1
//! Port `tools/project_topology/registry.py` — descriptor spec registry với
```
Also `rust/crates/cortex-mcp/src/mind/qdrant.rs:2` (the anchor in the brief): `//! qdrant_search_entity_payload plumbing of doc-tiny/mcp_graph_rag.py.` — doc comment only; the actual gliner reference from the brief is WRONG: there is **no** `gliner_sidecar.py` spawn in cortex-mcp/mind; the live Rust GLiNER path is the inline `-c` sidecar in cortex-doc (finding 24), and `scripts/rust_mcp/gliner_sidecar.py` is spawned only by the parity tool `scripts/rust_mcp/compare_mind.py:293`.

---

### Category 2 — Build / entry surfaces

36. `Makefile:2,6` -> `PYTHON ?= … .venv/…python…` [RUNTIME build convention]
    ```make
    PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
    PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
    ```

37. `Makefile:61` -> dependency install for all remaining Python planes [RUNTIME]
    ```make
    $(UV) pip install --python $(PYTHON) -r requirements.txt -r code-tiny/requirements.txt -r doc-tiny/requirements.txt -e .
    ```
    (installs root package editable → makes `cortex_harness/dev.py` importable for tests/parity; installs code-tiny + doc-tiny requirements).

38. `Makefile:180` -> `$(PYTHON) scripts/rust_parity/export_jina_onnx.py --verify` [PARITY — `make embed-jina-onnx`; ONNX artifact generation]

39. `Makefile:183` -> `$(PYTHON) scripts/rust_parity/fetch_bge_onnx.py` [PARITY — `make embed-bge-onnx`]

40. `Makefile:188` -> `$(PYTHON) scripts/rust_parity/gen_embed_fixtures.py --limit 500` [PARITY — `make embed-parity`]

41. `Makefile:194` -> `bash $(PARITY_DIR)/build_pyo3.sh` [PARITY — `make rust-pyo3`; script itself runs `.venv/bin/python scripts/rust_parity/test_pyo3_parity.py` at `scripts/rust_parity/build_pyo3.sh:23`]

42. `Makefile:199-203` -> fixture generators [PARITY — `make rust-fixtures`]
    ```make
    $(UV) run --no-project --with rank_bm25==0.2.2 python $(PARITY_DIR)/gen_bm25_fixtures.py
    $(UV) run --no-project --with rank_bm25==0.2.2 python $(PARITY_DIR)/gen_fusion_fixtures.py
    $(UV) run --no-project python $(PARITY_DIR)/gen_phase03_fixtures.py
    $(UV) run --no-project python $(PARITY_DIR)/gen_phase05_fixtures.py
    $(UV) run --no-project python $(PARITY_DIR)/gen_journal_scenario.py
    ```

43. `Makefile:223` -> `$(PYTHON) $(PARITY_DIR)/diff_journal_stores.py …` [PARITY — `make journal-shadow-diff`]

44. `dev.sh:1-15`, `dev.bat`, `dev.ps1`, `dev-global.cmd` -> **binary-only; zero .py references** [n/a — evidence: full file reads; comments say "binary-only (phase-06 cutover; no Python rollback)"].

45. `installers/windows/scripts/wrapper.bat` -> binary-only in the live path, but retains an **unreachable post-`exit /b` block** referencing `%PYTHON_EXE% "%DEV_MODULE%"` [INSTALLER, dead text]
    ```bat
    REM Binary-only entry (phase-06): exec cortex-dev.exe per D1 resolution.
    if exist "%CORTEX_DIR%\rust\target\release\cortex-dev.exe" ( … exit /b %ERRORLEVEL% )
    ...
    "%PYTHON_EXE%" "%DEV_MODULE%" %ACTION% --project "%FOLDER_PATH%"
    ```

46. `pyproject.toml:9,43-52` -> Python packaging surface [RUNTIME-test infra]
    ```toml
    requires-python = ">=3.12"
    [tool.pytest.ini_options]
    pythonpath = ["code-tiny", "doc-tiny"]
    [tool.setuptools.packages.find]
    include = ["cortex_harness*"]
    ```
    `make update` `-e .` editable-install keeps this alive. No `[project.scripts]` (bridge-ban test enforces that; see 47).

47. `conftest.py:1-17` (repo root) -> phase-08 retired-module stub installer [RUNTIME test infra]
    ```python
    from cortex_harness._phase08_skip import install_stubs
    install_stubs()
    ```

48. `install-windows.bat:9,31,37,46,92` and `install-windows.ps1:40` -> ambient python for venv/torch bootstrap [INSTALLER]
    ```bat
    python --version
    python -m venv .venv
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
    ```
    No .py file spawns; provisions the venv that `harness_python` later resolves.

49. Scoop shim definitions: **none found** (`find installers scripts -name '*scoop*'` → empty; cutover-runbook.md:5 mentions "scoop shim" historically, but no scoop manifest exists in the repo today) — `unknown`: whether scoop shims are generated externally at release time.

### Category 3 — CI (`.github/workflows/*.yml`)

Only two workflows exist.

50. `.github/workflows/lifecycle-macos.yml:11-15,23-27` -> path triggers on `cortex_harness/dev.py`, `scripts/mcp-lifecycle.py`, `tests/test_dev_lifecycle_commands.py`, `tests/test_make_lifecycle.py` [RUNTIME CI]

51. `.github/workflows/lifecycle-macos.yml:80` -> [RUNTIME CI]
    ```yaml
    run: python -m unittest tests.test_make_lifecycle tests.test_dev_lifecycle_commands tests.test_rust_bridge_ban tests.test_embedded_discovery_parity -v
    ```

52. `.github/workflows/lifecycle-macos.yml:103,107,110` -> [RUNTIME CI]
    ```yaml
    'python-dotenv>=1.2.2' 'portalocker>=3.2,<4' click pytest pytest-asyncio pandas
    uv run python -m pytest code-tiny/tests/test_ladybug_driver_local.py tests/test_ladybug_provider_plumbing.py -v
    uv run python -m pytest tests/test_parity_ladybug.py -m ladybug -v
    ```

53. `.github/workflows/cobol-macos.yml:10-11,19-20` -> triggers on `scripts/rust_parity/analyzer_parity_cobol.py`, `tests/test_cobol*.py` [PARITY CI]

54. `.github/workflows/cobol-macos.yml:53-54` -> [PARITY CI]
    ```yaml
    python -m pip install --upgrade pip
    python -m pip install -e . "tree-sitter>=0.25,<0.26" "tree-sitter-language-pack>=1.12,<2"
    ```

55. `.github/workflows/cobol-macos.yml:64,66` -> [PARITY CI]
    ```yaml
    run: .venv/bin/python scripts/rust_parity/analyzer_parity_cobol.py
    run: python -m unittest discover -s tests -p 'test_cobol*.py' -v
    ```

### Category 4 — `installers/**`

56. `installers/common/config_manager.py` + `installers/windows/registry_manager.py` -> callers [INSTALLER]
    Sole live caller: `cortex_harness/dev.py` `dev installer install` command:
    - `cortex_harness/dev.py:4802-4803` and `:4957-4958`:
      ```python
      from windows.registry_manager import WindowsRegistryManager
      from common.config_manager import ContextMenuConfig
      ```
    Dev-only invocation documented in `installers/README.md:80-86,195`:
    ```
    python -m installers.common.config_manager show
    python -m installers.common.config_manager init
    python -m installers.common.config_manager add "New Command" "harness run --custom"
    ```
    NOTE: there is no Python entrypoint anymore to reach `dev installer install` except manually (`python -m cortex_harness.dev …` is not documented anywhere live); classify INSTALLER/dev-only, `inferred` reachability.

57. `installers/windows/inno_setup/cortex_harness.iss:65-69` -> packs Python trees into the app dir [INSTALLER]
    ```ini
    Source: "..\..\cortex_harness\*"; DestDir: "{app}"; … Excludes: "*.pyc,__pycache__"
    Source: "..\..\cli\*"; DestDir: "{app}\cli"; …            ; NOTE: no top-level cli/ dir exists in repo — `inferred` stale
    Source: "..\..\code-tiny\*"; DestDir: "{app}\code-tiny"; …
    Source: "..\..\doc-tiny\*"; DestDir: "{app}\doc-tiny"; …
    Source: "..\..\harness\*"; DestDir: "{app}\harness"; …
    ```
    All shortcuts/context-menu commands in the .iss run `rust\target\release\cortex-dev.exe` (lines 83-89, 100-138) — no python execution. But shipping `code-tiny/`+`doc-tiny/` is REQUIRED while the forced-Python paths remain (delegation target, doc ingestor, harness scripts).

58. `installers/macos/build_pkg.sh`, `installers/ubuntu/build_deb.sh` -> copy `.workflow`/nautilus `.sh` scripts only; no .py references found [INSTALLER, no py hits — verified by full read].

### Category 5 — Shell / launcher scripts

59. `code-tiny/mcp.sh:34` -> `python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp "$@"` [RUNTIME — manual dev launcher; also asserted by `tests/test_dev_lifecycle_commands.py:195`]
    Lines 5-11 are commented-out historical invocations (`fastmcp_server.py`, `android_mcp.py`, `cplus_mcp.py`) [DOC-STALE].

60. `code-tiny/run_mcp.sh:17,60,91-100` -> `PYTHON_BIN` + script dispatch to `mcp/fastmcp_server.py`, `mcp/java/java_mcp.py`, `mcp/cplus/cplus_mcp.py`, `mcp/android/android_mcp.py` [RUNTIME — manual launcher; `java/java_mcp.py`, `android/…`, `cplus/…` existence `unknown`/`inferred` — `fastmcp_server.py` and the java/cplus/android subdirs exist under code-tiny/mcp/ per directory listing]

61. `doc-tiny/mcp.sh:20` -> `python mcp_graph_rag.py --host 127.0.0.1 --port 8789 --transport streamable-http --path /mcp "$@"` [RUNTIME — manual dev launcher]

62. `harness/scripts/init.sh:6` -> `REQUIRED_CMDS=(python3)` [RUNTIME — `.harness` project bootstrap requires python3]

63. `harness/scripts/verify.sh` -> no .py refs [verified].

64. `scripts/rust_parity/build_pyo3.sh:23` -> `"$REPO/.venv/bin/python" "$REPO/scripts/rust_parity/test_pyo3_parity.py"` [PARITY]

65. `scripts/rust_mcp/compare_mind.py:293` -> spawns sibling `gliner_sidecar.py` [PARITY]
    ```python
    str(Path(__file__).resolve().parent / "gliner_sidecar.py"),
    ```

### Category 6 — Docs that instruct usage

66. `ReadMe.md:100-106` -> authoritative forced-Python list [RUNTIME doc]
    ```
    The Python source tree remains only as the parity reference for
    `scripts/rust_parity/` plus a small documented forced list (torch device
    probe, `.harness` project scripts, the doc-tiny ingestor and the
    required-journal consumer until their Rust ports land).
    ```
    Also `ReadMe.md:372,382` torch install commands (RUNTIME env setup). No `python file.py` invocations otherwise [verified].

67. `docs/cutover-runbook.md` -> the governing cutover contract:
    - line 8: "entrypoint không còn trỏ `dev.py`" [DOC-STALE re: dev.py as entrypoint]
    - lines 14-16: "`dev.py` còn trong repo chỉ là **parity reference** (phục vụ `scripts/rust_parity/dev_cli_parity.py`), không entrypoint nào gọi nó" [RUNTIME doc]
    - lines 176-184: grep gate — "`_analyzer.py` chỉ tồn tại trong 2 file: `code-tiny/tools/sync/incremental_sync.py` … `rust/crates/cortex-sync/src/registry.rs` … Script `phase07_composition_parity.py` enforce gate này" [PARITY]
    - lines 208-212: flip matrix — `CORTEX_RUST_ANALYZER=python` (hoặc khác) → "Loud 'retired' error … KHÔNG silent fallback" [RUNTIME convention]
    - lines 261-263: "Giữ lại vĩnh viễn: `code-tiny/tools/sync/incremental_sync.py` (orchestrator sync, vẫn là entry `dev sync code`), doc-tiny pipeline tới khi port xong" [RUNTIME doc — NOTE: partially superseded: `dev sync code` now runs cortex-sync binary; incremental_sync.py remains only the delegation target]

68. `docs/UNIFIED_INGEST_QUERY_CONTRACT.md:200` -> [DOC-STALE]
    ```
    Both `cortex_harness/dev.py mcp start` and `scripts/mcp-lifecycle.py start`
    ```
    `:244` -> [RUNTIME contract check]
    ```
    python scripts/smoke_unified_contract.py --project-a cortext --project-b proj_beta
    ```
    `:272-274` -> [RUNTIME ops for remote graphs]
    ```
    python code-tiny/scripts/setup_constraints.py --project-id <id>
    ```

69. `docs/mcp-output-contract.md:375-377` -> [DOC-STALE — instructs running the retired entrypoint]
    ```
    .venv/bin/python cortex_harness/dev.py mcp start \
      --force-restart \
    .venv/bin/python scripts/mcp-lifecycle.py doctor
    ```

70. `docs/HARNESS_WORKFLOW.md:42-43,134-135` -> documents `.harness/scripts/{context_selector.py,orchestrator.py}` layout [RUNTIME doc — matches `dev harness init/run/context`]

71. `docs/PROJECT_ID_QUERY_RULES.md:42` -> lists ops helpers [RUNTIME ops]
    ```
    - `code-tiny/scripts/backfill_project_scope_keys.py`, `doc-tiny/graphrag_ingest_langextract.py`, `doc-tiny/0_reset_all.py`
    ```

72. `code-tiny/README.md:23-46` -> parser table mapping every parser to `tools/<x>/<x>_analyzer.py` [DOC-STALE — entry files deleted; also `:159` `python tools/sync/incremental_sync.py …` (RUNTIME manual path), `:317` `python -m tools.python.python_analyzer …` (DOC-STALE, file gone), `:458-461` `python mcp/unified_mcp.py …` (RUNTIME manual path)]

73. `doc-tiny/Readme.md:84-92` -> `python mcp_graph_rag.py …` [RUNTIME manual]; `:285-445` many `python graphrag_ingest_langextract.py …` + `:383` `python 0_reset_all.py --neo4j-pass …` [RUNTIME manual ops]

74. `INSTALLER_GUIDE.md` -> no .py invocation lines [verified; references config_manager/registry_manager as file inventory only].

75. `wiki/**` -> 146 files contain `.py` references describing the pre-migration Python CLI (`python -m cortex_harness.dev …` era) [DOC-STALE wholesale; generated wiki, not instructions for current runtime].

76. `skills/**` -> directory does not exist in this repo [n/a].

77. `code-tiny/CLAUDE.md`, `doc-tiny/CLAUDE.md` -> no .py invocation lines found in grep [verified; `code-tiny/CLAUDE.md` exists, `doc-tiny/CLAUDE.md` exists; `inferred` — only checked by grep for `.py`, no hits].

### Category 7 — Python-side live spawn/import graph (who executes which .py)

78. `code-tiny/tools/sync/incremental_sync.py` (delegation target) [RUNTIME]
    - Its own registry still lists all `tools/<x>/<x>_analyzer.py` script paths (`:127-152`, `:161-240`, `:362-363`) — same DEAD-IN-SPAWN status as Rust: `:1536` documents "`python` hoặc giá trị khác → raises ``_RetiredError`` immediately"; `:1448` maps parsers to Rust binaries.
    - imports the journal consumer **in-process** (`:90` `from tools.graph.journal.consumer import resume_journal`) — the standalone `python -m tools.graph.journal.consumer` spawn lives only in Rust (`journalx.rs:334`).
    - `:4111` `parser.add_argument("--python-bin", default=sys.executable)`.
    - Spawned by: `cortex-sync/src/orchestrator.rs:2935` (delegation), `code-tiny/README.md:159` (manual).

79. `code-tiny/mcp/unified_mcp.py` [RUNTIME python MCP backend] — no subprocess/.py spawns found (embedding is in-process); spawned by `cortex-dev/src/cmds/mcp.rs:520` (backend=python), `code-tiny/mcp.sh:34` (manual), `code-tiny/run_mcp.sh` (fastmcp/java/cplus/android variants), `SyncLifecycle` restart (`cmds/sync.rs`), `Makefile` start flows via dev.

80. `doc-tiny/mcp_graph_rag.py` [RUNTIME python MCP backend] — in-process `sentence_transformers` embedding (`:13-15,129-134`); no .py subprocess spawns; spawned by `cmds/mcp.rs:520` and `doc-tiny/mcp.sh:20`.

81. `doc-tiny/graphrag_ingest_langextract.py` [RUNTIME — `dev sync doc`] — spawned by cortex-dev (`docsync.rs:156-159`); imports doc-tiny modules (`embedding_utils.py`, `entity_extractors.py`, `graph_store.py`, `neo4j_loader.py`, `model.py`, `project_contract.py`, `enviroment_loader.py`, `doc_local_qdrant.py` — all exist). GLiNER used **in-process** here (`entity_extractors.py:214-216` `from gliner import GLiNER`), NOT via `gliner_sidecar.py`.

82. `code-tiny/scripts/setup_constraints.py` [RUNTIME — remote provision] — spawned by `cortex-storage/src/remote_probe.rs:415`; also documented for manual ops (finding 68).

83. `scripts/mcp-lifecycle.py` [RUNTIME — lifecycle shim] — spawned by `cortex-dev/src/cmds/lifecycle.rs:93-107` for `infra-up`/`infra-down`/legacy actions; internally (per `docs/logs/2026-08-18-infra-up-remote-support.md:12`) it orchestrates `code-tiny/scripts/setup_constraints.py` for remote provisioning.

84. `harness/scripts/orchestrator.py`, `context_selector.py` [RUNTIME — `dev harness run`/`context`] — copied per-project by `dev harness init`, executed via `harness_python`.

85. `tools.graph.journal.consumer` (`code-tiny/tools/graph/journal/consumer.py`, EXISTS) [RUNTIME — required journal lane] — subprocess entry (`consumer.py:1` "including a subprocess entry point") spawned only by `journalx.rs:333-334`; `resume_journal` also imported in-process by `code-tiny/tools/graph/cli.py:326,352,396` and `incremental_sync.py:90`.

86. `scripts/rust_mcp/embed_worker.py` [RUNTIME — embed backend `python`] — spawned by `cortex-embed/src/sidecar.rs` and `cortex-mcp/src/mind/embed.rs`.

87. `scripts/rust_mcp/vector_worker.py` [RUNTIME — local Qdrant lane] — spawned by `cortex-mcp/src/vector_sidecar.rs:40`; consumed by `graph/vector_lane.rs` + `mind/qdrant.rs:334`.

88. `scripts/rust_mcp/gliner_sidecar.py` [PARITY] — spawned only by `scripts/rust_mcp/compare_mind.py:293`.

89. `cortex_harness/dev.py` [PARITY + test infra; MARKER for repo-root resolution] — spawned by `scripts/rust_parity/dev_cli_parity.py:46,620` (parity gate) and covered by CI unittests (`tests.test_dev_lifecycle_commands`, `tests.test_make_lifecycle`, `tests.test_rust_bridge_ban`). Its `installer` command group (`dev.py:4608-5010` approx) imports `installers/windows/registry_manager.py` + `installers/common/config_manager.py`. **Critical side role**: it is the existence marker for `cortex-dev::repo_root()` (finding 18) and `procinfo` matching (finding 15).

90. `tests/` (176 .py files) [RUNTIME CI] — notable live bindings:
    - `tests/test_rust_bridge_ban.py:26-56` — bans `pyexec`/`venv_python`/`HELPER_SRC` in cortex-dev sources AND asserts entrypoints (`dev.sh/bat/ps1`, `dev-global.cmd`, `wrapper.bat`, `Makefile`) contain no `dev.py` substring.
    - `tests/test_dev_lifecycle_commands.py:147-195` — "The 10 entrypoint artifacts must not route to dev.py"; asserts `code-tiny/mcp.sh` contains `MSYS_NO_PATHCONV=1 python`.
    - CI-selected: `tests/test_make_lifecycle.py`, `tests/test_dev_lifecycle_commands.py`, `tests/test_rust_bridge_ban.py`, `tests/test_embedded_discovery_parity.py`, `tests/test_ladybug_provider_plumbing.py`, `tests/test_parity_ladybug.py`, `tests/test_cobol*.py`, `code-tiny/tests/test_ladybug_driver_local.py`.

---

## DELEGATE_SENTINEL — full map

**Definition**: `rust/crates/cortex-sync/src/orchestrator.rs:31`
```rust
/// Sentinel prefix marking Python-plane delegation.
const DELEGATE_SENTINEL: &str = "__delegate__";
```

**Emitters** (all inside cortex-sync `run_flow`; error strings returned from the main-line flow are inspected at `orchestrator.rs:342`):
1. `orchestrator.rs:1226` — reason string: `"required graph journal lane (SQLite store, resume/finalize) is Python-plane"`.
   Exact condition (`orchestrator.rs:1217-1225`):
   ```rust
   let setup_is_required = journalenv::REQUIRED_MODES.contains(&configured_journal_mode.as_str())
       || (configured_journal_mode.is_empty()
           && parser_filter.contains("cplus")
           && !cplus_changed.is_empty());
   ```
   with `REQUIRED_MODES = ["required", "shared-required"]` (`cortex-sync/src/journalenv.rs:26`).
   That is the **only** emitter in the repo. Exhaustive grep `DELEGATE_SENTINEL|__delegate__` across `rust/` + `code-tiny/` returns: definition (31), doc mentions (611, journalx.rs:304), consumer (342), emitter (1226). The Python side (`code-tiny/tools/sync/incremental_sync.py`) does **not** know the sentinel — delegation is strictly Rust→Python, one-way.

**Consumer** → `delegate_to_python` (`orchestrator.rs:2934-2949`): prints `[cortex-sync] python-plane delegation: {reason}`, resolves `code-tiny/tools/sync/incremental_sync.py` under `registry::repo_root()`, re-execs original argv through `cli::resolve_python_bin` (`--python-bin` → `CORTEX_SYNC_PYTHON_BIN` → `<exe>/../../../.venv/bin/python` → `python3`), returns child exit code (1 on spawn error).

**Downstream interaction with cortex-dev**: when `CORTEX_GRAPH_JOURNAL_MODE` ∈ required modes, `dev sync code` first runs the pre-attempt recovery `journalx::recover_required_lane` (`cmds/sync.rs:344-345` → `journalx.rs:306-343`), spawning `python -m tools.graph.journal.consumer` (cwd `code-tiny`, PYTHONPATH prepended); a non-zero rc aborts before cortex-sync runs. Then cortex-sync itself immediately delegates the whole run to Python via the sentinel. Net effect: with required journal mode, the code-sync lane is fully Python today.

---

## Conventions to follow

1. **Python interpreter resolution (3 distinct chains)** — a cleanup must preserve each:
   - `cortex-dev::util::harness_python` (util.rs:41): project venv (`{base}/.venv/Scripts/python.exe`, `{base}/bin/python`) → repo `.venv` → `python3`. Used for: mcp-lifecycle shim, `.harness` scripts, doc ingestor, journal-consumer recovery.
   - `cortex-doc::providers::python_binary` / `cortex-mcp` `python_binary` / `cortex-embed sidecar::python_binary`: `CORTEX_DOC_PYTHON` / `CORTEX_MCP_PYTHON` / (`CORTEX_EMBED_PYTHON` → `CORTEX_MCP_PYTHON` → `CORTEX_DOC_PYTHON`) → repo `.venv/bin/python` → `python3`.
   - `cortex-sync::cli::resolve_python_bin`: `--python-bin` flag → `CORTEX_SYNC_PYTHON_BIN` → `<exe>/../../../.venv/bin/python` → `python3`.
2. **Repo-root detection by Python marker files**: cortex-dev keys on `cortex_harness/dev.py` (util.rs:16,22); cortex-sync keys on `code-tiny/tools/sync/incremental_sync.py` (registry.rs:21) with `CORTEX_REPO_ROOT`/`CORTEX_HARNESS_REPO_ROOT` env escapes. Deleting these files without swapping the marker breaks binary pathing silently.
3. **Fallback flags**: `CORTEX_MCP_BACKEND=python` forces Python MCP; `=rust` with missing binary falls back to Python *with a loud warning*; unset = auto (Rust if binary exists). `CORTEX_EMBED_BACKEND=python` forces `embed_worker.py` sidecar; `onnx` removes the spawn (mind lane re-baselined to onnx default per cutover-runbook §env table, while cortex-embed lib.rs doc still claims default python — see Risks). `CORTEX_RUST_ANALYZER`: unset=`rust-if-binary` else retire error; `=rust`=error+build hint; `=python`/other=retire error. `CORTEX_GRAPH_JOURNAL_MODE` ∈ {required, shared-required} ⇒ whole code-sync runs Python-plane.
4. **Retire-error phrasing is contract**: `retire_hint` text ("Python analyzer plane retired at commit …; rollback = `git revert …`") must stay in sync with `incremental_sync.py`'s `_RetiredError` message per cutover-runbook §5.1.3.
5. **Grep gates already in CI**: no `dev.py` substring in entrypoints (test_rust_bridge_ban), no `_analyzer.py` refs outside `incremental_sync.py` + `registry.rs` (phase07 gate, enforced by `scripts/rust_parity/phase07_composition_parity.py`).

## Risks / open questions

1. **`cortex_harness/dev.py` is a load-bearing marker** for `repo_root()` in cortex-dev (util.rs:16,22). Any cleanup that deletes/moves it must first replace the marker (e.g. `cortex_harness/CARGO_ROOT`) — otherwise every binary invocation with a non-repo cwd fails. Evidence: quoted lines. `inferred` impact beyond cortex-dev: procinfo matching (procinfo.rs:91) degrades gracefully.
2. **`code-tiny/tools/sync/incremental_sync.py` is both marker and live delegation target** (journal-required lanes; cplus-changed auto-delegation). It cannot be deleted until the SQLite journal store/resume/finalize gets a Rust port AND the cplus auto-delegate condition is removed.
3. **`registry.rs script_path` entries point mostly at nonexistent files** — they are rollback-only data today (spawn path provably unreachable), so the cleanup can either delete the fields + `build_analyzer_cmd` python branch + `resolve_python_bin`, or keep them per the "keep rollback parsing (cheap, harmless)" doctrine in cutover-runbook §5.2.3. Decision needed from planner.
4. **CONTRADICTION to resolve**: `cortex-embed/src/lib.rs:8` says default embed backend is `python`, while `docs/cutover-runbook.md:36` says default from vector-lane re-baseline phase-02 is onnx (`CORTEX_EMBED_BACKEND=python` is the rollback flag) and `cortex-mcp/src/mind/embed.rs:2-8` also calls python "mặc định = Plan B phase-13". Actual default selected at runtime: `unknown` from static reading — `backend.rs:31` + selection function not fully traced here; flagged for the planner (affects whether `embed_worker.py` is default-live or rollback-only).
5. **`installers/windows/inno_setup/cortex_harness.iss` packs `cli\*` which does not exist** in the repo (line 66) — the .iss is `inferred` stale/broken for current tree; also packs `cortex_harness\*` + `code-tiny\*` + `doc-tiny\*` + `harness\*`, so installer-size cleanup depends on the Python cleanup decisions. `dev installer install/installer build` in dev.py is only reachable manually (no entrypoint routes to dev.py) — `inferred`.
6. **`code-tiny/mcp.sh` / `run_mcp.sh` / `doc-tiny/mcp.sh` are live manual launchers** asserted by tests (`test_dev_lifecycle_commands.py:195`); the `fastmcp_server.py`/`java_mcp.py`/`cplus_mcp.py`/`android_mcp.py` variants in `run_mcp.sh` were not individually existence-checked — `unknown`.
7. **`wiki/**` (146 files)** and `code-tiny/README.md:23-46` parser tables instruct running deleted analyzers — DOC-STALE wholesale, but `code-tiny/README.md` ships inside the Windows installer (`*.md` glob, iss line 76).
8. **PyO3 surface**: `cortex-retrieval-py` crate + `scripts/rust_parity/cortex_retrieval_py.so` + `build_pyo3.sh` are PARITY-only today (`make rust-pyo3`); the Makefile comment says the PyO3 extension module needs undefined-symbol link flags — keep in sync if parity targets are pruned.
9. **`tests/` corpus**: 176 test files, many still exercise Python-plane behavior through stubs (`conftest.py` phase-08 stubs). Cleanup of `code-tiny/tools/{csharp,jp1,project_topology,vb,cplus}` support modules will require re-auditing which tests skip loudly vs. break at import.
10. **`docs/UNIFIED_INGEST_QUERY_CONTRACT.md:200` and `docs/mcp-output-contract.md:375` still instruct `cortex_harness/dev.py mcp start`** — DOC-STALE that contradicts the binary-only cutover; should be rewritten or marked during cleanup.
