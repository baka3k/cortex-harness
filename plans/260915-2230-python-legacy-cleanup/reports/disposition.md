# Phase-01 Disposition Report — toàn bộ .py trong repo (khảo sát 2026-09-15, branch `feat/change-db`)

Phương pháp: (1) inventory `find -name "*.py"` loại trừ `.venv/.git/__pycache__/.qwen/target/archived/fixtures/plans`;
(2) **import closure tĩnh** (AST) từ 2 bộ seed — RUNTIME (entry do Rust/entrypoint spawn) và PARITY
(`scripts/rust_parity/**` + `scripts/rust_mcp` tooling); (3) bổ sung **path-spawn** (file được spawn
theo đường dẫn, không qua import); (4) smoke thực nghiệm trên scratch instance. Script tái chạy:
`scripts/audit_python_disposition.sh`. Closure lists: `runtime-live.txt`, `parity-infra.txt`,
`dead-by-import.txt` (kèm theo report này).

## Số liệu tổng (chốt sau review, đầu ra `audit_python_disposition.sh` 2026-09-15 ~23:05)

| Nhóm | Files | Định nghĩa | Số phận |
|---|---|---|---|
| **A. LIVE-RUNTIME (forced-Python)** | **97** (gồm 8 path-spawn keep + 4 installers bảo tồn) | được spawn/import bởi luồng chạy thật hôm nay | **GIỮ**, ghi inventory |
| **B. PARITY/GOLDEN infra** | **104** | chỉ import/spawn bởi parity + fixture tooling | GIỮ đến dogfood sign-off, archive phase-05 |
| **E. ENTRY (CI/pytest)** | **8** | CI lifecycle-macos + conftest chạy trực tiếp | GIỮ (carve-out phase-04) |
| **C. DEAD (delete)** | **373** | không closure, không path-spawn, không entry | XOÁ theo phase 02–04 |
| (fixture corpus `tests/fixtures/**`) | ngoài inventory | dữ liệu, không runtime | giữ vĩnh viễn |

## A. LIVE-RUNTIME — giữ, có chủ đích

### A1. Sync-plane — **DEAD-BY-PLAN (2026-09-16, superseded by plans/260915-2300-sync-plane-rust-cutover phase-06; tag `pre-syncplane-delete`)**
~~SMOKE chứng minh còn delegate~~ — plan 260915-2300 đã port embedded resolution + ladybug store +
journal replay sang Rust rồi **xoá sync closure** (waiver dogfood 2026-09-16):
- ~~`code-tiny/tools/sync/**`~~ — DELETED (commit phase-06)
- `code-tiny/tools/common/**` — phần closure-cụ thể chết theo; còn lại về disposition của plan này
- `code-tiny/tools/graph/**` (drivers/cli/core/schema/operations/writer/journal) — **LIVE importers:
  doc-tiny/graph_store.py, MCP python rollback (`provider_contract`/`shared_runtime`), operations/**
  (red-team C1) → giữ, disposition của plan này (phase-02)**
- **`cortex_harness/storage/**` (16 file)** — vẫn LIVE (doc-tiny, factory, ladybug_driver; phase-08
  xếp parity-reference là SAI — đã sửa tại đây) → disposition plan này
- path-classifier helpers — disposition plan này

### A2. cplus clang plane (decision #8 umbrella — `analyzer-cplus/src/main.rs:8-13`)
`code-tiny/tools/cplus/**` — clang_worker/parse_recovery/semantic_worker/semantic_context là
Python subprocess của Python sync plane (`tools/common/call_evidence.py:58` SEMANTIC_PROVIDERS).
Riêng `rc_parser.py` đã "PORT đầy đủ" (main.rs:25) — xoá được ở phase-04 nếu grep sạch.
**KHÔNG liên quan**: `tools/csharp/roslyn_worker/`, `tools/vb/roslyn_worker/` là **C# source** (.cs/.csproj) — không thuộc cleanup Python.

### A3. Sidecars của Rust runtime (kept-by-design, vector-lane phase-04)
- `scripts/rust_mcp/embed_worker.py` — spawn `cortex-embed/src/sidecar.rs` + `cortex-mcp/src/mind/embed.rs` (rollback `CORTEX_EMBED_BACKEND=python`)
- `scripts/rust_mcp/vector_worker.py` — spawn `cortex-mcp/src/vector_sidecar.rs` (MỚI, vector-lane phase-04)
- **Sửa theo refs-digest (researcher):** `gliner_sidecar.py` KHÔNG được spawn bởi cortex-mcp
  (`mind/qdrant.rs` không có gliner spawn) — nó chỉ là parity tool (`compare_mind.py:293`) →
  chuyển nhóm **B**. GLiNER runtime sống dưới dạng **inline `python -c` script** trong
  `cortex-doc/src/providers.rs:153-186` — một forced-Python dependency ẩn (Rust nhúng code
  Python gọi doc-tiny) cần ghi inventory; import closure của seed gliner_sidecar vẫn trỏ
  đúng vào cùng chuỗi module doc-tiny nên nhóm LIVE bên dưới không đổi:
  `doc-tiny/{doc_local_qdrant,embedding_utils,entity_extractors,graph_store,project_contract}.py`, `doc-tiny/extractor/excel/*`, `code-tiny/tools/common/{local_qdrant,scan_ignore,harness_config,...}.py`

### A4. Dev-harness scripts (chưa có bản Rust)
`harness/scripts/{orchestrator,context_selector}.py` — spawn bởi `cortex-dev/src/cmds/harness.rs`. Giữ; port sang Rust là plan riêng nếu user muốn.

### A5. Doc-ingest + MCP Python rollback (live đến khi phase-02 retire)
- `doc-tiny/graphrag_ingest_langextract.py` — spawn bởi `cortex-dev/src/cmds/sync.rs` doc lane (code-evidence; chưa smoke vì cần GLiNER model download)
- `code-tiny/mcp/unified_mcp.py`, `doc-tiny/mcp_graph_rag.py` — path-spawn khi `CORTEX_MCP_BACKEND=python` (`cmds/mcp.rs:35-46`) + pattern-match trong `mcp_state.rs:21-22`, `cmds/sync.rs:550-551` → **phase-02 phải xử lý cả pattern list**, không chỉ xoá file
- toàn bộ `code-tiny/mcp/**` (27 file) + doc-tiny query path: chết khi flag retired — thuộc DELETE phase-02 (không phải keep)
- **journal consumer** — `dev sync code` pre-run `python -m tools.graph.journal.consumer`
  khi `CORTEX_GRAPH_JOURNAL_MODE=required|shared-required` (`cortex-dev/src/journalx.rs:334`);
  đây là emitter duy nhất của `DELEGATE_SENTINEL` → `orchestrator.rs:1226` (one-way
  Rust→Python). `code-tiny/tools/graph/journal/**` = LIVE.
- marker file: sự tồn tại của `code-tiny/tools/sync/incremental_sync.py` được cortex-sync
  dùng làm repo check (`registry.rs:21`) — xoá file mà không bỏ marker sẽ phá registry.

### A6. Repo-root sentinel — KHÔNG XOÁ `cortex_harness/dev.py`
`cortex-dev/src/util.rs:16,22`: xác định repo root bằng **sự tồn tại** của `cortex_harness/dev.py`;
`procinfo.rs:91` dùng path này. Xoá file → cortex-dev không nhận ra repo. Giữ nguyên (tiện thể
là parity reference).

## B. PARITY/GOLDEN infra (giữ đến dogfood sign-off — archive phase-05)

`scripts/rust_parity/**` (72) + `scripts/rust_mcp/{record_*,compare_*,*_contract,contract_*,generate_data,ingest_mind_fixture,after_sync_check,capture_vector_golden}.py` — import closure 100 file
(vào `tools/common`, `tools/graph`, `cortex_harness/storage`, `mcp_contract.py`,
`doc-tiny/graphrag_query_langextract.py`). **Sửa so với dự thảo plan:** 
`cortex_harness/{sync_processes,db_transfer,project_config}.py` thuộc PARITY chứ không
phải phase-02 delete — `scripts/rust_parity/dev_cli_parity.py:681` spawn
`python -c "from cortex_harness.sync_processes import ..."`; `dev.py` là repo-root
sentinel (`util.rs:16`) + parity reference. Cả 4 chuyển archive phase-05.
Fixtures JSON (`scripts/rust_mcp/fixtures/`,
`tests/fixtures/**`, gen-output) = hợp đồng parity, **giữ vĩnh viễn**.
`cortex_harness/mcp_contract.py` thuộc closure parity.
`conftest.py` + pytest còn chạy cho parity suites — disposition theo phase-04.

## C. DEAD — delete list theo phase

| Phase | Khối | Files (≈) | Evidence chết |
|---|---|---|---|
| 02 | `code-tiny/mcp/**` (trừ file A5 tính pattern), doc-tiny query (`mcp_graph_rag.py`, `graphrag_query_langextract.py` sẽ chuyển nhóm B-nếu-parity / dead), `code-tiny/mcp/services/**`, sub-servers | 27 + ~4 | không closure runtime; chỉ rollback-flag spawn — retire cùng flag |
| 02 | `cortex_harness/_phase08_skip.py` | 1 | phase-08 stub, không còn referencer |
| 04 | tests suite Python chết (`tests/test_*.py` import module đã xoá), `code-tiny/tests/**` (19), `code-tiny/testtool/**` (6) | ~190 | closure DEAD + pytest loud-skip policy. **Carve-out bắt buộc (reviewer fix #3):** `conftest.py` + 6 tests CI chạy trực tiếp (`.github/workflows/lifecycle-macos.yml:80-110` — `test_make_lifecycle`, `test_dev_lifecycle_commands`, `test_rust_bridge_ban`, `test_embedded_discovery_parity`, `test_ladybug_provider_plumbing`, `test_parity_ladybug`) + `code-tiny/tests/test_ladybug_driver_local.py` = nhóm ENTRY, không xoá |
| 04 | `scripts/` top-level: `benchmark_*.py` (3), `audit_graph_ingest.py`, `check_project_isolation.py`, `smoke_unified_contract.py`, `validate_retrieval.py`, `export_lbug_view.py`, `generate_graph_ingest_scale_fixture.py`, `mcp_runtime_config.py` (nếu không thuộc closure mcp-lifecycle), `code-tiny/list_db.py` | 11 | Makefile chỉ còn gọi rust_parity fixture targets (:180-203). **`scripts/mcp-lifecycle.py` KHÔNG xoá** (reviewer fix #1): `cortex-dev/src/cmds/lifecycle.rs:93-95` spawn cho `infra-up/infra-down/doctor` — forced-Python đến khi có bản Rust |
| 04 | `code-tiny/{livingdoc/** (8), skills/, scripts/ (9)}`, `doc-tiny/{0_reset_all,6_setup_indexes,neo4j_loader,open_ai_exec,enviroment_loader,model,gliner/generate_labels}.py`, `code-tiny/tools/csharp/{models,roslyn_integration}.py` (adapter đã port Rust), ts/ts_analyzer.py | ~30 | closure DEAD; từng item needs-decision theo docs/skills. **Carve-out (reviewer fix #2):** `code-tiny/scripts/setup_constraints.py` = LIVE (path-spawn `cortex-storage/src/remote_probe.rs:439-446` cho `dev infra-up --provision`) |
| 04 | `installers/**/*.py` (4) | 4 | needs-decision — caller duy nhất là `dev installer install` (`dev.py:4802,4957`), không entrypoint nào với tới post-cutover; kèm mâu thuẫn `.iss` pack `cli\*` không tồn tại (refs-digest cat-4) |
| 04+05 | Docs stale + mâu thuẫn needs-decision | — | `wiki/**` 146 file DOC-STALE; `wrapper.bat` dead `PYTHON_EXE` block; mâu thuẫn embed default (`cortex-embed lib.rs` nói `python` vs runbook nói `onnx`); `mcp.sh` launchers là live manual paths được CI test assert (refs-digest cat-5/6) |

### Đính chính cuối audit (2026-09-15 ~23:00)

Trong lúc audit kết thúc, **một session song song đang sửa worktree** (chưa commit, không thuộc
plan này): xoá rồi trả lại `code-tiny/tools/sync/incremental_sync.py`, sửa
`scripts/rust_mcp/compare_mind.py`, `mind_contract.py`, `doc-tiny/mcp_graph_rag.py`,
`tests/test_mcp_acceptance_matrix.py`. Audit script **bắt được ngay** qua `verify-live`
(MISSING PROTECTED: incremental_sync.py trong lúc file vắng mặt) — bằng chứng gate hoạt động.
Classification trong report này dựa trên code đã commit (HEAD e4d8ad2) + smoke thực nghiệm;
`verify-live` exit 0 trở lại tại thời điểm chốt. Installers py
(`config_manager/registry_manager`): bảo tồn theo PATH_SPAWN_KEEP đến khi needs-decision
chốt (reviewer fix #5) — caller duy nhất `dev installer install` hiện unreachable.

## Câu trả lời cho phase-03: sync-plane CHẾT HAY SỐNG?

**SỐNG — nhánh B.** Delegation fired thực tế trên ladybug local (`smoke2-ladybug.log`);
embedded-falkordb fail-closed cứng (`graphops.rs:132`); journal lane Python-plane
(`orchestrator.rs:1226`). Muốn xoá `incremental_sync.py` phải **port trước**: local storage
resolution (`resolve_storage`), embedded drivers, journal SQLite lane sang Rust — ngoài scope
plan này. Phase-03 = chốt inventory + docs, không xoá.

## Smoke evidence — `reports/smoke-delegation.md`

Runs thực hiện trên scratch `/tmp/cleanup-smoke-project` (copy fixture `web-overlays/fastapi_django`),
instance `CORTEX_STORAGE_INSTANCE=cleanup-smoke`; không đụng instance `default`/`cortex` của user.
