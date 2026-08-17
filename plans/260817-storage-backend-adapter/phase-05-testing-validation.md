# Phase 05 — Testing & Validation

## Objective

Comprehensive test coverage for the adapter layer: config validation, factory
routing, local parity, remote parity, and end-to-end MCP tool behavior.

## Dependencies

- Phase 01–04 (all implementation phases).

## Test Categories

### 5.1 Config Validation Tests

**File:** New `tests/test_backend_config.py`

| Test | Description |
|------|-------------|
| `test_local_default` | Config without `storage_backend` resolves to `BackendMode.LOCAL` |
| `test_explicit_local` | Config with `"storage_backend": "local"` resolves correctly |
| `test_remote_valid` | Config with valid remote URLs resolves to `BackendMode.REMOTE` |
| `test_remote_missing_urls` | Config with `remote` but no URLs raises `ValueError` |
| `test_unknown_backend` | Config with `"storage_backend": "cloud"` raises `InvalidStorageIdentityError` |
| `test_remote_credential_redaction` | `RemoteStorageConfig.__repr__` masks API key and password |
| `test_mixed_remote` | Only `qdrant_url` set, `falkordb_uri` None — valid for Qdrant-only project |

### 5.2 Factory Tests

**File:** New `tests/test_storage_factory.py`

| Test | Description |
|------|-------------|
| `test_factory_local_qdrant` | `get_qdrant_store(LOCAL)` returns `LocalQdrantStore` |
| `test_factory_remote_qdrant` | `get_qdrant_store(REMOTE)` returns `RemoteQdrantStore` |
| `test_factory_local_falkordb` | `get_falkordb_driver(LOCAL)` returns path-based driver |
| `test_factory_remote_falkordb` | `get_falkordb_driver(REMOTE)` returns URI-based driver |
| `test_factory_mixed_backend` | Remote Qdrant + local FalkorDB resolves correctly |
| `test_factory_missing_remote_url` | Remote mode without URL raises actionable error |
| `test_factory_from_targets` | `from_targets()` correctly parses ProjectTargets |
| `test_create_storage_convenience` | `create_storage()` one-call works |

### 5.3 Remote Qdrant Adapter Tests

**File:** New `tests/test_qdrant_remote_adapter.py`

| Test | Description |
|------|-------------|
| `test_remote_store_creation` | `RemoteQdrantStore` constructs with URL |
| `test_remote_store_search` | `search()` delegates to `QdrantClient.query_points` |
| `test_remote_store_upsert` | `upsert()` normalizes dict → `PointStruct` |
| `test_remote_store_scroll` | `scroll()` returns paginated results |
| `test_remote_store_connection_error` | Unreachable server raises `BackendConnectionError` |
| `test_remote_client_caching` | Same URL returns cached client |
| `test_remote_client_different_urls` | Different URLs create different clients |

Uses `unittest.mock` to patch `QdrantClient` — no real server required.

### 5.4 Parity Tests

**File:** New `tests/test_backend_parity.py`

Verify that `LocalQdrantStore` and `RemoteQdrantStore` return equivalent
results for the same operations:

| Test | Description |
|------|-------------|
| `test_parity_search` | Both stores return same result shape from `search()` |
| `test_parity_scroll` | Both stores return same result shape from `scroll()` |
| `test_parity_upsert_delete` | Both stores handle upsert + delete identically |
| `test_parity_collection_lifecycle` | Both stores create/list/delete collections |

Uses mocked clients to verify interface conformance.

### 5.5 Integration Tests (Optional)

Gated by environment variable `CORTEX_TEST_REMOTE=1`:

| Test | Description |
|------|-------------|
| `test_integration_remote_qdrant` | Connect to real Qdrant Docker server |
| `test_integration_remote_falkordb` | Connect to real FalkorDB Docker server |
| `test_integration_mcp_remote` | MCP tool resolves remote backend and returns results |

Requires Docker Compose setup (not included in default test run).

### 5.6 Existing Test Preservation

All existing tests must pass unchanged:

```bash
pytest tests/test_qdrant_adapter.py          # LocalQdrantStore tests
pytest tests/test_falkordb_driver_local.py   # Local FalkorDB tests
pytest tests/test_qdrant_local_smoke.py      # Local smoke tests
pytest tests/test_qdrant_project_scope.py    # Project scope tests
pytest tests/test_qdrant_collection_scope.py # Collection scope tests
```

## make doctor Extension

**File:** `cortex_harness/dev.py` or `Makefile`

Thêm remote connectivity check:

```python
def doctor_remote():
    """Validate remote backend connectivity for configured projects."""
    config_dir = _default_config_dir()
    for entry in _project_entries(_read_config_files(config_dir)):
        if entry.get("storage_backend") != "remote":
            continue
        remote = entry.get("remote_config", {})
        project_id = entry["project_id"]
        if remote.get("qdrant_url"):
            ok = _ping_qdrant(remote["qdrant_url"], remote.get("qdrant_api_key"))
            status = "✓" if ok else "✗"
            print(f"  {status} {project_id} Qdrant @ {remote['qdrant_url']}")
        if remote.get("falkordb_uri"):
            ok = _ping_falkordb(remote["falkordb_uri"])
            status = "✓" if ok else "✗"
            print(f"  {status} {project_id} FalkorDB @ {remote['falkordb_uri']}")
```

## Documentation Updates

| File | Change |
|------|--------|
| `ReadMe.md` | Add "Storage Backend Configuration" section |
| `docs/DATABASE_INTEGRATION.md` | Document local vs remote modes |
| `.cortext-harness/config/README.md` | Config schema reference with examples |

## Acceptance Criteria

- [ ] `tests/test_backend_config.py` — 7+ tests, all pass.
- [ ] `tests/test_storage_factory.py` — 8+ tests, all pass.
- [ ] `tests/test_qdrant_remote_adapter.py` — 7+ tests, all pass.
- [ ] `tests/test_backend_parity.py` — 4+ tests, all pass.
- [ ] All existing tests pass unchanged.
- [ ] `make doctor` validates remote connectivity for configured projects.
- [ ] Documentation updated with backend configuration guide.
- [ ] Integration tests (gated) pass with Docker backends.

## Test Command

```bash
# Default (no remote server needed)
pytest tests/test_backend_config.py tests/test_storage_factory.py \
       tests/test_qdrant_remote_adapter.py tests/test_backend_parity.py

# With Docker backends
CORTEX_TEST_REMOTE=1 pytest tests/test_backend_parity.py -m integration
```

## Files Modified

| File | Change |
|------|--------|
| New: `tests/test_backend_config.py` | Config validation tests |
| New: `tests/test_storage_factory.py` | Factory routing tests |
| New: `tests/test_qdrant_remote_adapter.py` | Remote adapter unit tests |
| New: `tests/test_backend_parity.py` | Local/remote parity tests |
| `cortex_harness/dev.py` | Add `doctor_remote()` check |
| `Makefile` | Add `doctor-remote` target |
| `ReadMe.md` | Backend configuration documentation |
