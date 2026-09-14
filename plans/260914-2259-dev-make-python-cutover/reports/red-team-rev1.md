# Red-team rev 1 — verdict: FAIL (2026-09-14, agent_3a46b3d4)

Reviewer độc lập, 46 tool calls, đối chiếu code thật. Verdict: **FAIL** — kiến trúc sound
(waves, native-first, rollback flag) nhưng exit gate tự đặt không thể đạt với scope hiện tại,
goal statement sai cho `dev sync`/`dev harness`, `make build` không thể pass gate "no venv"
(ort-ensure), và khoản cut-maximal lớn nhất (cortex-sync đã parity-PASS) bị bỏ sót.

## Findings

1. **[P0] Exit gate `grep "pyexec\|venv_python" → 0` unreachable.** `cmds/sync.rs:918,1060,1239,1597` +
   `cmds/harness.rs:469,515` spawn Python (incremental_sync.py, graphrag ingest, torch probe,
   orchestrator.py, context_selector.py). Không phase nào đụng execution paths này.
2. **[P0] Goal overclaim**: `dev sync code/doc` spawn Python trực tiếp từ CLI binary
   (sync.rs:939-941, 1095-1097, 1317-1321, 411-415). Qualifier "tầng CLI/lifecycle" không cứu được.
3. **[P0] `make build` mâu thuẫn gate**: `build: ort-ensure` → `scripts/ensure_ort.py:38-42`
   hard-require `import onnxruntime`; cortex-embed load-dynamic tìm `ORT_DYLIB_PATH → .cache/ort → .venv`
   (cortex-embed/src/onnx.rs:63-100, error message bảo "run make build" at :172). Phase-04 bỏ
   pip mà không có successor provisioning → cortex-embed gãy runtime.
4. **[P1] Inventory Python-còn-lại thiếu 5 mục**: (a) incremental_sync.py + graphrag ingest;
   (b) harness orchestrator.py/context_selector.py; (c) torch probe sync.rs:1597;
   (d) `CORTEX_EMBED_BACKEND` default là python sidecar (runbook:21) — claim "mặc định Rust" sai;
   (e) ensure_ort.py.
5. **[P1] Cut-maximal gap lớn nhất**: `rust/crates/cortex-sync/` (~6.6k LoC, parity PASS,
   umbrella phase-09.md:44, consumes CORTEX_RUST_ANALYZER tại registry.rs:269-279) nhưng
   `dev sync code` vẫn spawn Python orchestrator; umbrella phase-10.md:41 deferred — plan không wire.
6. **[P1] Parity harness không cover phases 02–04**: dev_cli_parity.py:8-19 chỉ help-surface
   ~56 paths + output parity status/doctor/storage-layout + init (canned stdin :324-326) + ignore;
   sync stop, mcp ops, journal, db, lifecycle actions không có; sync end-to-end "intentionally
   skipped" (:12-13, 68-73).
7. **[P1] Doctor byte-parity vs no-venv**: invoke_doctor check "python version", "python venv",
   probe qdrant_client/requests/cortex_harness.storage/FalkorDB (mcp-lifecycle.py:1367-1387, 50-58)
   — native doctor trên máy sạch phải in fail + exit non-zero để byte-khớp, mâu thuẫn gate "doctor OK".
8. **[P1] CI đỏ khi flip**: tests/test_make_lifecycle.py:115,148,165 + test_dev_lifecycle_commands.py:141
   assert Python dispatch; workflow lifecycle-macos.yml:75 (trigger dev.py :11). Phases 05/06 không nhắc.
9. **[P1] Sót entrypoints**: dev-global.cmd:5-11; install-windows.bat:63-71; install-windows.ps1:58-60
   (scoop shim → .venv\Scripts\dev.exe) + :73 (pip install -e); installers/windows/scripts/wrapper.bat:54;
   inno cortex_harness.iss:83,88-89,211,220; pyproject.toml:37-38 console script `dev`.
10. **[P1] Miscounts**: harness.rs không chỉ "dùng helper" — helper feed Command::new(python) spawn thật
    (harness.rs:469-471, 515-516); mcp.rs = 4 op call sites (222,355,369,375) + 3 helper refs;
    total `pyexec::` = 39 (plan ghi "38+").
11. **[P2] Cite sai**: pyexec.rs:495 → util.rs:495; pyexec.rs:183,188 → 184,189; Makefile:3 là
    branch Windows → .ps1 (không phải .py); "21/21 commands byte-compare" — harness có ~56 paths,
    không byte-compare 21.
12. **[P2] phase-04 thiếu action `storage-stop`** (ACTIONS dict 14 actions, mcp-lifecycle.py:2031-2045);
    cortex-dev route `dev storage-stop` qua shim (main.rs:68).
13. **[P2] Circular dep phase-04 item 7 ↔ phase-05 item 3** về launcher contract — cần freeze spec.
14. **[P2] Rollback decay**: flag python vô dụng trên máy chưa/chưa còn provision (build không còn
    tạo venv, install không còn viết launcher python). Cần precondition hoặc escape hatch.
15. **[P2] `mcp-lifecycle.py` "port rồi archive" nhưng không schedule archive; ps1 giữ vô thời hạn.**
16. **[P2] Runbook flag table (`docs/cutover-runbook.md:12-23`) phải cập nhật tại phase-05/06
    khi flag ship, không đợi phase-06/07 docs.**

## Cut-maximal assessment

**Forced (phải giữ)**: parity reference + venv dev machines; ensure_ort.py (short-term);
torch device probe; CORTEX_EMBED_BACKEND=python sidecar default (umbrella-owned);
`.harness/scripts/*` (không có Rust nào).

**Removable mà plan giữ/sót**: Python sync orchestrator (wire cortex-sync — unlocks xoá
LANG_ANALYZERS .py table sync.rs:31-60, journal-consumer recovery sync.rs:411, doc-tiny spawn
sync.rs:1318); mcp-lifecycle.ps1 vô thời hạn; 6 entrypoint artifacts Windows/pip không inventory.

**Too aggressive (bỏ Python chưa có replacement)**: `make build` drop ort-ensure không successor
(duy nhất chỗ cut-maximal sẽ gãy chính Rust runtime); doctor byte-parity vs python-check removal
cần quyết định tường minh.
