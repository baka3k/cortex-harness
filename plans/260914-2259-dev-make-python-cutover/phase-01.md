# Phase 01 — Wave A: Wire native storage/config env (bỏ ops `status_env`, `code_env`, `doc_env`, `mcp_env`)

## Scope

Nối `cmds/*` của `cortex-dev` vào crate `cortex-storage` + `config.rs` **native**, thay
`pyexec::call_json` — không port logic mới (crate đã port đủ từ umbrella phase-10 Scope B).

1. **`status_env`** → `cmds/status.rs`: dựng payload
   `{config_name, project, code_env_raw, doc_env_raw, code_env, doc_env, code_provider, doc_collection}`
   từ `config::load_active_config` + storage resolution native. Đây là op duy nhất trả JSON
   nhiều field — port 1:1 key order (status output text phải byte-khớp).
2. **`code_env` / `doc_env`** → helper native `env::code_env_for_process(cfg, project_root)` /
   `env::doc_env_for_process(...)` trong `cortex-dev` (module mới `src/env.rs`), wrap
   `cortex_storage::` resolve + overlay + topology fingerprint. Giữ hệ quả phụ:
   `CORTEX_HARNESS_CONFIG_PATH` được gắn vào payload như HELPER_SRC đang làm (`pyexec.rs:184,189`).
   `cmds/sync.rs` (~20 call site) chuyển sang gọi native.
3. **`mcp_env`** → `cmds/mcp.rs`: port `_mcp_env_from_config` (đọc section env + merge
   service-specific) sang native; 5 call site trong `mcp.rs` dùng lại.

## Không được làm

- Không sửa semantics của storage resolution — nếu phát hiện bug Python, ghi lại report,
  giữ parity với Python trước, fix riêng (pattern cũ của chương trình Rust).

## Work item chung (D2 — làm trước khi port)

- **Mở rộng `scripts/rust_parity/dev_cli_parity.py`**: thêm fixture cho `status` env payload
  (hiện harness chỉ byte-compare status/doctor/storage-layout text + help-surface; chưa có
  so JSON env). Capture Python output làm golden trước khi chuyển native.

## Gate

- [ ] Fixture capture: chạy Python HELPER_SRC với 3 config thật (stock, stock_doc, 1 remote
      backend) → golden JSON; Rust trả JSON khớp từng key.
- [ ] `dev status` output text byte-khớp trước/sau (diff trên stock).
- [ ] `dev sync code` dry-run/end-to-end trên stock sau khi sync.rs chuyển native — env thấy
      bởi analyzer subprocess giống hệt (`printenv` probe trong parity harness).
- [ ] Parity harness mở rộng (D2) pass cho op env.
- [ ] `cargo test -p cortex-dev` pass.
- [ ] `grep -c "status_env\|\"code_env\"\|\"doc_env\"\|mcp_env" pyexec.rs` — 4 op còn trong
      HELPER_SRC (xoá ở phase-07), nhưng **0 call site** còn trỏ tới.

**Trạng thái:** draft (rev 2)
