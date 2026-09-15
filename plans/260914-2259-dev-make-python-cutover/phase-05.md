# Phase 05 — Wave B: Port `scripts/mcp-lifecycle.py` (2147 LOC, 14 actions) → `cmds/lifecycle.rs`

## Scope

Port theo **từng action**, không big-bang. Action chưa port vẫn shim `run_lifecycle()` sang
Python (behavior không đổi trong lúc chuyển). Mỗi action 1–2 PR + output-parity test (D2).
Có **14 actions** trong `ACTIONS` (`mcp-lifecycle.py:2031-2045`) — rev 1 sót `storage-stop`
(red-team #12), bảng dưới liệt kê đủ:

| # | Action | Nội dung chính | Ghi chú |
|---|---|---|---|
| 1 | `help` | bảng help text | byte-khớp dễ test |
| 2 | `doctor` | các check Rust thật sự dùng (config, storage, graph, qdrant, binary presence); **bỏ python/venv checks** (validate 2026-09-14) | Output delta vs Python được chấp nhận; parity harness normalise; docs liệt kê checks mới |
| 3 | `start` / `stop` | gọi `dev mcp start/stop` semantics | sau phase-02 có native `mcp_start` |
| 4 | `infra-up` / `infra-down` | docker compose up/down (nhánh remote + `--provision`) | shell-out `docker` CLI hợp lệ — không phải Python dep |
| 5 | `storage-init` / `storage-layout` / `storage-migrate-layout` / `storage-backup` / `storage-stop` | layout init/migrate/backup/stop | tái dùng `cortex-storage` native |
| 6 | `build` | **đổi semantics có chủ đích**: `cargo build --release --workspace`; venv/pip bootstrap bỏ; work item riêng **`ensure-ort` native** (validate: port Rust — reqwest tải wheel PyPI pinned + unzip dylib vào `.cache/ort`, tôn trọng `ORT_DYLIB_PATH`; thay `scripts/ensure_ort.py`) — không âm thầm bỏ đường provision duy nhất của cortex-embed (red-team #3) | action duy nhất được phép đổi internals |
| 7 | `install` / `uninstall` | sinh launcher theo **Decision D1** (spec frozen trong plan.md — hết circular dep red-team #13) + context menu | làm sau khi D1 chốt |

## Gate

- [ ] Với mỗi action port: `cortex-dev <action>` output byte-khớp
      `python scripts/mcp-lifecycle.py <action>` trên stock (chuẩn hoá timestamp/pid).
- [ ] Đủ 14/14 actions native hoặc shim được liệt kê kèm lý do còn shim.
- [ ] `make build install doctor infra-up infra-down storage-init storage-backup start stop help`
      chạy qua native — xác nhận không spawn python (`ps` probe).
- [ ] Native `ensure-ort`: máy sạch download+provision thật OK; `.cache/ort`/`ORT_DYLIB_PATH`
      đã có → no-network OK; version pin khớp wheel Python đang dùng (cortex-embed load được).
- [ ] Case máy sạch: `make build` + `dev doctor` không fail vì thiếu venv (doctor đã bỏ python
      checks — validate); doctor output delta được ghi vào parity exception.
- [ ] Windows: `dev` lifecycle qua PowerShell entry vẫn hoạt động trong phase này (ps1 chỉ xoá ở 07).
- [ ] `cargo test -p cortex-dev` pass.

**Trạng thái:** draft (rev 2) — **PARTIAL 9/14 native 2026-09-15** (reports/phase-05.md)
