# Phase 02 — Qdrant Remote Adapter

## Objective

Create `RemoteQdrantStore` that wraps `QdrantClient(url=...)` with the same
interface as `LocalQdrantStore`, so callers use one API regardless of backend.

## Dependencies

- Phase 01 (BackendMode config).

## Changes

### 2.1 Refactor LocalQdrantStore Base

**File:** `cortex_harness/storage/qdrant.py`

Extract shared interface into `QdrantStoreBase` (or keep `LocalQdrantStore`
as-is and have `RemoteQdrantStore` mirror its API). Quyết định: **mirror API**
rather than abstract base class — simpler, avoids over-engineering for two
implementations.

`LocalQdrantStore` giữ nguyên không thay đổi.

### 2.2 RemoteQdrantStore

**File:** New `cortex_harness/storage/qdrant_remote.py`

```python
class RemoteQdrantStore:
    """Qdrant store backed by a remote server via URL.

    Mirrors LocalQdrantStore's public API. Connection is established lazily
    on first operation. No filesystem lease (server manages concurrency).
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._client = QdrantClient(
            url=url,
            api_key=api_key,
            timeout=timeout or 30.0,
        )

    # Mirror every public method from LocalQdrantStore:
    # list_collection_names, collection_exists, get_collection_info,
    # create_collection, recreate_collection, delete_collection,
    # upsert, upload_points, search, query_points, scroll, retrieve,
    # count, delete, set_payload, overwrite_payload, create_payload_index,
    # close
```

### 2.3 Client Caching

Remote clients được cache per `(url, api_key)` tuple để tránh credential leakage
giữa projects dùng cùng URL nhưng khác API key:

```python
_remote_client_lock = threading.Lock()
_remote_clients: dict[tuple[str, Optional[str]], QdrantClient] = {}

def get_remote_client(
    url: str, *, api_key: Optional[str] = None
) -> QdrantClient:
    cache_key = (url, api_key)
    with _remote_client_lock:
        existing = _remote_clients.get(cache_key)
        if existing is not None:
            return existing
        client = QdrantClient(url=url, api_key=api_key)
        _remote_clients[cache_key] = client
        return client


def reset_remote_clients() -> None:
    """Close all cached remote clients (mirrors local reset_clients)."""
    with _remote_client_lock:
        for client in _remote_clients.values():
            try:
                client.close()
            except Exception:
                pass
        _remote_clients.clear()


atexit.register(reset_remote_clients)
```

Không cần `StorageLease` cho remote — server tự quản lý concurrency.

### 2.4 Connection Health Check

```python
def check_connection(self) -> bool:
    """Verify the remote server is reachable."""
    try:
        self._client.get_collections()
        return True
    except Exception:
        return False
```

### 2.5 Error Mapping

Remote errors (connection refused, timeout, auth failure) cần được wrap thành
actionable messages:

```python
class BackendConnectionError(ConnectionError):
    """Remote storage backend is unreachable."""

    def __init__(self, backend: str, url: str, cause: Exception):
        super().__init__(
            f"{backend} server at {url} is unreachable: {cause}. "
            f"Check that the server is running and the URL is correct."
        )
        self.backend = backend
        self.url = url
        self.cause = cause
```

**File:** New `cortex_harness/storage/errors.py` (shared error types).

Inherits from `RuntimeError` (not `ConnectionError`) để tránh bị catch bởi
`OSError` handlers quá broad:

```python
class BackendConnectionError(RuntimeError):
    ...
```

## Key Differences from LocalQdrantStore

| Aspect | LocalQdrantStore | RemoteQdrantStore |
|--------|-----------------|-------------------|
| Constructor | `QdrantClient(path=...)` | `QdrantClient(url=..., api_key=...)` |
| Concurrency | `StorageLease` per directory | Server-managed |
| Cache key | Absolute filesystem path | URL string |
| `close()` | Releases lease + closes client | Closes client only |
| Health check | Path exists + readable | HTTP ping to server |

## Acceptance Criteria

- [ ] `RemoteQdrantStore` tồn tại với identical API to `LocalQdrantStore`.
- [ ] `get_remote_client()` cache per URL, không tạo duplicate connections.
- [ ] `BackendConnectionError` raised với actionable message khi server unreachable.
- [ ] `check_connection()` trả về `True`/`False` không raise.
- [ ] Credential redaction trong `__repr__`.
- [ ] Unit tests với mocked `QdrantClient` (không cần real server).
- [ ] Integration test với real Qdrant Docker server (optional, gated by env var).

## Files Modified

| File | Change |
|------|--------|
| New: `cortex_harness/storage/qdrant_remote.py` | `RemoteQdrantStore` implementation |
| New: `cortex_harness/storage/errors.py` | `BackendConnectionError` |
| `cortex_harness/storage/__init__.py` | Export `RemoteQdrantStore`, `BackendConnectionError` |
| New: `tests/test_qdrant_remote_adapter.py` | Unit + optional integration tests |
