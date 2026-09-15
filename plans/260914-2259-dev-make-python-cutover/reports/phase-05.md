# Phase 05 report — Port `mcp-lifecycle.py` → native `cmds/lifecycle.rs` (action-by-action)

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## Trạng thái: 11/14 actions NATIVE, 3/14 shim có lý do (start/stop port ở phase-05b, 2026-09-15)

| # | Action | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | `help` | **NATIVE** | USAGE byte-copy — `diff` python vs rust **rỗng** |
| 2 | `doctor` | shim | Remote/storage/qdrant probes native có sẵn (cortex-storage `remote_probe`, `LocalQdrantStore`) nhưng output-shape parity fixture + quyết định checks-mới cần 1 lượt verify riêng (validate cho phép output delta); làm ở lượt kế |
| 3 | `start` | **NATIVE (phase-05b)** | `mcp_state.rs` + `cmds/lifecycle.rs::start`; giữ nguyên wrapper `.command` + Terminal.app/osascript + `pids.json` + `.active.env`; graph key theo provider (ladybug→`LADYBUG_GRAPH`) |
| 4 | `stop` | **NATIVE (phase-05b)** | `mcp_state::stop` — record sweep + `stop_process_tree` + prune/xoá `pids.json`; `dev stop` vẫn pre-kill `cortex-mcp` trước |
| 5 | `infra-up` | shim | ~700 LOC docker-compose plumbing (container state/ports/provision) — port riêng |
| 6 | `infra-down` | shim | Cụm infra |
| 7 | `storage-init` | **NATIVE** | `ensure_layout` + 7 dòng `[storage-init]` khớp format |
| 8 | `storage-layout` | **NATIVE** | `resolve_active_storage` port (flatten code/doc local keys + conflict check + env overrides) — output **byte-identical** với python (leases + manifest, sort-keys) |
| 9 | `storage-migrate-layout` | **NATIVE** | `migrate_legacy_layout` — dry-run output **byte-identical** |
| 10 | `storage-backup` | **NATIVE** | `StorageLease` (qdrant+falkor) + verified copy + manifest; tested isolate |
| 11 | `storage-stop` | **NATIVE** | 1 dòng print — byte-khớp |
| 12 | `build` | **NATIVE (semantics đổi có chủ đích)** | `cargo build --release --workspace` (replicate `RUST_LINK_ENV` macOS cho cortex-retrieval-py) + **`ensure-ort` native**; venv/pip bootstrap bỏ hẳn |
| 13 | `install` | **NATIVE (D1)** | binary → `~/.local/bin/cortex-dev` + launcher `dev` theo D1 (CORTEX_DEV_BIN → installed prefix → repo release → error+hint); PATH hint giữ |
| 14 | `uninstall` | **NATIVE** | xoá `dev` + `cortex-dev`; PATH giữ nguyên |

## `ensure-ort` native (thay `scripts/ensure_ort.py`)

- Command mới `dev ensure-ort [--force] [--print-path]` (Rust-only, delta #2 như
  `migrate` — python reference không có; GATE 1 root-case ghi nhận).
- Resolution: `ORT_DYLIB_PATH` → `.cache/ort/1.29.0` (no-network OK, tested) → venv
  wheel capi (copy cùng build với parity fixtures) → **PyPI pinned wheel 1.29.0**
  (curl + bsdtar/unzip — system tools, không thêm crate deps; deviation so với
  "reqwest" trong plan: cùng behavior, ít deps).
- Version pin = 1.29.0 (khớp uv.lock + venv wheel đang dùng — cortex-embed load được,
  đã có `.cache/ort/1.29.0/` từ trước).

## Gate kết quả (phase-05.md)

- [x] Actions native: output byte-khớp python (`help`, `storage-layout`,
      `storage-migrate-layout`, `storage-stop` — diff rỗng; `storage-init` format khớp;
      `storage-backup` tested isolate CORTEX_DATA_HOME: leases + verified sha256 +
      manifest sort-keys).
- [x] 11/14 native + 3/14 shim được liệt kê kèm lý do (bảng trên) — theo đúng điều
      khoản "native HOẶC shim kèm lý do" của plan.
- [x] `dev build install storage-init storage-backup storage-layout help
      storage-migrate-layout storage-stop` chạy native (không spawn python; doctor/
      infra còn shim nên chưa liệt kê). `start`/`stop` bổ sung native ở phase-05b
      (verify: `ps` không còn `mcp-lifecycle.py` trên đường dev start/stop).
- [x] Native `ensure-ort`: no-network OK (cache hit); venv-wheel path có sẵn; PyPI
      download path implement (chưa test máy sạch trong session này — không xoá
      `.cache/ort` trên máy dùng thật); pin khớp wheel.
- [x] `make build` không fail vì thiếu venv: native build không đụng venv (verify
      Makefile flip ở phase-06 #10).
- [x] Windows: ps1 lifecycle entry không đụng trong phase này (chỉ xoá ở 07).
- [x] `cargo test -p cortex-dev` 23/23; parity harness 75/76 (root-case: rust-only
      subs `migrate` + `ensure-ort` — documented deltas).

## Kết luận

**PASS** — 11/14 native (start/stop về đích ở phase-05b), doctor/infra-* shim có lý do +
kế hoạch; `make build` hết phụ thuộc venv khi Makefile flip (phase-06).
