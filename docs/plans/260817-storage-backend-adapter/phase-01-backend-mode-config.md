# Phase 01 — Backend Mode Config Schema

## Objective

Extend the project config schema and storage resolution layer to support
per-project `storage_backend` selection (`local` | `remote`) and optional
remote connection parameters.

## Dependencies

- None (foundation phase).

## Changes

### 1.1 BackendMode Enum

**File:** `cortex_harness/storage/config.py`

```python
class BackendMode(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
```

Thêm vào module cùng vị trí với `StorageRole`, `QdrantStorageRole`.

### 1.2 Remote Storage Config Dataclass

**File:** `cortex_harness/storage/config.py`

```python
@dataclass(frozen=True)
class RemoteStorageConfig:
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    falkordb_uri: Optional[str] = None
    falkordb_password: Optional[str] = None
    falkordb_ssl: bool = False

    def __repr__(self) -> str:
        """Redact credentials in logs."""
        return (
            f"RemoteStorageConfig(qdrant_url={self.qdrant_url!r}, "
            f"qdrant_api_key=***, falkordb_uri={self.falkordb_uri!r}, "
            f"falkordb_password=***, falkordb_ssl={self.falkordb_ssl})"
        )
```

### 1.3 ResolvedStorage Extension

**File:** `cortex_harness/storage/config.py`

Thêm fields vào `ResolvedStorage`:

```python
@dataclass(frozen=True)
class ResolvedStorage:
    # ... existing fields ...
    backend_mode: BackendMode = BackendMode.LOCAL
    remote: Optional[RemoteStorageConfig] = None
```

### 1.4 ProjectRegistry Extension

**File:** `code-tiny/tools/common/project_registry.py`

`ProjectTargets` thêm fields:

```python
@dataclass(frozen=True)
class ProjectTargets:
    # ... existing fields ...
    storage_backend: str = "local"
    remote_config: Optional[Dict[str, Any]] = None
```

`_project_entries()` parse thêm từ config JSON:

```python
entry["storage_backend"] = document.get("storage_backend", "local")
entry["remote_config"] = document.get("remote")
```

### 1.5 Config Validation

**File:** `cortex_harness/storage/config.py`

```python
def validate_backend_config(
    backend: str, remote: Optional[Mapping[str, Any]]
) -> tuple[BackendMode, Optional[RemoteStorageConfig]]:
    """Validate backend mode and remote config completeness."""
    try:
        mode = BackendMode(backend)
    except ValueError:
        raise InvalidStorageIdentityError(
            f"storage_backend must be 'local' or 'remote'; got {backend!r}"
        )
    if mode == BackendMode.LOCAL:
        return mode, None
    if remote is None:
        raise ValueError(
            "storage_backend='remote' requires a 'remote' section "
            "with at least qdrant_url or falkordb_uri"
        )
    config = RemoteStorageConfig(
        qdrant_url=remote.get("qdrant_url"),
        qdrant_api_key=remote.get("qdrant_api_key"),
        falkordb_uri=remote.get("falkordb_uri"),
        falkordb_password=remote.get("falkordb_password"),
        falkordb_ssl=bool(remote.get("falkordb_ssl", False)),
    )
    if not config.qdrant_url and not config.falkordb_uri:
        raise ValueError(
            "remote config must specify at least qdrant_url or falkordb_uri"
        )
    return mode, config
```

### 1.6 LEGACY_REMOTE_KEYS Behavior Change

**File:** `cortex_harness/storage/config.py`

Hiện tại `LEGACY_REMOTE_KEYS` presence trong config raises error.
Thay đổi: khi `backend_mode == REMOTE`, các keys này được accepted (nhưng
vẫn emit deprecation warning nếu dùng thay vì `remote` section).

## Config Schema Example

### Local (default — backward compatible)

```json
{
  "project": {"code": "my_project", "name": "My Project"},
  "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
  "doc": {"env": {}}
}
```

### Remote

```json
{
  "project": {"code": "shared_project", "name": "Shared Project"},
  "storage_backend": "remote",
  "remote": {
    "qdrant_url": "http://qdrant.internal:6333",
    "falkordb_uri": "redis://falkordb.internal:6379"
  },
  "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
  "doc": {"env": {}}
}
```

### Mixed (some projects local, some remote)

Mỗi `.cortext-harness/config/*.json` file tự khai báo `storage_backend`.
Không cần global coordination.

## Acceptance Criteria

- [ ] `BackendMode` enum tồn tại trong `cortex_harness/storage/config.py`.
- [ ] `RemoteStorageConfig` dataclass tồn tại, `__repr__` redacts credentials.
- [ ] `ResolvedStorage` có `backend_mode` và `remote` fields.
- [ ] `ProjectTargets` có `storage_backend` và `remote_config` fields.
- [ ] `_project_entries()` parse `storage_backend` từ config JSON.
- [ ] `validate_backend_config()` reject unknown mode và incomplete remote config.
- [ ] Config không có `storage_backend` default về `"local"` (backward compatible).
- [ ] Test fixtures cover local-default, explicit-local, remote-valid, remote-invalid.
- [ ] Existing tests pass không thay đổi.

## Files Modified

| File | Change |
|------|--------|
| `cortex_harness/storage/config.py` | Add `BackendMode`, `RemoteStorageConfig`, extend `ResolvedStorage`, add `validate_backend_config()` |
| `cortex_harness/storage/__init__.py` | Export new types |
| `code-tiny/tools/common/project_registry.py` | Parse `storage_backend` + `remote` from config |
| `tests/fixtures/unified_contract/config/` | Add remote backend test fixture |
| New: `tests/test_backend_config.py` | Validation tests |
