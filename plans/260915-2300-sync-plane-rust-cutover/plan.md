---
title: "Sync-plane Rust cutover rev 2 — wire embedded storage + ladybug store + journal replay vào cortex-sync, flip, rồi xoá incremental_sync.py + tools/sync closure (red-team rev1 đã hấp thụ 13 findings: 1 Critical re-scope manifest, 3 High)"
status: ready (red-team rev1: verdict FAIL 1C/3H/5M/4L — tất cả đã xử lý, chi tiết reports/red-team-rev1.md; chờ validate/execute)
created: 2026-09-15
revised: 2026-09-15 (rev2 hấp thụ red-team-rev1)
target: "rust/crates/cortex-sync (graphops.rs, orchestrator.rs, registry.rs, journal_replay.rs mới, cli.rs), rust/crates/cortex-dev (journalx.rs, cmds/init.rs, cmds/sync.rs, procinfo.rs), rust/crates/cortex-graph-driver (bin graph-state diff), rust/crates/cortex-migrate (falkor_boot reuse spike), cortex_harness/dev.py (dead sync bodies), code-tiny/tools/sync/** (xoá), tests/test_incremental_sync_*.py + test_sync_processes.py (xoá), docs/cutover-runbook.md, plans/260915-2230-python-legacy-cleanup (cross-update)"
blockedBy: []
blocks:
  - "260915-2230-python-legacy-cleanup"  # nhóm A1 (sync-plane LIVE) chuyển dead khi plan này xoá; `tools/graph/**` di sản (drivers/cli/core) chuyển sang disposition riêng của cleanup
relatedPlans:
  - "260913-2130-rust-full-migration"      # umbrella — wave sync-plane cuối; gate dogfood stock §2 = điều kiện DELETE phase-06
  - "260915-analyzer-layer-rust-cutover"   # tiền nhiệm — phase-07 giữ incremental_sync.py làm delegation target; plan này đóng seam; mirror convention phase-08 (dogfood gate + pre-delete tag)
  - "260913-1538-ladybug-graph-provider"   # lbug crate 0.20.4 + provider; phase-02 kế thừa binding
  - "260913-1715-rust-retrieval-graph-port"# journal primitives; phase-03 kế thừa cortex-graph-core::journal
  - "260821-2115-dev-sync-code-windows"    # active, target đè deletion manifest — phải đóng/re-scope trước phase-06 (M3b)
  - "260915-2027-vector-lane-rust-port"    # uncommitted runbook edits cùng file — commit trước khi phase-02 đụng runbook (M3a)
research: "plans/260915-2300-sync-plane-rust-cutover/research/repository-findings.md"
redTeam: "plans/260915-2300-sync-plane-rust-cutover/reports/red-team-rev1.md"
---

# Sync-plane Rust cutover (rev 2) — xoá `incremental_sync.py` sau khi Rust tự xử lý được mọi graph provider

## Overview — hiện trạng đo 2026-09-15 (research §repository-findings.md, red-team rev1 verify)

Smoke thực nghiệm + test rename-file hôm nay: `dev sync code` trên config ladybug (config thật của
user, `.cortext-harness/config/dev.json`) delegate **toàn bộ run** sang Python qua
`delegate_to_python`; rename `incremental_sync.py` làm sync đứt (`can't open file … [Errno 2]`).
Chỉ có **2 trigger delegation** còn lại:

| Trigger | Vị trí | Lý do |
|---|---|---|
| Graph target resolution | `orchestrator.rs:270-279` ← `graphops.rs:85-88` (ladybug), `:104-107` (embedded falkordb) | chưa gọi `resolve_storage` / chưa construct LadybugStore |
| Required journal lane | `orchestrator.rs:1208-1228` → `DELEGATE_SENTINEL` | consumer replay driver (`consumer.py drain()`) chưa có bản Rust |

**Mọi thứ khác đã native** (research §1.1). Phần "phải port" thì **thư viện Rust đã có, chưa wire**:

- `cortex-storage` = port đầy đủ `cortex_harness.storage` (`resolve_storage` config.rs:588-594 — red-team
  confirmed; layout `.lbug`, lease, migration); `cortex-dev/src/env.rs:296-349` đang dùng, riêng
  `cortex-sync/graphops.rs` chưa.
- `cortex-graph-writer::store::LadybugStore` (crate `lbug` 0.20.4 FFI) = write path đầy đủ đang chạy
  production trong analyzer Rust; `store/mod.rs` **đã có sẵn `open_store_from_env() -> Box<dyn
  GraphStore>` với ladybug branch** (red-team verified-sound) → refactor open_store low-risk.
- `cortex-graph-core::journal` = DDL + producer/consumer primitives đầy đủ (journal.rs:87-303, 733-2880)
  — chỉ thiếu replay driver.
- **Embedded falkordb KHÔNG phải "impossible"**: `cortex-migrate/src/falkor_boot.rs:100-117` đã boot
  redislite + `falkordb.so` từ Rust (red-team H2). Không có data migration falkordb→ladybug (grep —
  chỉ falkor_boot chạm falkordb) — đổi provider = rebuild bằng full re-sync.

Marker `registry.rs:21` nhận diện repo bằng **sự tồn tại của incremental_sync.py** — swap marker là
điều kiện cứng của deletion.

## Scope decisions (user 2026-09-15; khuyến nghị mặc định do user không phản hồi realtime; rev2 sửa Q2)

| Câu hỏi | Quyết định |
|---|---|
| Q1 Biên xoá | **Chỉ true sync closure**: `code-tiny/tools/sync/**` + tests + Rust delegate/marker/journalx-python. **Không xoá** `tools/graph/**` (drivers/cli/core) — red-team C1: live importers ngoài sync (`doc-tiny/graph_store.py:19-23` top-level import falkordb_driver; `tools/graph/__init__.py:8` → core/base; operations/*; MCP python rollback import provider_contract/shared_runtime; impact_service import cli.env_graph_provider). `tools/graph` di sản chuyển disposition python-legacy-cleanup. Giữ `cortex_harness/dev.py` sentinel (A6), giữ parity group B |
| Q2 Embedded falkordb (rev2 sửa) | **Spike-first**: thử wire `open_store` embedded qua reuse `cortex_migrate::falkor_boot` (spawn+readiness+shutdown sạch) → giữ default `dev init` falkordb + giữ data user. FAIL lifecycle → fallback fail-closed với error **trung thực** (H2): `FALKORDB_URI` giữ data / `GRAPH_PROVIDER=ladybug` = rebuild bằng full re-sync, **không có** data migration. Không port redislite từ đầu |
| Q3 Chiến lược cutover | Mirror phase-08 analyzer: wire → parity gates → `CORTEX_SYNC_BACKEND=python` hatch → flip → dogfood thật (7 ngày umbrella §2 hoặc waiver ghi rõ) → **tag `pre-syncplane-delete`** → DELETE 1 commit kèm marker swap |

## Giữ lại có chủ đích (không phải sync closure — không xoá)

| Python | Lý do giữ |
|---|---|
| `cortex_harness/dev.py` | Repo-root sentinel (`cortex-dev/src/util.rs:16,22`); **dead sync-command bodies :3400-3560 xoá ở phase-06** (sau caller-check entrypoints binary-only) |
| `cortex_harness/sync_processes.py`, `tools/common/**` | Parity group B + doc-tiny closure |
| `tools/graph/**` (drivers, cli, core, schema, operations, writer) | Live importers doc-plane + MCP python rollback (C1) — chết cùng python-legacy-cleanup |
| `cortex_harness/storage/**` | doc-tiny + parity closure (disposition A1/B) |
| `scripts/rust_parity/**`, `scripts/rust_mcp/**`, fixtures golden | Parity/golden infra (group B) |
| doc-tiny closure + gliner inline (`cortex-doc/src/providers.rs:153-186`) | Doc-plane, ngoài biên |

## Phase map (6 phases — rev2)

| Phase | Tên | Deliverable chính | Gate chốt |
|---|---|---|---|
| 01 | Probes + golden capture | Golden ladybug sync (summary + manifests + state + `.lbug` dump) từ Python-leg còn sống; probe journal (Rust children có enqueue gì — M2 precondition); caller-check **mở rộng 7 file C1** + dev.py sync bodies dead-check + windows-plan coordination (M3); cwd-vs-root probe; blast-radius embedded falkordb + falkor_boot reuse feasibility spike (H2 input) | Fixtures commit; reports/phase01-probes.md đủ 7 mục; không còn open question |
| 02 | Embedded resolution + polymorphic store + hatch | ladybug branch qua `resolve_storage` (precedence mirror cli.py:249-256 — **H1**: arg > project_id > LADYBUG_GRAPH > "hyper_graph", registry KHÔNG tham dự ladybug_graph); `open_store` → `Box<dyn GraphStore>` qua `open_store_from_env()`; message-lane provider gate :3064-3069 vào cùng refactor (**M1**); embedded falkordb theo spike Q2; `summary["backend"]="rust-native"/"python"` stamp (**L2**); `CORTEX_SYNC_BACKEND=python` hatch | Scratch ladybug sync Rust-only: 0 delegation, summary khớp golden (masked), **graph name assert không-masked**, `native_message_scan.total_graph_upserted > 0` |
| 03 | Journal replay driver Rust | Port consumer.py drain() (barriers node→endpoint-audit→edge, retry/reconcile, JSONL verify, fingerprint preflight); **xoá cả empty-mode-cplus required branch :1214-1221** (M2); journalx pre-run → native lib dùng chung; DELEGATE_SENTINEL emitter chết | Replay byte-compat fixture thật + conservation; double-drain no-op; required-mode scratch PASS Rust-only; non-cplus behavior unchanged |
| 04 | Parity harness embedded lanes | `graph-state dump/diff` bin (canonical + **schema fingerprint field** — L3); harness 5 legs (full/incremental/hatch/journal/embedded-fail) so golden; graph name asserted per leg (H1) | Xanh 2 lần liên tiếp; reports/phase04-parity.md |
| 05 | Flip + dogfood + rollback drill | Flip 1 commit; runbook §flags + §0c; dogfood user ≥1 kỳ thật (verify qua `summary.backend=rust-native` — L2); drill có **convergence criteria** (M5): sau python-flag leg → native re-run → `graph-state diff` = 0 vs pre-flag + summary health | Drill report cả 2 chiều + convergence; dogfood hoặc waiver ghi rõ |
| 06 | Marker swap + DELETE sync closure + docs/cross-plan | Marker → `cortex_harness/dev.py`; xoá **true closure** (C1 re-scope): tools/sync/** + 15 test files (glob, không hardcode count — L1) + Rust delegate/hatch/journalx-python + `procinfo.rs:109` + dev.py dead sync bodies :3400-3560; retire `CORTEX_SYNC_BACKEND` thành retired-error; runbook/wiki/ReadMe; cross-update python-legacy-cleanup + rust-full-migration | **Gate DELETE: dogfood sign-off umbrella §2 hoặc waiver user ghi trong reports/phase05-drill.md (H3)**; tag `pre-syncplane-delete` (L4); grep allowlist **đếm đủ** (M4: dev.py refs còn, sync_processes.py:77,84, incremental_sync_state.py, scan_ignore.py:190); MCP python backend boots + `dev sync doc` smoke PASS (C1); windows plan đã đóng/re-scope (M3b) |

## Deletion manifest (phase-06, rev2 — C1 re-scope; khoá cuối sau caller-check phase-01)

**Xoá:**
- `code-tiny/tools/sync/` — `incremental_sync.py`, `message_scan.py`, `owner_manifest.py`,
  `build_owner_manifests.py`, `dead_code_report.py` (3 file sau rọi caller-check)
- `tests/test_incremental_sync_*.py` (glob — 15 file) + `tests/test_sync_processes.py`; port assertion
  thiếu sang Rust integration test nếu phase-01 thấy gap
- Rust: `delegate_to_python` + hatch check + `DELEGATE_SENTINEL` const; `journalx.rs` python spawn +
  PYTHONPATH block; `procinfo.rs:109` script list entry; dev.py dead sync-command bodies :3400-3560

**Không xoá (chuyển python-legacy-cleanup disposition):**
- `tools/graph/cli.py`, `tools/graph/driver/**`, `tools/graph/core/**`, `tools/graph/schema/**`,
  `tools/graph/operations/**`, `tools/graph/writer/**`, `tools/graph/journal/**` — live importers:
  doc-tiny/graph_store.py, tools/graph/__init__.py, MCP python rollback servers, operations/*
- `tools/common/incremental_sync_state.py` (import sống: parity + state compat)

## Rủi ro chính (rev2 — red-team re-ranked)

1. **Journal replay correctness** — exactly-once/lease/barrier; byte-compat journals trên disk user →
   phase-03 gate replay fixture thật (phase-01 probe), double-drain no-op.
2. **Graph-name drift ladybug** (H1) — precedence mirror cli.py:249-256; assert tên graph ở phase-02/04
   (summary mask phải bỏ graph-name field).
3. **Embedded-falkordb spike-first** (H2) — falkor_boot reuse thử trước; fallback fail-closed trung thực
   + message nêu rõ rebuild-vs-keep-data; phase-01 đo blast radius thật.
4. **Message-lane provider gate** (M1) — :3064-3069 vào cùng refactor; gate `total_graph_upserted > 0`.
5. **Marker/allowlist quên reference** (M4) — grep gate allowlist đếm đủ; `CORTEX_REPO_ROOT` unset test.

## Rollback

- Trước flip: `CORTEX_SYNC_BACKEND=python` ép delegation (hatch, phase-02).
- Sau flip: `git revert` commit flip; python plane còn trong tree tới hết phase-06.
- Trước DELETE: tag `pre-syncplane-delete` (L4) — forensics/revert anchor.
- Dữ liệu: không migrate store; embedded-falkordb wiring (nếu spike thành công) giữ nguyên data;
  fallback ladybug = rebuild (nêu rõ trong error + runbook).

## Out of scope

- Doc-plane (`graphrag_ingest_langextract.py`), MCP python rollback plane, `tools/graph/**` di sản,
  parity archive — python-legacy-cleanup phase-02/05.
- `harness/scripts/{orchestrator,context_selector}.py` (A4).
- Journal producer wiring vào Rust language_writer — durability qua mark-dirty (phase-03 document).
