# Phase 03 — Storage Factory

## Objective

Create `StorageFactory` — single entry point that resolves the correct Qdrant
and FalkorDB backend instances based on `ProjectTargets` configuration.

## Dependencies

- Phase 01 (BackendMode config).
- Phase 02 (RemoteQdrantStore).

## Changes

### 3.1 StorageFactory

**File:** New `cortex_harness/storage/factory.py`

```python
from __future__ import annotations

from typing import Optional, Union
from .config import BackendMode, ResolvedStorage, RemoteStorageConfig
from .qdrant import LocalQdrantStore
from .qdrant_remote import RemoteQdrantStore

QdrantStore = Union[LocalQdrantStore, RemoteQdrantStore]


class StorageFactory:
    """Resolve the correct backend instances for a project.

    Usage:
        factory = StorageFactory.from_targets(targets, resolved)
        qdrant = factory.get_qdrant_store()
        graph_driver = factory.get_falkordb_driver(graph_name)
    """

    def __init__(
        self,
        *,
        backend_mode: BackendMode,
        resolved: ResolvedStorage,
        remote: Optional[RemoteStorageConfig] = None,
    ) -> None:
        self._mode = backend_mode
        self._resolved = resolved
        self._remote = remote

    @classmethod
    def from_targets(
        cls,
        targets: "ProjectTargets",
        resolved: ResolvedStorage,
    ) -> "StorageFactory":
        mode = BackendMode(targets.storage_backend)
        remote = None
        if mode == BackendMode.REMOTE and targets.remote_config:
            from .config import validate_backend_config
            _, remote = validate_backend_config(
                targets.storage_backend, targets.remote_config
            )
        return cls(backend_mode=mode, resolved=resolved, remote=remote)

    def get_qdrant_store(
        self, role: "QdrantStorageRole"
    ) -> QdrantStore:
        if self._mode == BackendMode.REMOTE:
            if not self._remote or not self._remote.qdrant_url:
                raise ValueError(
                    "Remote Qdrant requested but qdrant_url not configured"
                )
            return RemoteQdrantStore(
                url=self._remote.qdrant_url,
                api_key=self._remote.qdrant_api_key,
            )
        return LocalQdrantStore(self._resolved, role)

    def get_falkordb_driver(
        self, graph_name: str
    ) -> "FalkorDBDriver":
        if self._mode == BackendMode.REMOTE:
            if not self._remote or not self._remote.falkordb_uri:
                raise ValueError(
                    "Remote FalkorDB requested but falkordb_uri not configured"
                )
            from tools.graph.driver.falkordb_driver import FalkorDBDriver
            return FalkorDBDriver(
                uri=self._remote.falkordb_uri,
                password=self._remote.falkordb_password,
                ssl=self._remote.falkordb_ssl,
                graph=graph_name,
            )
        from tools.graph.driver.falkordb_driver import FalkorDBDriver
        path = str(self._resolved.falkordb_path)
        return FalkorDBDriver(path=path, graph=graph_name)
```

### 3.2 Convenience Function

```python
def create_storage(
    targets: "ProjectTargets",
    *,
    project_root: Path = Path.cwd(),
    resolved: Optional[ResolvedStorage] = None,
) -> StorageFactory:
    """One-call factory for the common case.

    ``project_root`` is required because ``resolve_storage()`` needs it
    to locate the project config and resolve local paths.
    """
    if resolved is None:
        from .config import resolve_storage
        resolved = resolve_storage(project_root)
    return StorageFactory.from_targets(targets, resolved)
```

### 3.3 FalkorDB Factory Classmethod

**File:** `code-tiny/tools/graph/driver/falkordb_driver.py`

Thêm classmethod:

```python
@classmethod
def from_storage_factory(
    cls, factory: "StorageFactory", graph_name: str
) -> "FalkorDBDriver":
    """Create a driver from a StorageFactory."""
    return factory.get_falkordb_driver(graph_name)
```

### 3.4 Export from __init__

**File:** `cortex_harness/storage/__init__.py`

```python
from .factory import StorageFactory, create_storage, QdrantStore
```

## Usage Pattern

### Before (current code)

```python
# In MCP tool or ingest script:
from cortex_harness.storage import LocalQdrantStore, resolve_storage

resolved = resolve_storage()
store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
results = store.search(collection_name, query_vector, limit=10)
```

### After (with factory)

```python
# In MCP tool or ingest script:
from cortex_harness.storage import create_storage
from tools.common.project_registry import ProjectRegistry

targets = ProjectRegistry.resolve_project_targets(project_id)
factory = create_storage(targets)
store = factory.get_qdrant_store(QdrantStorageRole.CODE)
results = store.search(collection_name, query_vector, limit=10)
```

Same API call — only the factory resolution step is new.

## Mixed Backend Handling

Một project có thể chỉ dùng remote Qdrant nhưng local FalkorDB (hoặc ngược lại).
Factory falls back to local cho component không có remote URI:

```python
def get_falkordb_driver(self, graph_name: str) -> FalkorDBDriver:
    if (self._mode == BackendMode.REMOTE
            and self._remote
            and self._remote.falkordb_uri):
        return FalkorDBDriver(
            uri=self._remote.falkordb_uri,
            password=self._remote.falkordb_password,
            ssl=self._remote.falkordb_ssl,
            graph=graph_name,
            _suppress_deprecation=True,
        )
    # Fall back to local (default path or explicit fallback)
    path = str(self._resolved.falkordb_path)
    return FalkorDBDriver(
        path=path, graph=graph_name,
        owner_id=self._resolved.code_owner_id,
        instance_id=self._resolved.instance_id,
    )
```

- `remote.qdrant_url` set nhưng `remote.falkordb_uri` is None →
  `get_qdrant_store()` returns remote, `get_falkordb_driver()` returns local.
- Validation ở Phase 01 chỉ yêu cầu **at least one** remote URL, không bắt cả hai.
- Local FalkorDB driver nhận `owner_id` và `instance_id` từ `ResolvedStorage`
  để đảm bảo lease identity chính xác.

## Acceptance Criteria

- [ ] `StorageFactory.from_targets()` tạo đúng backend từ config.
- [ ] `get_qdrant_store(LOCAL)` returns `LocalQdrantStore`.
- [ ] `get_qdrant_store(REMOTE)` returns `RemoteQdrantStore`.
- [ ] `get_falkordb_driver(LOCAL)` returns local-path `FalkorDBDriver`.
- [ ] `get_falkordb_driver(REMOTE)` returns URI-based `FalkorDBDriver`.
- [ ] Mixed backend (remote Qdrant + local FalkorDB) hoạt động.
- [ ] `create_storage()` convenience function hoạt động.
- [ ] Unit tests cover all factory paths.

## Files Modified

| File | Change |
|------|--------|
| New: `cortex_harness/storage/factory.py` | `StorageFactory`, `create_storage` |
| `cortex_harness/storage/__init__.py` | Export factory |
| `code-tiny/tools/graph/driver/falkordb_driver.py` | Add `from_storage_factory` classmethod |
| New: `tests/test_storage_factory.py` | Factory unit tests |
