# Phase 01 report — Native env ops (status_env / code_env / doc_env / mcp_env)

**Ngày:** 2026-09-15 • **Branch:** `feat/change-db`

## Đã làm

1. **Module mới `rust/crates/cortex-dev/src/env.rs`** — port native toàn bộ env layer:
   - `_code_env_for_process` / `_doc_env_for_process` (stringify env, legacy-key filter,
     overlay merge, PROJECT_ID/QDRANT_*/EMBED_* setdefaults, CORTEX_HARNESS_CONFIG_PATH,
     provider isolation) gọi thẳng `cortex_storage::config::{resolve_storage, storage_overlay}`.
   - `_storage_targets` + `_isolate_graph_provider_environment` (generic qua trait
     `EnvMapOps` cho cả `serde_json::Map` payload lẫn `BTreeMap` ConfigMap) +
     `_active_config_path_for_process` (phiên bản không-exit) + `resolve_start_config`
     (walk-up tìm `.cortext-harness/config/dev.json`, fallback harness root) cho `_mcp_env_from_config`.
   - `status_env_payload` — payload JSON của op `status_env` cho `dev status`.
   - Python truthiness (`py_falsy`) + `str()` rendering (`py_str`: `True`/`False`, repr
     container) giữ nguyên semantics cho env value non-string.
   - **torch device probe** dời về đây (từ `sync.rs::python_resolve_device`), cache
     `OnceLock` 1 lần/process, chỉ probe khi device ∈ {auto, mps, cuda} — forced-Python
     được whitelist của plan.
2. **Rewire call sites** (không đổi hành vi quan sát được):
   - `cmds/status.rs` → `env::status_env_payload` (bỏ `call_json("status_env")`).
   - `cmds/sync.rs` → 3× `code_env` + 2× `doc_env` → `env::code_env/doc_env`; xoá
     `normalize_embed_device`/`python_resolve_device` bản cục bộ → `env::normalize_embed_device`.
   - `cmds/mcp.rs` (`start_rust_one`) → `env::mcp_env_from_config` (bỏ `try_call_json("mcp_env")`).
3. **Parity hook**: `CORTEX_DEV_PARITY_ENV=code|doc|status` + `CORTEX_DEV_PARITY_PROJECT`
   (env-gated, không đụng CLI surface) in payload JSON sort-keys — để harness đối chiếu.
4. **D2 — parity harness mở rộng**: `dev_cli_parity.py` thêm **GATE 6** — env payload
   per-key Python vs Rust trên 3 config shapes: stock falkordb / ladybug / remote-backend.

## Gate结果 (phase-01.md)

- [x] Fixture capture 3 config (stock, ladybug, remote) → **GATE 6 PASS 6/6** (mọi key khớp).
- [x] `dev status` byte-khớp: `diff` python vs rust **rỗng** trên repo thật (ladybug provider) + GATE 2 fixture PASS.
- [x] `dev sync code --dry-run` trên stock sau khi sync.rs native: env thấy bởi analyzer
      subprocess = cùng payload GATE 6 đối chiếu (ladybug path resolve đúng qua cortex-storage).
- [x] Parity harness mở rộng pass (GATE 6 mới; tổng 72/73 — mục dưới).
- [x] `cargo test -p cortex-dev` → 23/23 pass.
- [x] `grep` call sites của 4 op env trong `cmds/` + `main.rs` → **0** (HELPER_SRC giữ
      nguyên trong pyexec.rs, xoá ở phase-07).

## Delta / exception ghi nhận

- **GATE 1 root-case FAIL (pre-existing, không do phase này)**: Rust có thêm subcommand
  `migrate` mà dev.py không có — thêm bởi umbrella phase-14B (commit `e3fcd3c`, cutover
  tooling). Chấp nhận: lệnh cutover chỉ tồn tại phía Rust. Không sửa trong plan này.
- `CORTEX_DEV_PARITY_ENV` hook: bề mặt env-gated mới chỉ phục vụ parity harness,
  không nằm trong help/spec.

## Kết luận

**PASS** — 4 op env hết caller Python-bridge; tầng env/dựng payload chạy 100% native.
