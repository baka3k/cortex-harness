---
title: "Storage Backend Adapter — Local File ↔ Server URL Switching"
status: active
created: 2026-08-17
updated: 2026-08-17
mode: hi-plan --full
scope: cortex_harness/storage, code-tiny/tools/graph, code-tiny/mcp, doc-tiny, project config schema
relatedPlans:
  - 260806-1648-local-file-storage
  - neo4j-to-falkordb-migration
  - 260728-0000-unified-ingest-query-contract
  - 260820-dev-init-backend-selection
blockedBy: []
blocks: []
---

# Storage Backend Adapter — Local File ↔ Server URL Switching

## Overview

Cortex Harness hiện tại chỉ hỗ trợ local file storage (Qdrant local mode +
FalkorDBLite `.rdb`). Plan `260806-1648-local-file-storage` đã thiết lập contracts
cho owner-scoped embedded storage nhưng explicitly noted rằng remote backend
có thể được reintroduce later.

Plan này thiết lập **adapter layer** cho phép:

- **Per-project backend selection**: Mỗi project trong `.cortext-harness/config/*.json`
  có thể chọn `storage_backend: "local"` (default) hoặc `storage_backend: "remote"`.
- **Transparent MCP layer**: MCP tools (`graph_mcp`, `mind_mcp`) gọi adapter factory
  và không cần biết backend là file hay server.
- **Transparent storage layer**: Ingest, query, reset, cleanup scripts không thay đổi —
  chúng nhận được cùng một interface bất kể backend.
- **Self-hosted server support**: Remote mode hỗ trợ Qdrant server (Docker/K8s)
  và FalkorDB server (Docker/K8s) qua URL + optional credentials.

## Verified Current State

### Qdrant

- `cortex_harness/storage/qdrant.py` cung cấp `LocalQdrantStore` — chỉ dùng
  `QdrantClient(path=...)` local mode.
- `get_client(resolved, role)` cache client per path, acquire `StorageLease`.
- Không có remote URL mode; `LEGACY_REMOTE_KEYS` trong `config.py` chứa
  `QDRANT_URL`, `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_API_KEY` nhưng bị coi
  là legacy và emit error nếu xuất hiện trong local config.
- MCP tools gọi `LocalQdrantStore` hoặc `get_client()` trực tiếp.

### FalkorDB

- `code-tiny/tools/graph/driver/falkordb_driver.py` đã có **cả local và remote**:
  - Local: `FalkorDBDriver(path="/path/to/data.rdb", graph="hyper_graph")`
  - Remote: `FalkorDBDriver(uri="redis://...", host=..., port=...)` — accepted
    nhưng emit deprecation warning khi `path` được cung cấp.
- Constructor chấp nhận cả hai mode nhưng logic ưu tiên local khi có `path`.
- Không có factory function để chọn mode từ config.

### Config

- `.cortext-harness/config/*.json` có schema:
  ```json
  {
    "project": {"code": "project_id", "name": "...", "parser_type": "..."},
    "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
    "doc": {"env": {}}
  }
  ```
- Không có field `storage_backend` hoặc remote URL fields.
- `ProjectRegistry` load config on every call, trả về `ProjectTargets`.

### Storage Resolution

- `cortex_harness/storage/config.py` `resolve_storage()` chỉ resolve local paths.
- `ResolvedStorage` dataclass chứa `qdrant_code_path`, `qdrant_doc_path`,
  `falkordb_path` — tất cả là `Path` objects cho local filesystem.
- Không có field cho remote URL/credentials.

## Target Architecture

### Backend Mode Enum

```python
class BackendMode(str, Enum):
    LOCAL = "local"    # Qdrant local file + FalkorDBLite .rdb
    REMOTE = "remote"  # Qdrant server URL + FalkorDB server URI
```

### Per-Project Config Schema Extension

```json
{
  "project": {"code": "my_project", "name": "My Project"},
  "storage_backend": "local",
  "remote": {
    "qdrant_url": "http://localhost:6333",
    "qdrant_api_key": null,
    "falkordb_uri": "redis://localhost:6379",
    "falkordb_password": null
  },
  "code": {"env": {}},
  "doc": {"env": {}}
}
```

- `storage_backend` default: `"local"` (backward compatible).
- `remote` section chỉ required khi `storage_backend == "remote"`.
- Credentials optional (self-hosted không cần API key nếu không có auth).

### Adapter Interface

```
┌─────────────────────────────────────────────┐
│           MCP Tools / Ingest Scripts        │
│   (call factory, don't know backend type)   │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼──────────┐
         │  StorageFactory    │
         │  .get_qdrant()     │
         │  .get_falkordb()   │
         └─────────┬──────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼────┐   ┌────▼─────┐  ┌────▼─────┐
│ Local  │   │  Remote  │  │  (future │
│Qdrant  │   │  Qdrant  │  │  backends│
│Store   │   │  Store   │  │  ...)    │
└────────┘   └──────────┘  └──────────┘
```

### Factory Resolution Flow

```
ProjectRegistry.resolve_project_targets(project_id)
    │
    ▼
ProjectTargets (includes storage_backend + remote config)
    │
    ▼
StorageFactory(targets)
    │
    ├─ if backend == LOCAL:
    │     return LocalQdrantStore(resolved, role)
    │     return FalkorDBDriver(path=..., graph=...)
    │
    └─ if backend == REMOTE:
          return RemoteQdrantStore(url, api_key)
          return FalkorDBDriver(uri=..., graph=...)
```

## Scope Challenge Decisions

### 1. Config scope: per-project vs global vs hybrid

**Selected: per-project.** Mỗi project trong `.cortext-harness/config/*.json`
tự khai báo `storage_backend`. Lý do:
- User yêu cầu "có dự án dùng file, có dự án dùng server".
- Project config đã tồn tại, chỉ cần thêm field.
- Không cần global default vì `local` là implicit default.

### 2. Server deployment: self-hosted vs cloud vs both

**Selected: self-hosted (Docker/K8s).** URL + optional credentials.
- Không cần cloud-specific auth (API key management, OAuth).
- `qdrant_url` và `falkordb_uri` đủ cho self-hosted.
- Cloud users có thể dùng URL + API key qua `qdrant_api_key` field.

### 3. Migration strategy: re-ingest vs export/import vs dual-write

**Selected: re-ingest from source.** Khi chuyển backend, chạy lại ingest.
- Đơn giản nhất, không cần complex migration tooling.
- Acceptable downtime cho per-project switching.
- Data consistency được đảm bảo vì ingest từ source of truth.

## Cross-Plan Dependencies

### `260806-1648-local-file-storage` (completed)

Plan này **extends** local storage plan bằng cách:
- Giữ nguyên `LocalQdrantStore` và local FalkorDB path resolution.
- Thêm `RemoteQdrantStore` song song.
- Thêm `BackendMode` vào `ResolvedStorage` hoặc `ProjectTargets`.
- `LEGACY_REMOTE_KEYS` không còn bị reject mà được parse khi `backend == remote`.

Bidirectional update: `260806-1648-local-file-storage` cần note rằng remote
backend đã được reintroduce qua adapter pattern.

### `neo4j-to-falkordb-migration` (completed)

Plan này **reuses** FalkorDBDriver's existing remote mode support.
- `FalkorDBDriver` đã chấp nhận `uri/host/port` — chỉ cần factory tạo đúng mode.
- No changes needed to the driver itself.

### `260728-0000-unified-ingest-query-contract` (completed)

Plan này **extends** unified contract bằng cách:
- Thêm `storage_backend` và `remote` fields vào `ProjectTargets`.
- MCP tools tiếp tục dùng `project_id` làm key, factory resolve backend.

### `260820-dev-init-backend-selection` (active)

Plan này **enables** init UX cho backend adapter. Storage-backend-adapter
thêm `storage_backend` / `remote` schema + adapter layer, nhưng không
prompt hoặc ghi các field này — operator phải sửa tay `dev.json`. Plan
260820 đã bổ sung bước chọn backend trong `dev init` wizard (local/remote
+ Qdrant/FalkorDB endpoints) và validation init-level để chống ghi
config invalid. Plan này không cần update thêm; adapter API giữ nguyên.

## Red Team Review

See [red-team-findings.md](red-team-findings.md) for 5 critical, 6 high,
6 medium issues and their resolutions. Key fixes applied:

- **C2/C3:** Phase 04 expanded to refactor wrapper functions and enumerate
  all MCP backend modules.
- **C4:** Remote client cache keyed by `(url, api_key)` tuple.
- **C5:** Mixed backend mode falls back to local for missing component.
- **H5:** Remote client lifecycle with `atexit.register(reset_remote_clients)`.
- **Q6:** Emergency rollback via `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1`.

## Phases

1. [Phase 01 — Backend Mode Config Schema](phase-01-backend-mode-config.md)
2. [Phase 02 — Qdrant Remote Adapter](phase-02-qdrant-remote-adapter.md)
3. [Phase 03 — Storage Factory](phase-03-storage-factory.md)
4. [Phase 04 — MCP Integration](phase-04-mcp-integration.md)
5. [Phase 05 — Testing & Validation](phase-05-testing-validation.md)

## Expected File Changes

### Config Schema
- `cortex_harness/storage/config.py` — thêm `BackendMode`, remote config fields
- `code-tiny/tools/common/project_registry.py` — parse `storage_backend` + `remote`
- `tests/fixtures/unified_contract/config/` — test fixtures

### Qdrant Adapter
- `cortex_harness/storage/qdrant.py` — refactor thành base class + LocalQdrantStore
- New: `cortex_harness/storage/qdrant_remote.py` — RemoteQdrantStore

### FalkorDB Adapter
- `code-tiny/tools/graph/driver/falkordb_driver.py` — minor: factory classmethod
- New: factory method `FalkorDBDriver.from_config(targets)`

### Factory
- New: `cortex_harness/storage/factory.py` — StorageFactory
- `cortex_harness/storage/__init__.py` — export factory

### MCP Integration
- `code-tiny/mcp/unified_mcp.py` — use factory instead of direct client
- `doc-tiny/mcp_graph_rag.py` — use factory instead of direct client

### Tests
- New: `tests/test_storage_factory.py`
- New: `tests/test_qdrant_remote_adapter.py`
- Update: `tests/test_qdrant_adapter.py`

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Remote server unavailable khi MCP cần query | Factory raise `BackendConnectionError` với actionable message; MCP tool trả về error rõ ràng |
| Config typo `storage_backend: "remot"` silently falls back to local | Validate enum, raise trên unknown value |
| Per-project remote credentials leaked vào logs | RemoteConfig redacts credentials trong `__repr__` |
| FalkorDBDriver remote mode có behavior khác local | Test parity suite cho cả hai modes |
| Qdrant local exclusive lock conflict | Giữ nguyên StorageLease mechanism cho local; remote không cần lock |
| MCP tool latency tăng khi dùng remote | Document latency expectations; connection pooling cho remote |

## Success Criteria

- Fresh project config với `storage_backend: "local"` hoạt động identical hiện tại.
- Project config với `storage_backend: "remote"` + `remote.qdrant_url` kết nối
  thành công tới Qdrant server.
- Project config với `storage_backend: "remote"` + `remote.falkordb_uri` kết nối
  thành công tới FalkorDB server.
- MCP `semantic_search`, `explore_graph` tools trả về kết quả identical bất kể backend.
- Ingest scripts (`graphrag_ingest_langextract.py`, analyzer scripts) hoạt động
  trên cả hai backends.
- `make doctor` validate cả local và remote connectivity.
- Test suite cover factory routing, local parity, remote parity, config validation.

## Implementation Handoff

After review, implement with:

```text
/hi-craft plans/260817-storage-backend-adapter/plan.md
```
