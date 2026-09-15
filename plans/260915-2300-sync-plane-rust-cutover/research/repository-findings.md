# Repository findings — sync-plane Rust cutover (research, 2026-09-15, branch `feat/change-db`)

Evidence-backed digest for planning the full Rust port of the Python sync plane, enabling deletion of `code-tiny/tools/sync/incremental_sync.py` + closure. All paths relative to repo root unless absolute.

---

## Q1. RUST SIDE — what already exists

### 1.1 cortex-sync orchestrator: native vs delegated

Native today (all in `rust/crates/cortex-sync/src/orchestrator.rs`):

- **CLI/summary skeleton**: `run_incremental` (orchestrator.rs:250) — run_id/correlation ids (:252-253), project_id/name derivation (:256-264), cache-dir/scope resolution (:265-266), summary path default (:282-297), `initial_summary` with the full Python-parity key set (`services`, `diff`, `parsers`, `component_failures`, `scope`, `lock`, `change_sources`, `reconciliation`, `parse_quality`) (orchestrator.rs:459-535); final `run_result` reliability artifact (orchestrator.rs:434-450) mirrors Python `tools.common.reliability`.
- **Locks**: `ProjectRunLock` flock + metadata blob, portalocker-compatible (`rust/crates/cortex-sync/src/syncscope.rs`; used orchestrator.rs:652-687; lock-busy exit code 2 at :672).
- **State**: load/legacy-fallback/migration-detection/backup (`state.rs`), mark_dirty-on-failure (`mark_dirty_on_failure` orchestrator.rs:537-598).
- **Change detection**: git scopes/submodules (`gitdiff.rs`), hybrid/committed/hash candidates, inventory diff + recovery/dirty replay, bootstrap full-scan decision (orchestrator.rs:753-807, 822-1203).
- **Inventory**: `inventory.rs` capture/diff/validate/write generations (orchestrator.rs:1086-1202, 1253-1310, 2339-2368).
- **Impact expansion**: three Cypher relationship-expand queries (orchestrator.rs:1314-1338 → `graphops::query_impacted_files`).
- **Parser routing**: `routing::group_paths_by_parser`, framework overlay detection `frameworks.rs`, topology bootstrap probe (orchestrator.rs:1340-1399, 1360-1382).
- **Analyzer child spawning**: `registry::build_analyzer_cmd` + `run_child` with build-commit handshake (orchestrator.rs:81-230, 1454-1678 primary; 1682-1867 framework; 1869-2012 topology overlay).
- **Message-scan lane (native)**: `run_native_message_scan_lane` (orchestrator.rs:2294-2313, 2969-3227) — cleanup → collect → `Message`/`MessageEndpoint` upsert → artifact, per parser; works on `FalkorDbStore` only (provider gate at :3058-3070).
- **Vector lane (native, phase-06)**: embedding pass orchestrator-side embed+upsert via `cortex_embed` + `cortex_storage::qdrant_remote` (`finish_native_embedding_pass` orchestrator.rs:2632-2723), gated by `CORTEX_EMBED_ORCHESTRATOR` (:2028-2033). Python embedding children already retired in the Rust orchestrator (`config.force_python = false` :2077).
- **Summary artifact**: atomic 0600 write (orchestrator.rs:2899-2928), parse-quality manifest (orchestrator.rs:393-433).

**All `delegate_to_python` / `DELEGATE_SENTINEL` sites** (complete list):

| Site | Trigger |
|---|---|
| orchestrator.rs:270-279 → `delegate_to_python("graph target resolution: {reason}")` | `graphops::prepare_graph_args` errors: (a) provider `ladybug` — "requires embedded storage resolution (Python-plane)" (graphops.rs:85-88); (b) default `falkordb` with neither `FALKORDB_URI` nor `--falkordb-path` — "embedded FalkorDB storage resolution (resolve_storage) is Python-plane" (graphops.rs:104-107) |
| orchestrator.rs:341-344 (strip of `DELEGATE_SENTINEL` prefix, const at :31) from `run_flow` | single emitter: orchestrator.rs:1222-1228 — required journal lane active (`CORTEX_GRAPH_JOURNAL_MODE` in {required, shared-required}, or empty + cplus parser with cplus changes; check at :1208-1221): "required graph journal lane (SQLite store, resume/finalize) is Python-plane" |
| orchestrator.rs:2931-2949 `delegate_to_python` impl | re-execs `python <repo>/code-tiny/tools/sync/incremental_sync.py` with identical argv (`registry::repo_root()`), forwarding child exit code |

- `graphops::open_store` additionally **fails closed** (not delegates) on embedded falkordb: "embedded FalkorDB (FALKORDB_PATH) driver is Python-plane" (graphops.rs:130-133).

### 1.2 graphops.rs — target resolution

`prepare_graph_args` (graphops.rs:66-127): supported providers = `neo4j` (needs uri/user/pass, :72-84), `falkordb` **remote URI only** (:89-125), `ladybug` → Err (:85-88). `apply_project_registry_defaults` is a stub that only warns (graphops.rs:48-60) — the Python ProjectRegistry lookup (`tools/common/project_registry.resolve_project_targets`, cli.py:137-191) is not ported. `graph_target_cli_args` pins children via `--graph-provider/--falkordb-graph` only (graphops.rs:304-317).

### 1.3 Existing Rust ladybug / embedded implementations — BIGGER THAN EXPECTED

- **Crates**: `rust/crates/` has `cortex-falkordb` (remote RESP client), `cortex-graph-core` (journal SQLite + schema manifest), `cortex-graph-driver` (ladybug spike), `cortex-graph-writer` (store backends + upserts), `cortex-storage` (full storage-plane port), `cortex-migrate`, `cortex-sync`, `cortex-embed`, `cortex-retrieval(-py)`.
- **`lbug` crate (FFI, pinned 0.20.4)** = native embedded LadybugDB binding, same engine/on-disk format as PyPI `ladybug` (plans/260913-1715 plan.md:22,58; `cortex-graph-driver/Cargo.toml:11`). Spike proves: open store `Database::new(path, SystemConfig::default())` + `Connection::new` (spike.rs:11-19), **write** `CREATE NODE TABLE IF NOT EXISTS` + `CREATE (:File)` (spike.rs:28-41), read (spike.rs:44-59), cross-language store copy incl. `.wal` (spike.rs:63-79). Phase-06/07 of plans/260913-1715 delivered driver spike + journal write core + PyO3 retrieval bindings (plan.md phase map :36-43; status "implemented-phases-01-07" :3).
- **`cortex-graph-writer::store::LadybugStore`** (`rust/crates/cortex-graph-writer/src/store/ladybug_store.rs:165`, `impl GraphStore` at :772) is a **full write path**: opens embedded store, bootstraps `CODE_GRAPH_SCHEMA` + auto-DDL on binder errors (module doc :7-21), renders params as Cypher literals with uniform-key batch maps (no LIST<STRUCT> binding in 0.20.4), `SET +=` unsupported, `datetime()`→`timestamp('…')` rewrite, MERGE natural-key→`id` rewrite (`rewrite_merge_natural_keys` :72+). Already used in production by **Rust analyzer binaries** (analyzer-{topology,perl,dart,js,shell,jvm-overlays,web-overlays}, `cortex-analyzer-framework/src/cli.rs:255-273` opens `LadybugStore::open` from `--ladybug-path`/`LADYBUG_PATH`). Integration test: `cortex-graph-writer/tests/ladybug_store_integration.rs`.
- **So: yes — existing Rust ladybug code can OPEN an embedded `.lbug` store and WRITE (schema bootstrap + node/edge upsert streaming)**. The gap is only that `cortex-sync`'s `graphops.rs` never constructs `LadybugStore` (its `open_store` returns `FalkorDbStore` concretely, graphops.rs:130-144).
- **Embedded FalkorDB has NO Rust binding**: `cortex-analyzer-framework/src/cli.rs:288-291` — "`--falkordb-path` (embedded FalkorDBLite) chỉ chạy phía Python". Python side is redislite (see 2.2). A Rust cutover for embedded falkordb would need either a redislite-equivalent or a data migration to ladybug (see GAPS).

### 1.4 Storage resolution — already ported in Rust

- `rust/crates/cortex-storage/src/config.rs:590` `pub fn resolve_storage` (CLI > config > env > derived default; instance root `data_home/v1/instances/<id>` at :693; manifest_path `instance_root/manifest.json` at :772) + `derive_ladybug_path` (:541-543 → `layout::derive_ladybug_store_path`, `.lbug` suffix `layout.rs:19`, per-graph file name :30, owner dir :63). `ResolvedStorage` mirrors Python fields incl. `ladybug_code_path` (:405-406). Remote/local backend validation + ladybug-alias normalization (:85-86, 183-186).
- `cortex-storage/src/lease.rs` `StorageLease` (lock-file acquire/conflict/release, :46-171) ports `cortex_harness/storage/lease.py`.
- **cortex-dev already consumes this**: `cortex-dev/src/env.rs:296-349` `storage_env_for_process` calls native `resolve_storage` + `storage_overlay` (sets `LADYBUG_PATH`/`ENV_GRAPH_PROVIDER=ladybug`, mirroring `storage/config.py:529-599`) to build the sync child env.

### 1.5 journalx.rs (cortex-dev) + required-lane behavior

- `recover_required_lane` (`rust/crates/cortex-dev/src/journalx.rs:306-343`): fires when `CORTEX_GRAPH_JOURNAL_MODE ∈ {required, shared-required}` (:307-313); prepends `<repo>/code-tiny` to `PYTHONPATH` (:315-332) and spawns `python -m tools.graph.journal.consumer` (`util::harness_python(&root)`, cwd `code-tiny`, :333-337). Documented FORCED-PYTHON with rationale (journalx.rs:300-305). Called per attempt from `cortex-dev/src/cmds/sync.rs:343-346`.
- journalx also holds native `status_payload`/`purge` on the SQLite journal via `cortex_graph_core::journal` (journalx.rs:175-294) — read/purge side is Rust already.

### 1.6 DELEGATE_SENTINEL emission point (orchestrator.rs:1226 area)

orchestrator.rs:1208-1228: computes `configured_journal_mode`; `setup_is_required` when mode ∈ REQUIRED_MODES (`journalenv.rs:26`) or (empty mode AND cplus in filter AND cplus paths changed). If required → `Err(DELEGATE_SENTINEL + …)` → `delegate_to_python`. Note shadow lanes are already configured natively: `journalenv::configure_journal_env` / `physical_target_from_env` / `journal_mode_for_lane` (journalenv.rs:287-441, call sites orchestrator.rs:1617-1644, 1810-1836, 1942-1968) — default migrated lane: **cplus → shared-required** (journalenv.rs:49-61), so the cplus lane is what keeps hitting the delegate today.

### 1.7 state.rs — JSON contract vs Python

`rust/crates/cortex-sync/src/state.rs` vs `code-tiny/tools/common/incremental_sync_state.py`: same `STATE_SCHEMA_VERSION = 2` (state.rs:9; py :16), same fields/`to_dict` key order and `sorted(set(...))` for `dirty_inventory_paths`/`working_tree_paths` (state.rs:122-150; py :69-88), same v1→v2 migration detection + `migration_required` merge (state.rs:44-58; py :40-49), same `.v{n}.bak` backup (state.rs:182-199; py :136-147), atomic tmp+rename save (state.rs:171-179; py :121-133 — Python retries `os.replace` 5× on PermissionError, Rust does not: minor divergence). **Bit-compatible in practice.** Python-only extra: `state_file_path`/`legacy_state_file_path` helpers exist on both sides (`syncscope.rs` mirrors py :100-108).

### 1.8 plans/260915-2027-vector-lane-rust-port — status and overlap

- That plan is the **MCP vector lane** (semantic_search/explore_graph/mind local+remote), NOT the sync embedding lane (plan.md:13-19). Status: phases 01-05 done; default flip deferred — 6 explore/expand divergences left to graph-track; rollback `CORTEX_MCP_BACKEND=python` (reports/phase05-cutover-bugfix.md §3). Delivers `scripts/rust_mcp/vector_worker.py` sidecar + `VectorSearch` trait; modifies `cortex-mcp`, runbook, Makefile. **No orchestrator.rs overlap.** The sync-side Python embedding children were already retired by the sync plan phases 06/08 (orchestrator.rs:2018-2033, 2071-2077 comments).

---

## Q2. PYTHON SIDE — what must be ported or dies

### 2.1 `code-tiny/tools/sync/incremental_sync.py` (4287 lines) responsibility inventory

| Responsibility | Python evidence | Rust equivalent? |
|---|---|---|
| Arg contract (~50 flags) | parse_args :4092-4249 (`--root/--config/--project-id/--before-sha/--after-sha/--parsers/--python-bin/--cache-dir/--submodules :4131/--allow-full-fallback :4159/--message-output-dir :4196/--full-scan :4206/--sync-mode :4211 + validation :4244-4248/--parse-quality*/--neo4j-*/--falkordb*/--ladybug*/--qdrant-url/--embed-*`) | Yes — `cortex-sync/src/cli.rs` mirrors incl. `--ladybug-path/--ladybug-graph` (cli.rs:118-126, 317-338) and provider default win32-ladybug (cli.rs:30-36) |
| State migration/backup | imports :37-44; usage in `_run_incremental` | Yes — state.rs (1.7) |
| Bootstrap full-scan decision | same rule as Rust (no baseline/migration) | Yes — orchestrator.rs:794-807 |
| Submodules / repo scopes | `tools.common.git_diff.discover_repository_scopes` (import :30-36) | Yes — gitdiff.rs (orchestrator.rs:858-913) |
| Framework overlay detection | `_group_paths_by_framework` :725-946 (detector classes) | Yes — frameworks.rs |
| Project topology overlay bootstrap | `_project_topology_bootstrap_needed` :1155-1226 | Yes — orchestrator.rs:1360-1382 + graphops.rs:275-302 |
| Parse quality | parse_quality args + artifacts | Yes — orchestrator.rs:393-433, 2520-2551 |
| Embedding children (`<parser>_embedding_changed_<token>.json` manifests :3623) | Python embedding pass | **Retire** — Rust orchestrator embedding pass replaces (orchestrator.rs:2085-2094 native artifacts) |
| Graph setup/schema bootstrap + Project/Repository | `tools.graph.schema ensure_schema` (import :81), `_PROJECT_REPOSITORY_SETUP_QUERY` | Yes for falkordb-remote — graphops.rs:147-213 + `cortex_graph_writer::preflight::ensure_schema`; missing for ladybug (open_store) |
| Graph writer invocation | via children (language writers) | Yes — children are Rust analyzers writing via cortex-graph-writer (both providers handled there) |
| Journal resume (required lane) | `_resume_configured_journal` :1229-1272 → `resume_journal` | **NO — must port** (consumer replay driver) |
| `_ladybug_driver_config` / embedded target derivation | :1030-1047; `prepare_graph_args` in cli.py:235-252 (`resolve_storage(Path.cwd()).ladybug_code_path` :243-244); falkordb embedded :265-269 | **NO — must wire**: cortex-storage already has `resolve_storage` (1.4), just call it |
| Summary JSON + run_result contract | `_write_summary` :1889-1924, reliability imports :62-72 | Yes — orchestrator.rs:2899-2928, 434-450 |
| Impact expansion, no-change verification, per-parser isolation | :1050-1152 etc. | Yes |
| TS analyzer pick, android/vb/jp1 classifiers | :366, :521-648 | Yes — tsdetect.rs, routing.rs |

**Net: the only strictly Python-only behaviors are (a) embedded target resolution wiring (library exists), (b) required-journal resume/finalize replay, (c) ladybug + embedded-falkordb driver construction inside cortex-sync (library exists for ladybug).**

### 2.2 `tools/graph/` drivers — exact mechanics

- **`driver/ladybug_driver.py` (1476 lines)**: PyPI package `ladybug`; `_open_local_ladybug` (:171-210) does `import ladybug; ladybug.Database(str(path), read_only=…, buffer_pool_size=$LADYBUG_BUFFER_POOL_SIZE)` — **in-process embedded binding, no port/socket/server**; creates parent dirs, chmod 0700 on create (:188-210). Dialect layer: auto-DDL error regexes for 0.20.4 binder wording (:85-108), `CORTEX_GRAPH_AUTO_DDL` default on (:110-123), write-intent token set incl. COPY/INSTALL/LOAD/CHECKPOINT/EXPORT/IMPORT (:129-139), value→column type inference (:154-168), result normalization to FalkorDB shape `_label/_id/_type/_start_id` (:80-81, 223-259). Uses `cortex_harness.storage.lease.StorageLease` + `admission.BoundedLane` (imports :64-65) and `storage.layout.ladybug_store_file_name` (:66).
- **`driver/falkordb_driver.py` (795 lines)**: embedded = PyPI **`falkordblite`**, API `from redislite.falkordb_client import FalkorDB` (:140-168) — i.e. redislite **spawns a bundled redis-server subprocess with the FalkorDB module, private unix socket, `.rdb` persistence file** (no TCP port; `socket_timeout` env `FALKORDB_SOCKET_TIMEOUT_SECONDS` default 300). Network args deprecated + rejected when path given (:228-238).
- **`driver/neo4j_driver.py` (535 lines)**: bolt; dies with sync plane only if nothing else uses it.
- **`core/factory.py` (217 lines)**: `GraphDriverFactory.create_driver/create_from_env`; imports `cortex_harness.storage` (:178 per disposition).
- **`schema/`**: `manifest.py` (CODE_GRAPH_SCHEMA, fingerprint — Rust port exists: `cortex-graph-core/src/schema_manifest.rs`), `preflight.py` (ensure_schema/manifest verify — Rust: `cortex-graph-writer/src/preflight.rs`).
- **`writer/`** (language_writer + per-family writers): superseded by Rust `cortex-graph-writer/src/{language_writer,upserts,operations,…}` used by Rust analyzers.
- **`operations/`** + **`cli.py`**: cli.py `prepare_graph_args`/`create_graph_driver_from_args` (:216-401) is the orchestrator-side entry — this is the piece to port; operations/* back MCP queries (die with Python MCP phase).

### 2.3 `cortex_harness/storage/` (16 files)

- **resolve_storage algorithm** (`storage/config.py:396-526`): precedence CLI arg > config key (`CFG_*`) > env (`ENV_*`) > derived default; data_home relative names anchored under `default_data_home()` (NOT project_root) :451-469; instance identity validated :471-484 (code_owner ≠ doc_owner); instance_root `<data>/v1/instances/<instance>` :486; falkordb `…/falkordb/<owner>/data.rdb` :497-498; ladybug `…/ladybug/<owner>/<DEFAULT_LADYBUG_GRAPH>.lbug` via `_derive_ladybug_path` :503-512; `manifest_path=instance_root/manifest.json` :520; remote/legacy validation :426-449; `storage_overlay` env emission incl. ladybug/remote overrides :529-599. Real manifest on disk confirms the contract: `~/.cortext-harness/v1/instances/default/manifest.json` = `{created_at, instance_id, owners{code/doc{falkordb_path,qdrant_path}}, path_provenance, schema_version:"v1"}`.
- **Lease** (`lease.py`): lock file beside target with JSON metadata; conflict error; `assert_owner_stopped` (:91).
- **Rust status**: full port exists in `cortex-storage` (config.rs resolve_storage :590, layout.rs, lease.rs, admission.rs, factory.rs, generation.rs active-generation manifests, targets.rs, qdrant_remote.rs, remote_probe.rs incl. `setup_constraints.py` spawn :439-446). **Rust code that reads/writes `instances/…/manifest.json`: `cortex-storage/src/config.rs:460,772`.** No other crate parses it directly.
- **CANNOT die with the sync plane**: live importers outside sync — `doc-tiny/graph_store.py`, `doc-tiny/doc_local_qdrant.py`, `doc-tiny/mcp_graph_rag.py`, `code-tiny/tools/common/{local_qdrant,harness_config}.py`, journal/config.py, driver/factory (grep 2026-09-15). Disposition groups storage under LIVE (A1) + parity closure (B), and `code-tiny/scripts/setup_constraints.py` is path-spawned by `cortex-storage/src/remote_probe.rs` (disposition.md:91).

### 2.4 `tools/graph/journal/` — consumer contract for a Rust port

- **SQLite schema** (`journal/sqlite_store.py:375-558`): tables `runs, artifacts, batches, barriers, events, producer_completion, node_manifest, edge_manifest, edge_endpoint, endpoint_audit` + 9 indexes. **The exact DDL is already ported**: `rust/crates/cortex-graph-core/src/journal.rs:87-303` (`schema_statements`/`create_schema`).
- **Rust journal API already has the full producer/consumer primitive surface**: `Journal::{open :733, recover_expired_leases :857, open_run :949, get_run :1082, purge_run :1101, list_runs :1232, find_resumable_run :1245, create_artifact :1285, enqueue_batch :1303, open/close/get_barrier :1607-1733, claim_batch :1733, renew_lease :1811, ack_batch :1852, mark_reconciling :1953, schedule_retry :1993, block_batch :2044, claim_reconciling(_job) :2091-2166, recover_run_leases_as_ambiguous :2332, quarantine_legacy_targets :2411, conservation_summary :2503, seal_endpoint_audit :2746, endpoint_audit_status :2838, list_open_producers :2861, complete_producers :2880}`. **What is missing is the replay driver only**: `consumer.py` `GraphWriteJournalConsumer.drain` loop — claim → `_load` (operation descriptor + JSONL artifact, count/hash check :68-95) → node batches first with `NODE_PHASE_BARRIER`/`ENDPOINT_AUDIT_BARRIER` (:250-297) → endpoint readback audit → edge batches → reconcile/retry classes; entry `_main` (:349-374) reads env config, creates driver from `CODE_GRAPH_PROVIDER/GRAPH_PROVIDER` (**falls back to FALKORDB for any non-neo4j value — no ladybug branch: OPEN QUESTION whether `python -m tools.graph.journal.consumer` ever worked on ladybug required lanes**) and exits 70 on failure.
- **Byte-compat requirements for existing journals on disk**: same DDL + same artifact JSONL read semantics (`artifacts.read_jsonl`, sha256 in `batches.artifact`), same `run_id = sha256(metadata)[:n]` identity (`journal/identity.py`), same status/lease columns. Rust already opens real journals (`.cache/graph-write-journal/<scope_id>/…` observed at repo `.cache`; journal filenames `<safe_parser>.sqlite3`, journalx.rs:273-280). The consumer must also honor `config.required` + `schema_fingerprint == CODE_GRAPH_SCHEMA.fingerprint` fail-closed preflight (`_ensure_recovery_schema`, consumer.py:299-329).
- **Emitter side** (what enqueues): Python `guard.py`/`runtime.py` wrap driver mutations into journal batches during Python children — with Rust analyzers writing through cortex-graph-writer today, required-lane **production** for Rust children is via the Rust `enqueue_batch` path (`journalenv.rs` configures env only; verify who enqueues for Rust cplus lane — OPEN QUESTION: does the Rust cplus analyzer already journal its writes, or does required-mode assume Python writers?).

### 2.5 What DIES vs SURVIVES when sync-plane goes Rust-only

**Dies (sync closure)**: `code-tiny/tools/sync/incremental_sync.py` (+ likely `tools/sync/message_scan.py` already ported to `cortex-sync/src/message_scan/*`; `owner_manifest.py`, `build_owner_manifests.py`, `dead_code_report.py` need separate caller check — OPEN QUESTION for phase planning). `tools/graph/{cli.py, driver/{falkordb_driver,ladybug_driver,neo4j_driver}.py, core/{factory,base,cypher_driver,provider_*,shared_runtime,…}.py}` — only if no other live callers (Python MCP plane dies separately in cleanup phase-02; `tools/graph/journal/**` consumer SURVIVES until the Rust replay driver lands). Tests: `tests/test_incremental_sync_{bootstrap,cobol,continue_on_error,framework_overlays,graph_setup,ignore,lock,non_git,parse_quality,phase_modes,project_topology,result_contract,state_migration,submodules,workflow}.py` (15 files listed in tests/), `tests/test_sync_processes.py`, plus graph-driver tests that target only Python drivers (`code-tiny/tests/test_ladybug_driver_local.py` is CI carve-out per disposition.md:89 — keep or port assertions to Rust integration test).
**Survives (per plans/260915-2230 disposition.md)**: `cortex_harness/dev.py` repo-root sentinel (`cortex-dev/src/util.rs:16,22` — existence of dev.py identifies repo root; disposition A6 :64-67); parity group B (`scripts/rust_parity/**` closure incl. `cortex_harness/{sync_processes,db_transfer,project_config}.py`, disposition :71-81); gliner inline python in `cortex-doc/src/providers.rs:153-186` with doc-tiny closure (A3 :42-48); **journal consumer until ported** (A5 :57-60); **repo-root marker**: `cortex-sync/src/registry.rs:21` checks `code-tiny/tools/sync/incremental_sync.py` existence to locate repo root — deletion without replacing this marker breaks `repo_root()` (disposition :61-62). Marker replacement is a hard prerequisite of deletion.

---

## Q3. PARITY / GUARDRAILS

### 3.1 scripts/rust_parity/sync_orchestrator_parity.py (540 lines)

Compares Python vs Rust on a fixture repo: matrix = hybrid full+incremental, `committed`, `hash` modes; gates (a) `[SCAN_RESULT]` stdout byte-identical, (b) summary JSON equal after masking volatile fields, (c) changed-manifest lists equal, (d) FalkorDB graph state diff 0 vs `dual_write_diff` mask, (e) per-mode change sets (head :13-37). **Archived as of phase-08**: Python analyzer side can no longer run (archive notice :40-46; fixtures golden). **Extending for embedded lanes**: runs today with `--graph-provider falkordb` remote/isolated graphs; to cover ladybug, the harness needs (1) Rust-only lanes (no Python reference) or golden fixtures from a ladybug Python run captured before cutover, (2) graph-state diff reader for `.lbug` stores (lbug crate or `export` script), (3) cache-dir isolation per provider. Suggest a new gate harness rather than reviving the Python leg.

### 3.2 docs/cutover-runbook.md — rollback convention

Existing convention (runbook :28-49, 178-194): env-flag rollback with explicit names — `CORTEX_MCP_BACKEND=python`, `CORTEX_EMBED_BACKEND=python`; analyzer layer has **no runtime python flag** — rollback = git revert of the cutover commit, with a loud "retired" error + `script_path` remnant (runbook :244-250). Phase-08 pattern: dogfood 7 days + rollback drill before removing flags (runbook :244). **Recommended convention for sync-plane cutover**: follow the analyzer precedent — single flipping commit, rollback = `git revert`/rebuild, since the delegation path (delegate_to_python) is the natural temporary rollback hatch: keep `incremental_sync.py` in tree + marker until dogfood sign-off, and add an explicit opt-out (e.g. `CORTEX_SYNC_BACKEND=python`, mirroring naming) that forces `delegate_to_python` even when native paths are ready; retire it in a later commit like phase-08 did.

### 3.3 `dev sync code` → cortex-sync wiring

- `cortex-dev/src/cmds/sync.rs:872-876,1016-1019` execs `cortex-sync` binary (located :520-541, env `CORTEX_SYNC_BIN` → rust/target/{release,debug}).
- Graph args: `neo4j_args_code` (sync.rs:148-220) — for ladybug forwards `--ladybug-path` **only if `LADYBUG_PATH` is non-empty in the project config env** (sync.rs:178-189) and `--ladybug-graph` (default `hyper_graph`). `LADYBUG_PATH` is set at `dev init` (cmds/init.rs:631-651, blank = storage default).
- The derived path seen in smoke logs (`…/instances/<id>/ladybug/code/code.lbug/<graph>`) is computed by the **storage overlay**: `cortex-dev/src/env.rs:296-349` `storage_env_for_process` → native `cortex_storage::resolve_storage` + `storage_overlay` (sets `LADYBUG_PATH`/`GRAPH_PROVIDER` for ladybug; Rust port of `storage/config.py:529-599`).
- **Would a Rust `resolve_storage` inside cortex-sync change it?** No — the same algorithm is already the native implementation (cortex-storage); calling it from `graphops::prepare_graph_args` instead of relying on dev's overlay makes cortex-sync self-sufficient (needed because parity/CI runs cortex-sync directly). Precedence must keep `--ladybug-path` explicit arg > `LADYBUG_PATH` env (cli.rs:321 already merges env fallback) > `resolve_storage(root).ladybug_code_path` (Python uses `Path.cwd()` — cli.py:243-244; the Rust port should use `--root`/cwd consistently — OPEN QUESTION: Python resolves with `Path.cwd()`, which may differ from `--root`; mirror or fix deliberately).
- `recover_required_lane` pre-run per attempt: sync.rs:343-346.

### Conventions to follow
1. Strangler-fig + golden parity fixtures (plans/260913-1715 plan.md:47); no `unsafe`, clippy `-D warnings` (:52).
2. Ladybug dialect facts belong in the store layer (`LadybugStore` doc comment is the canonical Rust record).
3. Cutover = one commit flip + loud retired-error, rollback by revert (runbook phase-08 pattern).
4. Delegate hatch (`delegate_to_python`) stays compilable until dogfood sign-off; marker `registry.rs:21` swap is part of the deletion phase, not the port phase.

---

## PORTING GAPS — ranked by risk

1. **Required journal lane replay driver in Rust (highest)** — everything else is already written: SQLite DDL, producer/consumer primitives (`cortex-graph-core/src/journal.rs`), env config (`journalenv.rs`) exist; missing only `consumer.py`'s `drain()` orchestration (barrier ordering node→endpoint-audit→edge, retry/reconcile classes, artifact JSONL verification) as a Rust driver over `Journal` + `GraphStore`. Risk: correctness of the exactly-once/lease semantics; byte-compat with existing journals. Sub-risk: the Python consumer `_main` has **no ladybug branch** (consumer.py:351-356) — confirm required-lane-on-ladybug behavior before freezing the contract.
2. **Embedded target resolution wiring in cortex-sync** (low code, medium blast radius): call `cortex_storage::resolve_storage` from `graphops::prepare_graph_args` for ladybug + embedded falkordb, port `apply_project_registry_defaults`/ProjectRegistry graph-name resolution (graphops.rs:48-60 stub vs cli.py:137-191), and decide the cwd-vs-root question. Risk: path drift vs dev-overlay-computed paths → wrong store opened silently.
3. **`open_store` must become provider-polymorphic**: return `Box<dyn GraphStore>` (`LadybugStore` for ladybug; `FalkorDbStore` for URI; **embedded falkordb has no Rust binding** — decide: fail-closed with guidance to ladybug/remote (matches analyzer-cli precedent cli.rs:288-291), or port redislite spawning (high effort, not recommended). Message-scan lane and journal replay need the same trait object (message lane currently hardcodes `FalkorDbStore`, orchestrator.rs:3009).
4. **Repo-root marker replacement** (must precede deletion): `registry.rs:13-26` keys on `code-tiny/tools/sync/incremental_sync.py`; also `delegate_to_python` (orchestrator.rs:2934-2936) and `journalx.rs` PYTHONPATH pre-depend on `code-tiny`. Swap marker (e.g. `cortex_harness/dev.py` or `rust/` dir) in the same phase that deletes the file, or repo detection silently degrades to `"."` (registry.rs:25).
5. **Schema/Project-Registry defaults + `falkordb-graph` default resolution parity** for embedded lanes (project_id-derived graph name, `hyper_graph` fallback) and `LADYBUG_GRAPH` vs `neo4j_db` unification — small but parity-sensitive (summary `services.graph_ready` semantics).
6. **Test/parity harness rebuild for embedded lanes**: sync_orchestrator_parity.py Python leg is archived; need golden fixtures for ladybug + a graph-state diff tool for `.lbug` stores; keep `CORTEX_SYNC_BACKEND=python` (new) as the temporary rollback flag per runbook convention, retired after drill.

**Explicit OPEN QUESTIONS**: (a) who enqueues journal batches when the cplus lane is Rust (does the Rust cplus analyzer journal its writes, or is required-mode only exercised with Python children today); (b) ladybug + required-journal combination viability on the Python consumer; (c) fate of `tools/sync/{owner_manifest,build_owner_manifests,dead_code_report}.py` callers; (d) `Path.cwd()` vs `--root` for `resolve_storage` inside the orchestrator; (e) embedded-falkordb Rust strategy (fail-closed vs port) — planner decision.
