---
title: "Dev CLI + make cutover — cut tối đa Python khỏi entrypoint dev/make (red-team rev 2)"
status: ready (red-team rev 2 hấp thụ 16 findings + validate 2026-09-14 chốt 4/4 OPEN decision)
created: 2026-09-14
revised: 2026-09-14 (red-team: agent_3a46b3d4, verdict FAIL — 3×P0, 8×P1, 5×P2; tất cả đã xử lý. Validate: ORT=port Rust, sync=giữ phase-04, doctor=bỏ python checks, rollback=bỏ ngay phase-06)
target: "rust/crates/cortex-dev (mở rộng), rust/crates/cortex-sync + cortex-doc + cortex-storage (wire), rust/crates/cortex-embed (ort provisioning), dev.sh/dev.bat/dev.ps1/dev-global.cmd, Makefile, scripts/mcp-lifecycle.py+ps1 (port rồi archive), installers/, pyproject.toml, tests/test_make_lifecycle.py + test_dev_lifecycle_commands.py"
blockedBy: []
blocks: []
relatedPlans:
  - "260913-2130-rust-full-migration"  # umbrella — phase 10 (dev CLI + storage), phase 09 (cortex-sync parity-PASS), phase 14 (cutover code) đã code-complete; plan này là đòn cutover của Scope B
redTeam: "plans/260914-2259-dev-make-python-cutover/reports/red-team-rev1.md"
validate: "plans/260914-2259-dev-make-python-cutover/reports/validate-2026-09-14.md"
---

# Dev CLI + make cutover — cut tối đa Python (rev 2)

## Overview

Hiện trạng đo ngày 2026-09-14 (branch `feat/change-db`): umbrella `260913-2130-rust-full-migration`
đã code-complete P01–P14 (binary `cortex-dev` đủ 21 commands, crate `cortex-storage`/`cortex-sync`
đã port và parity-PASS), nhưng **toàn bộ luồng người dùng vẫn chạy qua source Python**:

| Lớp | File | Hiện trạng |
|---|---|---|
| Entrypoint | `dev.sh:4`, `dev.bat`, `dev.ps1`, `dev-global.cmd:5-11` | `exec` thẳng `cortex_harness/dev.py` |
| Launcher do install sinh | `scripts/mcp-lifecycle.py:367-373` | viết sẵn `exec "$PYTHON_EXE" .../cortex_harness/dev.py` |
| Makefile | `Makefile:12` `DEV` | `export/import/export-db/import-db/sync-*-stop` → `dev.py` |
| Makefile | `Makefile:3,7` `LIFECYCLE` | `build/install/start/stop/doctor/infra-*/storage-*` → `scripts/mcp-lifecycle.py` (POSIX) / `.ps1` (Windows) |
| Bridge trong binary Rust | `cortex-dev/src/pyexec.rs` | 15 helper ops spawn Python import ngược `cortex_harness.dev`/`sync_processes`/`db_transfer` (39 ref `pyexec::`: sync.rs 20, mcp.rs 7 trong đó 4 op-call sites + 3 helper, status.rs 1, journal.rs 2, db.rs 2, lifecycle/migrate/harness dùng helper) |
| **Spawn Python từ binary** | `cmds/sync.rs:918,1060,1239,1317-1321,411-415,1597-1600` | `dev sync code/all` spawn `code-tiny/tools/sync/incremental_sync.py`; `dev sync doc` spawn `doc-tiny/graphrag_ingest_langextract.py`; journal recovery spawn `python -m tools.graph.journal.consumer`; torch device probe `python -c "import torch…"` |
| **Spawn Python từ binary** | `cmds/harness.rs:469,515` | `dev harness` spawn `.harness/scripts/orchestrator.py`, `context_selector.py` (script sinh vào project user — không có bản Rust nào) |
| Delegate lifecycle | `cmds/lifecycle.rs:27` | `Command::new(venv_python(...))` chạy `mcp-lifecycle.py` cho **mọi** action (14 actions, gồm `storage-stop`) |

15 ops của bridge `pyexec.rs`: `status_env`, `code_env`, `doc_env`, `mcp_env`,
`stop_sync_workers`, `embedded_falkordb_pids`, `stop_embedded`, `mcp_pids`, `mcp_uptime`,
`mcp_stop`, `mcp_start`, `journal_status`, `journal_purge`, `db_export`, `db_import`.

### Nguyên tắc cut-maximal (directive user 2026-09-14)

> Chỗ nào Rust đã có → bỏ Python hoàn toàn. Chỗ nào buộc dùng Python mới để lại.

Áp dụng: mọi đường exec/spawn Python mà **đã có crate Rust thay thế** phải chuyển trong plan này
(kể cả spawn từ binary — `dev sync` wire sang `cortex-sync`). Chỉ Python **bắt buộc** được giữ,
liệt kê trắng trong bảng dưới.

## Inventory Python còn lại sau plan (keep/port/kill, từng mục một)

| Python | Trạng thái | Quyết định | Lý do |
|---|---|---|---|
| `cortex_harness/dev.py` + `.venv` (dev machines) | runtime | **KEEP** (parity-reference-only **từ phase-06** — không entrypoint nào trỏ tới nữa) | parity gate `dev_cli_parity.py` cần Python reference; validate: bỏ python rollback ngay phase-06 |
| `scripts/rust_parity/*` | runtime | **KEEP** | golden-fixture reference có chủ đích của cả chương trình |
| `scripts/ensure_ort.py` | `make ort-ensure` | **PORT** (phase-05: native `ensure-ort` — reqwest tải + unzip wheel PyPI pinned) rồi archive phase-07 | validate 2026-09-14: port Rust; `make build` hết cần venv hoàn toàn |
| torch device probe (`EMBED_DEVICE=auto`) `sync.rs:1597` | spawn từ binary | **KEEP, dời vào cortex-sync** (phase-04) | chỉ torch biết MPS/CUDA; forced. Mitigate: cache kết quả + skip khi `EMBED_DEVICE` set sẵn |
| `embed_worker.py` sidecar (`CORTEX_EMBED_BACKEND` default `python`, `docs/cutover-runbook.md:21`) | runtime runtime-sync | **KEEP** (thuộc umbrella phase-14 runbook, không phải plan này) | default flip của embedder là cửa umbrella; plan này chỉ sửa claim sai "mặc định Rust" |
| `.harness/scripts/orchestrator.py` + `context_selector.py` (spawn bởi `dev harness`) | runtime | **KEEP** (wrapper `dev harness` là Rust sẵn) | script sinh vào project user, **không tồn tại bản Rust** — forced duy nhất còn spawn từ CLI |
| `code-tiny/tools/sync/incremental_sync.py` orchestrator + bảng `LANG_ANALYZERS` `.py` trong `sync.rs:31-60` + journal-consumer recovery `sync.rs:411` + doc-tiny spawn `sync.rs:1318` | spawn từ binary | **REMOVE** (phase-04): wire `cmds/sync.rs` → binary `cortex-sync` / `cortex-doc` / journal-core Rust | crate đã parity-PASS (umbrella phase-09); đây là khoản cut lớn nhất |
| `scripts/mcp-lifecycle.py` (2147 LOC) | `LIFECYCLE` + shim | **PORT → ARCHIVE** (phase-05 port, phase-07 archive) | port từng action, không big-bang |
| `scripts/mcp-lifecycle.ps1` (1212 LOC) | Windows lifecycle | **DELETE** ở phase-07 (rollback Windows = bản release trước) | bản copy gần-full thứ hai của lifecycle — đúng pattern "giữ Python nơi không forced" cần tránh |

## Mục tiêu (chính xác, không overclaim — rev 2)

Sau phase-06, các subcommand/target sau chạy **binary Rust, 0 subprocess Python từ tầng CLI**:
`help, init, status, ignore, doctor, build, install, uninstall, infra-up/down,
storage-init/layout/migrate-layout/backup/stop, start, stop, mcp (toàn bộ), mcp-gates,
journal (status/purge), export, export-db, import, import-db, sync code stop / doc stop,
migrate, db` — và **`sync code` / `sync all` / `sync doc`** (wire `cortex-sync`/`cortex-doc`
ở phase-04; worker analyzer con mặc định Rust binary qua `CORTEX_RUST_ANALYZER` auto).

Ngoại lệ được ghi tên (forced, xem bảng inventory): torch device probe (dời vào cortex-sync),
`dev harness` wrapper spawn project-script Python (không có Rust), embed sidecar mặc định
`python` (cửa umbrella). `.venv` **không còn điều kiện bắt buộc** cho bất kỳ lệnh trên —
`make ort-ensure` cũng chạy native `cortex-dev ensure-ort` (validate 2026-09-14).
Từ phase-06 entrypoint là **binary-only, không có nhánh python rollback** (validate) —
downgrade = cài lại release binary trước đó (runbook ghi).

## Non-goals

- Không xoá `cortex_harness/` hay flip default embedder/analyzer/MCP-server runtime
  (thuộc umbrella phase-14 runbook + B.4 archive).
- Không đổi CLI surface: `spec.rs` byte-compatible — command names, options, output text shape.
- Không đụng Makefile target parity/embed (`rust-fixtures`, `embed-*`, `rust-pyo3`,
  `journal-shadow-diff`) — Python reference có chủ đích; biến `PYTHON` giữ cho target đó.
- Không port `.harness/scripts/*` (không có Rust thay thế — forced).

## Phase map (3 waves / 7 phases — rev 2)

| Phase | Wave | Scope |
|---|---|---|
| 01 | A | Native env: `status_env`/`code_env`/`doc_env`/`mcp_env` qua `cortex-storage` (+ mở rộng parity harness) |
| 02 | A | Process ops native: `stop_sync_workers`, embedded falkordb, `mcp_pids/uptime/stop/start` (+ parity harness) |
| 03 | A | Journal + `db_transfer` native: `journal_status/purge`, `db_export/import` (+ parity harness) → `pyexec.rs` hết caller |
| 04 | A | **Wire `dev sync` → `cortex-sync`/`cortex-doc`**: bỏ spawn `incremental_sync.py`, doc-tiny ingest, journal-consumer python recovery, bảng `LANG_ANALYZERS` .py; dời torch probe vào cortex-sync (validate: giữ trong plan) |
| 05 | B | Port `mcp-lifecycle.py` → `lifecycle.rs` từng action (14 actions gồm `storage-stop`); build = cargo + native `ensure-ort`; launcher theo Decision D1; doctor bỏ python checks |
| 06 | C | Entrypoint swap binary-only: **10 artifacts** (dev.sh/bat/ps1, dev-global.cmd, install-windows.bat/ps1 + scoop shim + pip -e, wrapper.bat, inno shortcuts, pyproject console script, launcher generator, Makefile) — không có python rollback; rewrite CI suites; runbook row + downgrade path |
| 07 | C | Cleanup/archive: xoá `pyexec.rs`, archive `mcp-lifecycle.py`, **xoá `mcp-lifecycle.ps1`**, dev.py → parity-reference-only, CI bridge-ban, docs |

Wave A (01–04) tuần tự (cùng đụng `pyexec.rs`/`sync.rs`); 05 song song được từ 02;
06 sau 01–05 pass gate, bên trong dogfood window umbrella; 07 sau 1 release ổn định của 06.

## Key decisions

- **D1 (frozen — launcher contract, rev 2 sau validate)**: launcher + 10 entrypoint artifacts
  resolve binary theo thứ tự (1) `CORTEX_DEV_BIN` env, (2) installed prefix `<prefix>/bin/cortex-dev[.exe]`,
  (3) repo `rust/target/release/`; **không tìm thấy binary → error + hint cài đặt** (không có
  nhánh python — validate: bỏ rollback ngay phase-06). Cả phase-05 (generator) và phase-06
  (entrypoint scripts) implement đúng spec này — hết circular dependency (red-team #13).
- **D2 (parity gate)**: mỗi phase trước khi port op/action phải **mở rộng `dev_cli_parity.py`
  fixtures cho đúng op/action đó** (hiện harness chỉ cover help-surface ~56 paths + status/doctor/
  storage-layout/init/ignore; sync-stop, mcp ops, journal, db, lifecycle actions chưa có —
  red-team #6). Byte-parity đo trên máy provisioned; output chuẩn hoá timestamp/pid.
- **D3 (no python rollback — validate 2026-09-14)**: từ phase-06 entrypoint là binary-only;
  flag `CORTEX_DEV_BACKEND` **không tồn tại**. Downgrade path = cài lại release binary trước
  (installer giữ previous release); runbook ghi quy trình. Khép finding red-team #14
  (rollback decay) bằng cách xoá hẳn rollback thay vì precondition.
- **D4 (CI)**: `tests/test_make_lifecycle.py:115,148,165` + `tests/test_dev_lifecycle_commands.py:141`
  assert Python dispatch — phase-06 **rewrite** các suite này theo dispatch binary; workflow
  `lifecycle-macos.yml` trigger path giữ vì vẫn watch dev.py (parity reference).

## Risks

| Rủi ro | Mitigation |
|---|---|
| `_code_env_for_process`/`_doc_env_for_process` semantics (overlay + topology fingerprint) | Fixture capture JSON 3 config thật; diff từng key; gate phase-01 chặn |
| Wire `dev sync` → cortex-sync đổi hành vi orchestration (không chỉ env) | Parity: 1 sync run end-to-end Python vs Rust trên stock, diff graph-node counts + journal events (dùng `make journal-shadow-diff` có sẵn) |
| `mcp_start` contract pid-file/instance/log-redirect | Port y nguyên; `dev mcp status` là test sống; parity harness mở rộng (D2) |
| ORT provisioning hụt làm cortex-embed gãy runtime | Native `ensure-ort` pin version y hệt wheel Python đang dùng; gate phase-05 có case máy sạch (download thật) + case `.cache/ort`/`ORT_DYLIB_PATH` đã có (no-network OK) |
| Doctor bỏ python checks → output delta vs Python (validate: chấp nhận) | Parity harness normalise/skip python checks; docs liệt kê checks của Rust doctor; kiểm tra doctor-init scripts trước flip |
| Windows (10 entrypoint artifacts + ps1) | Phase-06 smoke Windows bắt buộc trước flip; ps1 xoá ở 07 sau khi 06 ổn |
| Entrypoint flip KHÔNG có python rollback (validate) — binary bug trên máy user phải hotfix Rust | Installer giữ previous release binary làm downgrade path; runbook ghi 2 bước downgrade; dogfood gate đóng trước khi archive Python (phase-07) |
| Entrypoint flip giữa dogfood window umbrella | Phase-06 là 1 điểm runbook `docs/cutover-runbook.md`; runbook cập nhật ngay tại phase-06 (không đợi 07 — red-team #16) |

## Gate tổng của plan

- [ ] `grep -rn "pyexec\|venv_python\|HELPER_SRC" rust/crates/cortex-dev/src/` → 0 match
      (torch probe đã dời vào cortex-sync ở phase-04)
- [ ] Shell không `.venv`: `dev init status doctor storage-init` + `make build install`
      (gồm native ensure-ort provision thật) chạy, không spawn python (ps/trace probe)
- [ ] `dev sync code` end-to-end qua `cortex-sync` binary: graph counts + journal events
      khớp Python reference (shadow-diff)
- [ ] `dev_cli_parity.py` mở rộng theo D2, pass toàn bộ op/action đã port
- [ ] `make export-db` → `make import-db` round-trip qua binary
- [ ] 10 entrypoint artifacts swap binary-only (không còn flag `CORTEX_DEV_BACKEND`);
      downgrade-path qua previous release test được; CI suites rewritten xanh
- [ ] phase-07: `scripts/mcp-lifecycle.py` + `ensure_ort.py` archived, `mcp-lifecycle.ps1` xoá,
      CI bridge-ban đỏ khi có vết bridge quay lại

## Báo cáo

Reports per phase + `reports/red-team-rev1.md` (verbatim findings của red-team rev 1) trong
`plans/260914-2259-dev-make-python-cutover/reports/`.
