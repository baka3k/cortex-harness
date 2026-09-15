# Phase-01 — Probes + golden capture (Python leg còn sống) — rev2

Mục tiêu: khoá evidence trước khi đổi code. Không sửa production code.

## Việc (7 mục — rev2 thêm 3/5/6/7)

1. **Golden ladybug sync** (scratch instance, KHÔNG đụng `cortex`/`default`/`bakatrans`/`phase14-synth`):
   - Fixture `tests/fixtures/web-overlays/fastapi_django` → `/tmp/sp1-golden` (project root chứa `src/`),
     config ladybug + `CORTEX_STORAGE_INSTANCE` scratch.
   - Bootstrap store scratch đúng cơ chế thật (Python driver tạo store lần full-sync đầu; ghi chính xác
     lệnh/cơ chế vào report).
   - Chạy `dev sync code … all` (delegation → Python plane). Capture: summary JSON, changed-manifests,
     state JSON, **copy `.lbug` store** (đóng sạch/checkpoint trước copy) làm golden.
   - Fixtures → `tests/fixtures/sync-plane-golden/ladybug/`; `.lbug` dump nặng → `.cache/sp1-golden/`
     (ghi path trong README fixtures).
2. **Probe journal** (scratch thứ 2, `CORTEX_GRAPH_JOURNAL_MODE=required`):
   - **M2 precondition**: xác nhận Rust children (post phase-08) KHÔNG enqueue batch (đọc bảng `batches`
     trước/sau run) — freeze contract replay = legacy-drain-only.
   - Khoá 1 journal SQLite CÓ dữ liệu làm fixture byte-compat phase-03 (từ legacy trên máy hoặc ép qua
     Python-leg run).
   - Verify consumer.py `_main` ladybug branch (consumer.py:349-374 — nghi ngờ không có): chạy thử, ghi.
3. **Caller-check deletion manifest — mở rộng (C1)**: `grep -rn` importers/callers của:
   - `tools/sync/{owner_manifest,build_owner_manifests,dead_code_report}.py` (giữ nguyên mục cũ)
   - **7 file C1**: `tools/graph/driver/falkordb_driver.py`, `driver/ladybug_driver.py`, `cli.py`,
     `core/base.py`, `core/provider_contract.py`, `core/shared_runtime.py`, `tools/graph/__init__.py`
   - `cortex_harness/dev.py` sync-command bodies :3400-3560 — xác nhận dead (entrypoints binary-only
     qua dev.sh/dev.bat/dev.ps1 + Makefile), cho phép xoá ở phase-06
   - `tests/test_incremental_sync_*.py` — đếm glob thật (red-team L1: 15 file, gồm `_lock`, `_worktree`)
   Kết luận từng file: xoá phase-06 / giữ (chuyển python-legacy-cleanup) — cập nhật plan.md manifest.
4. **cwd-vs-root probe**: cortex-sync chạy từ cwd ≠ `--root`; chốt anchor cho phase-02 (khuyến nghị
   `--root`, ghi deliberate deviation so cli.py:243-244 `Path.cwd()`).
5. **Embedded-falkordb spike input (H2)**: đọc `cortex-migrate/src/falkor_boot.rs:100-117` — đánh giá
   reuse cho `open_store`: spawn lifecycle (shutdown sạch? process leak?), readiness (PING/GRAPH.LIST),
   connect qua `FalkorDbClient`, dependency direction (cortex-sync → cortex-migrate hợp lệ? cần extract
   sang crate chung?). Kết luận: wire-able hay fallback fail-closed. Kèm blast-radius: grep
   `FALKORDB_PATH` trong configs/docs/wiki/CI; xác nhận KHÔNG có data migration falkordb→ladybug
   (đã pre-verify — red-team H2) → message phase-02 phải nêu rebuild.
6. **Coordination (M3)**: (a) kiểm tra working tree — `docs/cutover-runbook.md` đang có uncommitted
   edits của vector-lane; phase-02 chỉ đụng runbook sau khi các edits đó commit; (b) trạng thái
   `plans/260821-2115-dev-sync-code-windows` (status: active, target đè deletion manifest) — liên hệ
   owner/close/re-scope, ghi kết luận vào report (gate phase-06).
7. **Golden mask list**: chốt danh sách mask volatile cho phase-04 (mirror
   sync_orchestrator_parity.py:13-37) **loại graph-name khỏi mask** (H1) + thêm `backend` stamp (L2).

## Gates

- Fixtures + journal fixture commit; README tái-create được.
- `reports/phase01-probes.md` đủ 7 mục, evidence path/lệnh từng câu.
- Không còn open question của research — mỗi câu có kết luận; manifest plan.md khoá cuối.
