# Phase 04 — Testing & Validation

## Objective

Đảm bảo mọi thay đổi trong Phase 01–03 được cover bởi tests. Tests validate
cả local routing (backward compat) lẫn remote probe/provision logic.

## Scope

- `tests/test_infra_remote.py` (new)
- `tests/test_make_lifecycle.py` (update)
- `tests/test_storage_lifecycle.py` (update)
- `tests/test_doctor_remote.py` (new)

## Test Cases

### 4.1 Remote Probe Tests (`tests/test_infra_remote.py`)

```python
class TestProbeQdrant:
    """probe_qdrant returns correct ProbeResult."""

    def test_no_url_returns_skipped(self):
        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        result = probe_qdrant(config)
        assert result.reachable is True
        assert "skipped" in result.message

    def test_reachable_server(self, mock_qdrant_server):
        config = RemoteStorageConfig(qdrant_url=mock_qdrant_server.url)
        result = probe_qdrant(config)
        assert result.reachable is True
        assert result.message == "reachable"

    def test_unreachable_server(self):
        config = RemoteStorageConfig(qdrant_url="http://localhost:99999")
        result = probe_qdrant(config)
        assert result.reachable is False
        assert result.cause is not None


class TestProbeFalkordb:
    """probe_falkordb returns correct ProbeResult."""

    def test_no_uri_returns_skipped(self):
        config = RemoteStorageConfig(qdrant_url="http://localhost:6333")
        result = probe_falkordb(config)
        assert result.reachable is True
        assert "skipped" in result.message

    def test_reachable_server(self, mock_falkordb_server):
        config = RemoteStorageConfig(falkordb_uri=mock_falkordb_server.uri)
        result = probe_falkordb(config)
        assert result.reachable is True

    def test_unreachable_server(self):
        config = RemoteStorageConfig(falkordb_uri="redis://localhost:99999")
        result = probe_falkordb(config)
        assert result.reachable is False


class TestProbeAll:
    def test_returns_both_results(self):
        config = RemoteStorageConfig(
            qdrant_url="http://localhost:6333",
            falkordb_uri="redis://localhost:6379",
        )
        results = probe_all(config)
        assert len(results) == 2
        backends = {r.backend for r in results}
        assert backends == {"qdrant", "falkordb"}
```

### 4.2 Provisioning Tests

```python
class TestProvisionQdrant:
    def test_creates_collection_when_missing(self, mock_qdrant_server):
        config = RemoteStorageConfig(qdrant_url=mock_qdrant_server.url)
        result = provision_qdrant_collection(config, "test_collection")
        assert result.action == "created"

    def test_skips_existing_collection(self, mock_qdrant_server):
        config = RemoteStorageConfig(qdrant_url=mock_qdrant_server.url)
        # Create first time
        provision_qdrant_collection(config, "test_collection")
        # Second time should be "exists"
        result = provision_qdrant_collection(config, "test_collection")
        assert result.action == "exists"

    def test_skips_when_no_url(self):
        config = RemoteStorageConfig(falkordb_uri="redis://localhost:6379")
        result = provision_qdrant_collection(config, "test")
        assert result.action == "skipped"


class TestProvisionFalkordb:
    def test_accessible_graph(self, mock_falkordb_server):
        config = RemoteStorageConfig(falkordb_uri=mock_falkordb_server.uri)
        result = provision_falkordb_graph(config, "test_graph")
        assert result.action == "exists"
```

### 4.3 Lifecycle Tests Update (`tests/test_make_lifecycle.py`)

```python
class TestInfraUpRemote:
    def test_infra_up_no_longer_deprecated(self):
        """infra-up should NOT print deprecation warning."""
        # Remove old deprecation assertion
        result = invoke_infra_up()
        assert "deprecated" not in (result or "")

    def test_infra_up_routes_local_projects(self, tmp_path, monkeypatch):
        """Projects without storage_backend use local init."""
        # Setup: config with no storage_backend field
        # Verify: storage-init behavior (ensure_layout called)

    def test_infra_up_probes_remote_projects(self, tmp_path, monkeypatch):
        """Projects with storage_backend=remote get probed."""
        # Setup: config with storage_backend=remote, qdrant_url, falkordb_uri
        # Verify: probe results printed

    def test_infra_up_exits_nonzero_on_failure(self, tmp_path, monkeypatch):
        """infra-up exits 1 when remote probe fails."""
        # Setup: config pointing at unreachable server
        # Verify: SystemExit(1)
```

### 4.4 Storage Lifecycle Tests Update (`tests/test_storage_lifecycle.py`)

```python
def test_infra_up_is_no_longer_deprecated(tmp_path: Path) -> None:
    """infra-up is now a first-class command, not a deprecated alias."""
    result = _run_lifecycle("infra-up", cwd=tmp_path, data_home=tmp_path / "data")
    assert "deprecated" not in result.stdout
    # Local behavior preserved
    assert "storage" in result.stdout or "infra-up" in result.stdout


def test_infra_down_closes_remote_clients(tmp_path: Path) -> None:
    """infra-down closes remote client connections."""
    result = _run_lifecycle("infra-down", cwd=tmp_path, data_home=tmp_path / "data")
    assert "remote" in result.stdout.lower() or "closed" in result.stdout.lower()
```

### 4.5 Doctor Remote Tests (`tests/test_doctor_remote.py`)

```python
class TestDoctorRemoteChecks:
    def test_no_remote_projects(self, tmp_path, capsys):
        """No remote projects → optional check passes."""
        # Setup: empty config dir or only local projects
        failures = doctor_remote_checks()
        assert failures == 0

    def test_remote_project_reachable(self, tmp_path, mock_servers):
        """Reachable remote servers → all checks pass."""
        # Setup: config with reachable remote URLs
        failures = doctor_remote_checks()
        assert failures == 0

    def test_remote_project_unreachable(self, tmp_path):
        """Unreachable remote servers → checks fail."""
        # Setup: config with unreachable URLs
        failures = doctor_remote_checks()
        assert failures > 0

    def test_force_local_bypass(self, tmp_path, monkeypatch):
        """CORTEX_STORAGE_BACKEND_FORCE_LOCAL skips remote checks."""
        monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")
        failures = doctor_remote_checks()
        assert failures == 0
```

### 4.6 Project Config Scanner Tests

```python
class TestScanProjectBackends:
    def test_empty_config_dir(self, tmp_path):
        assert _scan_project_backends() == []

    def test_local_project(self, tmp_path):
        # Create config without storage_backend field
        config = {"project": {"code": "my_proj", "name": "Test"}}
        # Write to .cortext-harness/config/my_proj.json
        result = _scan_project_backends()
        assert len(result) == 1
        assert result[0]["backend_mode"] == "local"

    def test_remote_project(self, tmp_path):
        config = {
            "project": {"code": "my_proj"},
            "storage_backend": "remote",
            "remote": {
                "qdrant_url": "http://qdrant.local:6333",
                "falkordb_uri": "redis://falkor.local:6379",
            },
        }
        result = _scan_project_backends()
        assert result[0]["backend_mode"] == "remote"
        assert result[0]["remote_config"] is not None
```

## Test Infrastructure

### Mock Servers

Tests cần mock Qdrant và FalkorDB servers. Options:

1. **pytest fixtures với `httpx_mock`** cho Qdrant HTTP API
2. **`fakeredis`** cho FalkorDB RESP protocol
3. **Integration tests** với actual Docker containers (CI-only)

Recommendation: dùng fixtures cho unit tests, integration tests riêng.

```python
@pytest.fixture
def mock_qdrant_server():
    """Start a mock Qdrant HTTP server for testing."""
    # Use respx or httpx_mock to simulate Qdrant REST API
    ...

@pytest.fixture
def mock_falkordb_server():
    """Start a mock FalkorDB RESP server for testing."""
    # Use fakeredis for RESP protocol simulation
    ...
```

## Validation Checklist

- [ ] All new modules have unit tests
- [ ] Existing `test_infra_up_delegates_to_storage_init` updated
- [ ] Existing `test_infra_up_is_a_no_docker_deprecation_alias` removed/updated
- [ ] Remote probe tests cover: reachable, unreachable, not-configured
- [ ] Provision tests cover: create, exists, skip, fail
- [ ] Doctor remote tests cover: no projects, reachable, unreachable, force-local
- [ ] Scanner tests cover: empty dir, local, remote, malformed JSON
- [ ] `make infra-up` tested end-to-end with both local and remote configs
- [ ] CI passes: `pytest tests/test_infra_remote.py tests/test_doctor_remote.py`
