# Phase-06 report — Marker swap + DELETE sync closure + docs + cross-plan (2026-09-16)

Preconditions:
- **Dogfood gate (H3): WAIVED explicit bởi user** ("chạy phase 6 đi" sau khi được trình bày 2
  lựa chọn) — ghi nhận tại `reports/phase05-drill.md` §DOGFOOD WAIVER.
- Windows plan `260821-2115-dev-sync-code-windows`: **superseded/re-scoped** (status update trong
  plan.md của nó) — M3b ✓.
- Tag **`pre-syncplane-delete`** đã tạo trước DELETE commit (L4).

## Changes

1. **Marker swap (cùng commit DELETE)**: `registry.rs::repo_root` sentinel
   `code-tiny/tools/sync/incremental_sync.py` → **`cortex_harness/dev.py`** (nhất quán
   `cortex-dev/src/util.rs`). Detection chuyển walk-up từ exe (giữ đúng cho cả prod layout
   `target/{release,debug}/` lẫn test layout `target/*/deps/`) — unit test mới
   `repo_root_detects_sentinel_without_env_override` (không set `CORTEX_REPO_ROOT`, binary chạy
   từ sub-dir).
2. **DELETE — true closure (C1 re-scope)**:
   - `code-tiny/tools/sync/**` (5 .py + __pycache__).
   - `tests/test_incremental_sync_*.py` (glob — **15 file**, L1) + `tests/test_sync_processes.py`
     = 16.
   - **+7 test files ngoài glob import trực tiếp module đã xoá** (phase-01 caller-check không
     liệt kê vì chỉ quét tools/graph — phát hiện tại gate): `test_project_topology_acceptance_
     matrix`, `test_mcp_acceptance_matrix`, `test_common_analyzer_registry`,
     `test_primary_analyzer_vector_contract`, `test_aspnet_integration`,
     `test_cplus_windows_resource_parser`, `test_dev_sync_windows_remote` — tất cả test
     registry-shape/hành vi của python orchestrator (superseded bởi `registry_tests.rs` +
     orchestrator tests của cortex-sync).
   - Rust: `delegate_to_python` + `stamp_delegated_summary` + `DELEGATE_SENTINEL` const +
     sentinel-strip trong error handler; graph-target resolution error → **fail-closed exit 1**
     (không fallback).
   - `procinfo.rs`: `CODE_WORKER_NAMES` bỏ `build_owner_manifests.py` + `incremental_sync.py`.
   - `cortex_harness/dev.py`: dead sync-command bodies (sync_code/sync_code_all, ~300 dòng) xoá,
     thay bằng **retired-error stub** (phase-08 style) — file giữ nguyên làm sentinel, parse OK.
3. **Retire flag**: `CORTEX_SYNC_BACKEND` → **loud retired-error** trên stderr + run tiếp tục
   native (mirror phase-08 runbook §5).
4. **Docs**: ReadMe + INSTALLER_GUIDE thêm note sync-plane Rust-only; `wiki/DOC-STALE-sync-plane.md`
   mới; runbook chính thức **DEFERRED** (M3a — vector-lane edits chưa commit; draft nội dung ở
   `reports/phase05-drill.md`).
5. **Cross-plan**: python-legacy-cleanup `disposition.md` A1 → **DEAD-BY-PLAN** (tools/graph +
   storage quay về disposition của nó); rust-full-migration wave sync-plane **CLOSED**; windows
   plan superseded.

## Gates — kết quả

| Gate | Kết quả |
|---|---|
| `grep delegate_to_python\|DELEGATE_SENTINEL rust/crates/` | **0 hit** ✓ |
| `grep incremental_sync` allowlist (M4) | Chỉ còn: `plans/**`, `scripts/rust_parity/**`, `cortex_harness/{dev.py,sync_processes.py}` (path strings), `tools/common/incremental_sync_state.py`, doc-comment/tên thư mục retained (`incremental_sync_summaries/locks`), help-text đã sửa, wiki note ✓ (bảng chi tiết dưới) |
| `cargo test --workspace` (trừ cortex-retrieval-py PyO3 link env; analyzer-cobol `grammar_parity` env-broken — thiếu bundled lib dir, pre-existing) | **554+ passed** (cortex-sync lib 76/76 sau fix cô lập `vector_store` tuning test khỏi config thật qua `CORTEX_HARNESS_CONFIG_PATH` + env-mutex QDRANT_*) |
| `cargo clippy --workspace -D warnings` | sạch ✓ |
| Parity harness post-delete | **PASS all legs** — leg3 đổi thành assert retired-error (stderr loud + run native + no delegation) |
| MCP python backend boots | `dev mcp start` scratch: `unified_mcp.py` (8788) + `mcp_graph_rag.py` (8789) **[ok]** — import chain `provider_contract`/`shared_runtime` sống (C1) ✓ |
| `dev sync doc` import chain (C1) | `doc-tiny/graph_store.py` + `tools.graph.core.{provider_contract,shared_runtime}` import OK ✓ |
| Scratch ladybug sync post-delete | PASS (harness leg1/2/4 — exit 0, `backend=rust-native`, no delegation) ✓ |
| Repo-root detect không `CORTEX_REPO_ROOT` | unit test pass; old marker gone assert ✓ |
| `git status tests/fixtures/` | sạch ✓ |

## Allowlist chi tiết còn sót "incremental_sync" (đều retained-by-design)

- **Tên thư mục runtime** (M4 cho phép): `cortex_harness/dev.py:1558,3242`,
  `cortex_harness/sync_processes.py:77-84`, `cortex-dev/src/{journalx,cmds/sync}.rs`
  (`incremental_sync_locks`/`incremental_sync_summaries`).
- **Doc-comment/port note**: `cortex-sync/src/{graphops,registry,state,util,orchestrator}`,
  `cortex-analyzer-framework/src/{lib,cli}`, `cortex-dev/src/procinfo` (ghi chú phase-06),
  `code-tiny/tools/graph/core/require_neo4j.py` (docstring — tools/graph thuộc
  python-legacy-cleanup).
- **MCP action string**: `code-tiny/mcp/unified_mcp.py` + test input-coercion
  (`"run_incremental_sync"` — recommended-action literal, python MCP plane thuộc cleanup plan).
- **Tests còn sống đọc path names**: `test_dev_sync_reliability/ignore` (summary dirs).
- `tests/test_phase14_rust_analyzer_flip.py`, `test_perl_integration.py` — docstring/fixture refs.

## Rollback

`git revert` các commit phase-06 từ tag `pre-syncplane-delete` + rebuild; python plane trở lại
hoạt động (files restored bởi revert).
