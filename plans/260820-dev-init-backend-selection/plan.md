---
title: "dev init — Local DB / Remote Server Selection"
status: active
created: 2026-08-20
updated: 2026-08-20
mode: hi-plan --full
scope: cortex_harness/dev.py, tests/test_dev_init_*
relatedPlans:
  - 260817-storage-backend-adapter
  - 260818-infra-up-remote-support
blockedBy: []
blocks: []
---

# dev init — Local DB / Remote Server Selection

## Overview

Backend adapter (plan 260817) và remote-aware infra-up/doctor (plan 260818) đã
được implement, nhưng `dev init` không prompt và không ghi `storage_backend` /
`remote` vào config — người dùng phải sửa tay `.cortext-harness/config/{env}.json`
để dùng remote. Plan này bổ sung bước chọn backend trong luồng `dev init`:

- Prompt `storage_backend: local | remote` (default `local`, hoặc lấy từ config
  hiện có khi re-init).
- Khi chọn `remote`: prompt `qdrant_url`, `qdrant_api_key` (optional),
  `falkordb_uri`, `falkordb_password` (optional), `falkordb_ssl` — ít nhất một
  trong `qdrant_url` / `falkordb_uri` bắt buộc.
- **Không validate connectivity** khi init (quyết định user) — config chỉ được
  ghi; gợi ý chạy `make infra-up` / `dev doctor` để kiểm tra.
- Secrets lưu **plaintext trong config JSON** (quyết định user), nhất quán với
  schema `remote` hiện tại mà registry + `resolve_storage()` đã đọc.

## Verified Current State

- `dev init` implemented tại `cortex_harness/dev.py:2184-2391` (click). Chuỗi
  prompt hiện tại: project code/name → storage instance/data home → graph
  provider per-section → qdrant/embedding settings → source projects. Ghi JSON
  `{"active", "project", "code", "doc"}` — **không** ghi `storage_backend`/`remote`.
- Registry (`code-tiny/tools/common/project_registry.py:187-220`) đã đọc top-level
  `storage_backend` (default `"local"`) và `remote` section vào `ProjectTargets`.
- `cortex_harness/storage/config.py`: `BackendMode`, `RemoteStorageConfig`,
  `validate_backend_config()` (line 116) — yêu cầu remote mode có ≥1 của
  `qdrant_url`/`falkordb_uri`; reject legacy env keys (`QDRANT_URL`, `FALKORDB_URI`...).
- `resolve_storage()` đọc `cfg["storage_backend"]` / `cfg["remote"]` (line 345-365).
- Factory + probe + provisioning đã implement (`storage/factory.py`,
  `storage/remote_probe.py`, `scripts/mcp-lifecycle.py` infra-up).
- Tests hiện có: `tests/test_dev_init_graph_provider.py` (15 tests, có test reject
  legacy remote keys trong env), `tests/test_backend_config.py`.

## Decisions

| Quyết định | Chọn |
|---|---|
| Validate connectivity khi init | Không — chỉ ghi config, hint chạy `dev doctor`/`make infra-up` |
| Secrets | Plaintext trong config JSON (field `remote` top-level) |
| Phạm vi | Chỉ `dev init`; không thêm lệnh set-backend riêng |

## Implementation Phases

- [phase-01-storage-backend-prompt.md](phase-01-storage-backend-prompt.md) — prompt + ghi config
- [phase-02-tests.md](phase-02-tests.md) — unit tests cho init flow mới
- [phase-03-docs.md](phase-03-docs.md) — cập nhật README/docs + cross-plan log

## Out of Scope

- Lệnh đổi backend cho project đã tồn tại (`dev config set-backend`).
- Connectivity probing / provisioning trong init (đã có ở `make infra-up --provision`).
- Quản lý secret (env var, keychain, gitignore policy ngoài现状).

## Risks

- Ghi `remote` khi user để trống cả URL → phải validate ≤ init-level (yêu cầu
  ít nhất 1 URL) trước khi ghi, tránh config remote invalid mà `resolve_storage()`
  sẽ reject lúc runtime.
- Re-init một project remote hiện có: defaults phải đọc từ `remote` section cũ
  để không vô tình downgrade về local.
