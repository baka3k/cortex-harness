---
title: "infra-up Remote Support — Qdrant & FalkorDB Server Lifecycle"
status: active
created: 2026-08-18
updated: 2026-08-25
mode: hi-plan (fast)
scope: scripts/mcp-lifecycle.py, cortex_harness/storage, cortex_harness/dev.py, tests
relatedPlans:
  - 260820-falkordb-ui-url-doctor
  - 260817-storage-backend-adapter
  - 260820-infra-up-docker-idempotent
  - 260806-1648-local-file-storage
  - 260820-dev-init-backend-selection
  - 260820-doctor-caller-config
  - 260821-2115-dev-sync-code-windows (consumes local Docker FalkorDB for Windows sync)
  - 260829-2322-vector-search-query-optimization (its benchmark has a --live mode over remote Qdrant configured via this plan's lifecycle)
blockedBy: []
blocks: []
---

# infra-up Remote Support — Qdrant & FalkorDB Server Lifecycle

## Overview

`make infra-up` hiện tại là deprecated alias cho `storage-init` — chỉ tạo
thư mục local trên disk. Sau khi plan `260817-storage-backend-adapter` đã
thêm adapter layer cho remote backend (Qdrant server + FalkorDB server),
`infra-up` cần được nâng cấp thành **smart lifecycle command** hỗ trợ cả
local lẫn remote:

- **Local projects**: giữ nguyên behavior hiện tại (tạo instance tree).
- **Remote projects**: validate connectivity tới Qdrant/FalkorDB servers,
  optionally provision collections/graphs, report status.
- **Doctor**: mở rộng để check remote connectivity cho các project dùng
  `storage_backend: "remote"`.

## Verified Current State

### infra-up (deprecated alias)

- `scripts/mcp-lifecycle.py` `invoke_infra_up()` chỉ gọi `invoke_storage_init()`
  và in deprecation warning.
- `invoke_storage_init()` chỉ gọi `ensure_layout(resolved)` để tạo local
  directories + manifest.
- `invoke_infra_down()` là no-op, chỉ in deprecation warning.

### Storage Backend Adapter (plan 260817 — active)

- `BackendMode.LOCAL/REMOTE` enum đã tồn tại trong `cortex_harness/storage/config.py`.
- `RemoteStorageConfig` dataclass chứa `qdrant_url`, `qdrant_api_key`,
  `falkordb_uri`, `falkordb_password`, `falkordb_ssl`.
- `StorageFactory` resolve backend từ `ProjectTargets` — trả về
  `LocalQdrantStore` hoặc `RemoteQdrantStore`.
- `RemoteQdrantStore` có `check_connection()` và `ensure_reachable()`.
- `FalkorDBDriver` đã accept `uri` + `password` + `ssl` cho remote mode.
- `ResolvedStorage` có `backend_mode` và `remote` fields.

### Doctor

- `invoke_doctor()` check local paths writable, Qdrant local round-trip,
  FalkorDBLite round-trip.
- Không có remote connectivity check.

### Project Registry

- `ProjectTargets` có `storage_backend` (str, default "local") và
  `remote_config` (Optional[Dict]).
- `ProjectRegistry` load từ `.cortext-harness/config/*.json`.

## Target Architecture

### infra-up Command (un-deprecate + extend)

```
make infra-up
    │
    ├─ Scan all project configs (.cortext-harness/config/*.json)
    │
    ├─ Partition: local projects vs remote projects
    │
    ├─ Local projects:
    │     └─ invoke_storage_init() (existing behavior)
    │
    └─ Remote projects:
          │
          ├─ For each remote project:
          │     ├─ Resolve StorageFactory
          │     ├─ Qdrant: check_connection() / ensure_reachable()
          │     ├─ FalkorDB: ping via driver
          │     └─ Report status
          │
          └─ Optional --provision:
                ├─ Create Qdrant collections (if not exist)
                └─ Setup FalkorDB graphs + indexes
```

### infra-down Command (new remote lifecycle)

```
make infra-down
    │
    ├─ Local projects: no-op (existing)
    │
    └─ Remote projects:
          ├─ Close cached remote clients
          └─ Report disconnection
```

### Doctor Extension

```
make doctor
    │
    ├─ (existing local checks)
    │
    └─ Remote project checks (new):
          ├─ Qdrant server reachable?
          ├─ FalkorDB server reachable?
          └─ Required collections/graphs exist?
```

## Cross-Plan Dependencies

### `260817-storage-backend-adapter` (active)

Plan này **consumes** adapter layer từ storage-backend-adapter:
- Dùng `StorageFactory`, `RemoteQdrantStore.check_connection()`,
  `FalkorDBDriver` remote mode.
- Không thay đổi adapter code — chỉ thêm lifecycle command layer.

Bidirectional update: `260817-storage-backend-adapter` cần note rằng
`infra-up` là entrypoint cho remote provisioning.

### `260806-1648-local-file-storage` (completed)

Không conflict. Local behavior giữ nguyên.

### `260820-dev-init-backend-selection` (active)

Plan này **writes the config** mà `infra-up` đọc: `dev init` wizard đã
bổ sung prompt `storage_backend: local|remote` và `remote` section
(Qdrant URL / FalkorDB URI + credentials). Sau init, operator chạy
`make infra-up` (đã hỗ trợ remote từ plan này) để verify + provision.
Không thay đổi behavior của `infra-up` / `doctor`; chỉ đảm bảo config
đầu vào tồn tại và hợp lệ (`validate_backend_config()`).

## Phases

1. [Phase 01 — Remote-aware infra-up command](phase-01-remote-infra-up.md)
2. [Phase 02 — Remote connectivity probe & provisioning](phase-02-remote-provisioning.md)
3. [Phase 03 — Doctor remote checks](phase-03-doctor-remote.md)
4. [Phase 04 — Testing & validation](phase-04-testing.md)

## Expected File Changes

### Lifecycle Scripts
- `scripts/mcp-lifecycle.py`:
  - `invoke_infra_up()` — un-deprecate, scan configs, route local/remote
  - `invoke_infra_down()` — un-deprecate, close remote clients
  - New: `invoke_remote_probe()` — connectivity check per project
  - New: `invoke_remote_provision()` — create collections/graphs
  - `invoke_doctor()` — add remote connectivity checks

### Dev CLI
- `cortex_harness/dev.py`:
  - `infra_up` command — add `--provision` flag
  - `infra_down` command — update docstring

### Storage Layer
- `cortex_harness/storage/remote_probe.py` (new) — shared connectivity probe
  cho cả lifecycle scripts và doctor

### Tests
- New: `tests/test_infra_remote.py` — remote lifecycle tests
- Update: `tests/test_make_lifecycle.py` — infra-up no longer deprecated
- Update: `tests/test_storage_lifecycle.py` — remove deprecation assertion

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Remote server unreachable khi chạy `infra-up` | Report per-project status, không block local projects. Exit code phản ánh số project failed. |
| `--provision` tạo collection sai config | Dry-run by default; `--apply` flag để thực sự tạo. Hoặc ngược lại: `--provision` explicit opt-in. |
| Project config chưa có `storage_backend` field | Default `"local"` — backward compatible, không ảnh hưởng project cũ. |
| FalkorDB remote driver ping semantics khác local | Dùng `graph.query("RETURN 1")` — works cho cả local và remote. |
| `infra-down` đóng client đang được MCP tools dùng | `infra-down` chỉ đóng lifecycle-owned clients; MCP tools quản lý client riêng. |

## Success Criteria

- `make infra-up` trên project config `storage_backend: "local"` hoạt động
  identical hiện tại (tạo instance tree).
- `make infra-up` trên project config `storage_backend: "remote"` validate
  connectivity tới Qdrant server và FalkorDB server, report rõ ràng.
- `make infra-up --provision` tạo collections/graphs trên remote servers
  khi chưa tồn tại.
- `make infra-down` đóng remote client connections gracefully.
- `make doctor` check remote connectivity cho các project dùng remote backend.
- Backward compatible: project configs không có `storage_backend` field
  hoạt động như `local`.
- Test suite cover: local routing, remote probe, remote provision, doctor
  remote checks, mixed local/remote projects.

## 2026-08-25 Persistence Hardening

An incident exposed two defects in the Docker ensure path: Docker's
`<container-port>/tcp` keys were parsed backward, causing healthy containers
to be classified as missing all published ports, and the FalkorDB named volume
was mounted at `/data` while Redis writes to `/var/lib/falkordb/data`. The
remediation corrects both contracts and waits for a Redis `PING` response
before the immediate remote probe. The synchronous lifecycle probe also uses
`execute_query_sync()` rather than dropping the coroutine returned by
`execute_query()`. Regression coverage pins the real Docker inspect JSON
shape, the exact FalkorDB mount target, readiness behavior, and a real query
failure result.

## Implementation Handoff

After review, implement with:

```text
/hi-craft plans/260818-infra-up-remote-support/plan.md
```
