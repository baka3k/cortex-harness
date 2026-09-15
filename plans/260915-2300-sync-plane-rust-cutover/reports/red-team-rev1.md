# Red-team review rev1 — sync-plane Rust cutover (260915-2300)

Date: 2026-09-15 · Reviewer: red-team (adversarial, code-verified) · Branch `feat/change-db`
Scope: plan.md + phase-01..06 + research/repository-findings.md, verified against actual code (path:line below).
Verdict rule: PASS requires 0 Critical and ≤2 High.

---

## Findings

### C1 — Critical · Deletion manifest deletes files with LIVE importers outside the sync plane (breaks doc plane + Python MCP rollback)

Evidence (all read 2026-09-15):
- `doc-tiny/graph_store.py:19-23` — inserts `code-tiny` into `sys.path` then **top-level** `from tools.graph.driver.falkordb_driver import FalkorDBDriver`. `:212` — `_open_ladybug_store` lazily imports `tools.graph.driver.ladybug_driver.LadybugDriver`. The doc plane is forced-Python (disposition A5, alive; `cortex-dev/src/cmds/docsync.rs:80` still spawns doc-tiny python today).
- `tools/graph/__init__.py:8` — top-level `from tools.graph.core.base import GraphDriver, GraphProvider, QueryExecutor`; `tools/graph/operations/*.py:8-9` (function_ops, namespace_ops, infra_ops, flow_ops, package_ops, class_ops, document_ops, cross_edge_ops, type_ops) all import `tools.graph.core.base`. `operations/**` is explicitly KEPT ("Back MCP query plane", plan.md §Giữ lại).
- Live Python MCP servers (the `CORTEX_MCP_BACKEND=python` rollback until python-legacy-cleanup phase-02, which is itself `blockedBy` vector-lane 260915-2027): `code-tiny/mcp/cplus/cplus_mcp.py`, `mcp/fastmcp_server.py`, `mcp/unified_mcp.py`, `mcp/java/java_mcp.py`, `mcp/services/{explore,impact,workflow}_service.py` import `tools.graph.core.provider_contract` and `tools.graph.core.shared_runtime` (shared_runtime.py:16 imports `tools.graph.core.factory`); `mcp/services/impact_service.py` imports `tools.graph.cli.env_graph_provider`.
- Deletion manifest (plan.md §Deletion manifest; phase-06.md §2) deletes: `tools/graph/driver/{ladybug_driver,falkordb_driver,neo4j_driver}.py`, `tools/graph/core/{factory,base,cypher_driver,provider_*}.py`, `tools/graph/cli.py`. The `provider_*` glob matches `provider_contract.py` and `provider_runtime.py` — both imported by the live MCP servers.
- The one mechanism meant to lock the manifest — phase-01.3 caller-check — lists only `tools/sync/{owner_manifest,build_owner_manifests,dead_code_report}.py`, `tools/graph/driver/neo4j_driver.py`, `tools/graph/core/factory.py`. It does **not** check `falkordb_driver.py`, `ladybug_driver.py`, `cli.py`, `core/base.py`, `core/provider_contract.py`, `core/shared_runtime.py`, `tools/graph/__init__.py` — precisely the files with live importers.

Why it breaks the plan: phase-06 as written makes `dev sync doc` die at import (top-level `falkordb_driver` import) and kills the Python MCP rollback plane in the same commit that flips sync — directly violating Q1 ("KHÔNG đụng doc-plane/MCP python rollback"). The hedge "(chỉ phần không còn live importer)" is prose; no phase gate tests it for these files.

Fix: (a) narrow phase-06 deletion to `incremental_sync.py` + `tools/sync/**` + the 15 tests + Rust delegate/marker — that is the true sync closure; (b) add the seven files above to the phase-01.3 caller-check; (c) defer `core/*`, `cli.py`, drivers to python-legacy-cleanup (and record that `falkordb_driver`/`ladybug_driver` die only with doc-tiny, i.e. possibly never under current dispositions); (d) add "MCP python backend still boots + doc sync smoke" to phase-06 gates.

### H1 — High · Phase-02 ladybug graph-name precedence contradicts the Python code it cites, and no gate can catch the drift

Evidence:
- `tools/graph/cli.py:249-256` (actual Python): ladybug graph name = `--ladybug-graph` if set, else **`project_id` first**, then `LADYBUG_GRAPH` env, then literal `"hyper_graph"`. `apply_project_registry_defaults` (cli.py:137-191) fills only `args.falkordb_graph`/`qdrant_collection` — the registry never participates in `ladybug_graph`.
- plan phase-02.md §1 specifies: `--ladybug-graph` > `LADYBUG_GRAPH` env > ProjectRegistry defaults > `DEFAULT_LADYBUG_GRAPH` — env outranks project_id and injects a registry step that does not exist for ladybug in Python.
- Gate blindness: summary parity masks "graph name" as a per-run token (`scripts/rust_parity/sync_orchestrator_parity.py:15`, mask logic :225-229); the phase-04 `.lbug` canonical diff compares content per label — a wrong *named* graph inside the same store is a fresh full-sync with identical content, so diff=0 either way. Graph-name drift is invisible to every gate in the plan.

Why it breaks: writing into the wrong named graph silently splits/orphans graph data for registry-configured projects and for direct cortex-sync invocation (parity/CI — the exact scenario phase-02 wires `resolve_storage` for); the plan itself ranks "wrong store opened silently" as risk #2.

Fix: mirror cli.py:249-256 exactly (explicit arg > project_id > `LADYBUG_GRAPH` > `"hyper_graph"`; no registry for ladybug_graph), and make the resolved graph name an asserted, non-masked field in the phase-04 harness (or have `graph-state dump` take `--graph` and fail if the named graph is absent/empty on a full-sync leg).

### H2 — High · Embedded-falkordb "no Rust binding" premise is false; Q2 fail-closed + dev-init flip rest on it

Evidence:
- plan Overview: "embedded falkordb **không có** binding Rust (Python dùng redislite subprocess — falkordb_driver.py:140-168)"; phase-02/Q2: "Không port redislite (high-effort, low-value)".
- `rust/crates/cortex-migrate/src/falkor_boot.rs:100-117` — `launch_embedded_falkordb(data_rdb)` already boots redislite + `falkordb.so` **from Rust** with PING/GRAPH.LIST readiness and connects via `cortex_falkordb::client::FalkorDbClient`. `open_store` could reuse this path with modest effort; neither research nor plan mentions it.
- Corollary the plan misses: there is **no falkordb→ladybug data migration** in cortex-migrate (grep: only `falkor_boot.rs` touches falkordb; no ladybug target). The phase-02 actionable error's suggestion "đổi `GRAPH_PROVIDER=ladybug`" strands the existing embedded graph data — it is a rebuild-by-resync, not a migration. Real instance manifests confirm the exposed shape: `~/.cortext-harness/v1/instances/{cortex,default}/manifest.json` both carry `falkordb_path: …/falkordb/{code,doc}/data.rdb` (embedded).

Why it breaks: the plan's scope decision (Q2) is justified by an impossibility claim that the repo itself disproves, and the user-facing fail-closed message implies a lossless provider switch where only remote-URI (keep data) or ladybug (data regenerated) are the real options. Phase-01.5's probe would discover this, but the premise should be corrected now since Q2 is already "chốt".

Fix: restate Q2 as "not chosen" (reuse `cortex_graph_driver`/`cortex_migrate::falkor_boot` exists but a full embedded backend in cortex-sync adds a spawned-server lifecycle to the sync hot path); error message must say explicitly: `FALKORDB_URI` (keeps data) or `GRAPH_PROVIDER=ladybug` (graph rebuilt by full re-sync; no data migration exists).

### H3 — High · Phase-06 deletes without any dogfood sign-off gate — weaker than the mirrored phase-08 precedent

Evidence:
- Precedent `plans/260915-analyzer-layer-rust-cutover/phase-08.md` §1/§5: flip+delete gated on real dogfood (runbook umbrella wants 7 clean days) — exit criteria: "dogfood gate 7 ngày pass (ops sign-off)" **before** the delete commit; rollback drill on a pre-delete tag (`reports/phase08-rollback-drill.md` Setup step 1: tag `pre-phase08-cutover`).
- This plan: phase-05 accepts "≥1 kỳ" or a pure-scratch fallback (3 runs) with an explicit note that real dogfood "còn mở"; phase-06 Gates contain **no dogfood condition at all** — only smoke + cargo gates. Nothing prevents DELETE landing the same day as the flip on scratch-only evidence.

Why it breaks: after phase-06 the only rollback is `git revert` + rebuild with no Python plane; the precedent deliberately spent dogfood time *before* that point. The plan mirrors the precedent's mechanics (drill report, retired-error) but drops its mandatory gate.

Fix: make phase-06 blocked on the umbrella §2 dogfood-stock gate (or an explicit user-accepted waiver recorded in reports/phase05-drill.md), and tag the pre-delete commit (see L4).

### M1 — Medium · Message-lane provider gate not in the phase-02 change list

`orchestrator.rs:3064-3069`: the native message-scan lane opens a store only when `context.provider == "falkordb"`; any other provider sets `graph_provider_note = "skipped-provider-{provider}"` and silently skips the graph half. Phase-02.2 mentions only the concrete type at :3009. If the gate stays, ladybug message-scan skips graph upsert — caught late (summary diff), not by design. Fix: include the :3064 gate in the trait-object change and add a phase-02 gate asserting `native_message_scan.total_graph_upserted > 0` on the scratch ladybug leg.

### M2 — Medium · Phase-03 lane-policy change is internally contradictory on the empty-mode cplus branch

`orchestrator.rs:1214-1221`: `setup_is_required` = env ∈ REQUIRED_MODES **or (env empty AND cplus in filter AND cplus changed)** — the second branch *is* the `journal_mode_for_lane` cplus→`shared-required` default (`journalenv.rs:49-61`) surfaced at the parent. Phase-03.4 flips the lane default to `off` "giống 37 parser còn lại" but the spec only says to replace the `Err(DELEGATE_SENTINEL…)` emitter (:1222-1280) with native replay — leaving the empty-mode-cplus branch would keep cplus on the replay path (not like the other 37); removing it must be stated, and the gate "Scratch sync default (lane off): hành vi không đổi so phase-02" is unsatisfiable for cplus (phase-02 default for cplus = delegate). Also note journaling consumers today are Python-only (`tools/graph/journal/guard.py` used by consumer.py + retired Python writers; Rust `language_writer.rs:7-9` explicitly does not journal), so the replay driver mostly drains legacy journals — phase-01's OPEN QUESTION (a) probe must confirm the Rust-children-produce-nothing expectation before the contract freezes.

### M3 — Medium · Ordering hazards with in-flight work on the same files

(a) Uncommitted working-tree changes right now: `docs/cutover-runbook.md` (vector-lane live-cutover notes), `doc-tiny/mcp.sh`, `plans/260915-2027-vector-lane-rust-port/reports/phase05-cutover-bugfix.md`. This plan edits `docs/cutover-runbook.md` in phases 02/05/06 — land/commit the vector-lane runbook edit first or phase-02's runbook change ships mixed with another lane's uncommitted work.
(b) `plans/260821-2115-dev-sync-code-windows/plan.md` is still `status: active` and targets `code-tiny/tools/sync/incremental_sync.py`, `tools/graph/cli.py`, `tools/graph/core/factory.py` — exactly phase-06 deletion targets. Confirm that lane is closed (or re-scope it to remote-falkordb-only, which survives as native) before phase-06.

### M4 — Medium · Phase-06 grep gate cannot pass as written

Gate: `grep -rn "incremental_sync" --include="*.py" --include="*.rs"` → allowlist only `plans/**`, `docs/logs/**`, `scripts/rust_parity/**`, wiki. But kept-by-design files contain the string: `cortex_harness/dev.py:1022,1558,3242,3424,3505,3530` (incl. two live spawn commands of `incremental_sync.py` — dead only because entrypoints are binary-only since dev.sh/Makefile cutover), `cortex_harness/sync_processes.py:77,84` (group B parity closure), `code-tiny/tools/common/incremental_sync_state.py` (filename + content, kept), `tools/common/scan_ignore.py:190`. The gate is unachievable → the implementer will improvise the allowlist mid-phase. Fix: enumerate these in the allowlist, and either delete dev.py's dead sync-command bodies (:3400-3560) in phase-06 or record them as parity-reference with a DOC-STALE note.

### M5 — Medium · Rollback drill lacks the precedent's convergence criteria

`phase08-rollback-drill.md` measured: node/rel diff **0 after 2 sync cycles** of the reverted Python plane over post-cutover state, plus stale-vector purge counts. Phase-05.4's drill ("set flag → python PASS → unset → native PASS") checks exit codes only — it can pass while the Python leg mis-reports, half-cleans, or diverges on Rust-written state v2 / LadybugStore-written `.lbug`. This is the last drill possible before phase-06 removes the Python plane. Fix: after the python-flag leg, run `graph-state diff` vs pre-flag dump (=0 after the native leg re-runs) and assert summary health/conservation; record both in reports/phase05-drill.md.

### L1 — Low · Deletion-list count drift

plan.md/phase-06 say 13 `tests/test_incremental_sync_*.py` files; `tests/` actually has **15** (`test_incremental_sync_lock.py` and `test_incremental_sync_worktree.py` missing from the list; research additionally lists a nonexistent `…_workflow.py`). Use the glob without a hardcoded count; phase-01 caller-check should confirm 15.

### L2 — Low · "Native (không delegate)" is not verifiable from the summary artifact

Both native and Python legs write `component=incremental_sync`; the summary carries no backend/native marker. Phase-05 dogfood verification ("summary JSON mới nhất có component=incremental_sync native") is not implementable as written — it must parse stdout/logs. Cheap fix: have cortex-sync stamp `summary["backend"]="rust-native"` (and the delegation path stamp `"python"`) in phase-02/03.

### L3 — Low · graph-state diff does not cover schema-index drift

Phase-04 canonical dump covers labels/keys/property-hash; index/schema-fingerprint drift is covered only by phase-03's preflight. Note it explicitly in the phase-04 harness gates so a golden captured pre-change can't mask an index regression.

### L4 — Low · No pre-delete tag

The precedent created tag `pre-phase08-cutover` which the drill and any post-delete forensics rely on. Phase-06 should tag the commit before the DELETE commit (one command, real payoff for the "rollback = revert" story).

---

## Verified-as-sound (spot-checked, no finding)

- `orchestrator.rs:31,249,270-279,341-344,1214-1227,2931-2949,3009` and `graphops.rs:48-60,66-127,85-88,104-107,130-144` match the research/plan claims (delegation triggers, fail-closed open_store, registry stub).
- `registry.rs:13-26` marker keyed on `incremental_sync.py`; `cortex-dev/src/util.rs:12-31` already keys on `dev.py` (marker swap is cortex-sync-only, as planned).
- `journalx.rs:307-347` python consumer spawn + PYTHONPATH prepend confirmed (deleted by phase-03; its `":"` separator bug dies with it).
- `consumer.py:349-374` `_main` really has no ladybug branch (neo4j→NEO4J else FALKORDB) — research OPEN QUESTION (b) justified.
- `message_scan/graph.rs:199-288` already takes `&mut dyn GraphStore`, and `cortex-graph-writer/src/store/mod.rs` already has `open_store_from_env() -> Box<dyn GraphStore>` with a ladybug branch + `LadybugStore::open(path, graph)` (`ladybug_store.rs:187`) — the Box<dyn GraphStore> refactor is low-risk.
- `cortex-storage/src/config.rs:588-594` `resolve_storage(project_root, config, overrides)` exists and `cortex-dev/src/env.rs:296-349` already consumes it — "library exists, just wire" holds.
- `journalenv.rs:26,49-61`, `init.rs:225-262` (code default "falkordb", doc inherits code provider), `procinfo.rs:109` all match citations.
- cli.py `resolve_storage(Path.cwd())` at :243-244 and `resolve_project_targets` at :137-191 confirmed (cwd-vs-root probe is a real open question).
- Data-safety: phase-01/04 mandate scratch instances (`CORTEX_STORAGE_INSTANCE` scratch); user instances `cortex`/`default` (falkordb-embedded manifests; `bakatrans`/`phase14-synth` absent on this machine) are untouched except the phase-05 user-run dogfood, which is operator-owned and idempotent-by-design.
- Windows: entrypoints are binary-only (`dev.sh`/`dev.bat`/`dev.ps1` exec cortex-dev), so the init-default flip and native lanes apply uniformly; no new POSIX-only path is introduced (the one `":"` PYTHONPATH join is deleted).

---

## Tally

| Severity | Count |
|---|---|
| Critical | 1 (C1) |
| High | 3 (H1, H2, H3) |
| Medium | 5 (M1-M5) |
| Low | 4 (L1-L4) |

## VERDICT: FAIL

1 Critical + 3 High exceeds the PASS threshold (≤0 Critical, ≤2 High). Blockers in priority order:
1. **C1** — re-scope the phase-06 deletion manifest to the true sync closure (`incremental_sync.py` + `tools/sync/**` + tests + Rust delegate); everything under `tools/graph/` other than neo4j-only remnants has live importers (doc-tiny, kept operations, Python MCP rollback) and must move to python-legacy-cleanup; extend the phase-01.3 caller-check accordingly.
2. **H1** — fix the ladybug graph-name precedence to mirror cli.py:249-256 and add a gate that actually asserts the graph name.
3. **H2** — correct the embedded-falkordb premise (cortex-migrate falkor_boot.rs exists; no data migration to ladybug) and rewrite the fail-closed message guidance.
4. **H3** — gate phase-06 DELETE on real dogfood sign-off (umbrella §2) or an explicit recorded waiver, mirroring the phase-08 precedent.
