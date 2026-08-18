# Phase 01 — Remote-aware infra-up Command

## Objective

Un-deprecate `infra-up` và biến nó thành smart lifecycle command phân biệt
local vs remote projects. Local projects giữ nguyên behavior hiện tại
(delegate `storage-init`), remote projects chạy connectivity probe.

## Scope

- `scripts/mcp-lifecycle.py`: `invoke_infra_up()`, `invoke_infra_down()`
- `cortex_harness/dev.py`: CLI flags
- `cortex_harness/storage/remote_probe.py` (new): shared probe utilities

## Implementation

### 1.1 Remote Probe Module

Tạo `cortex_harness/storage/remote_probe.py` — shared module cho cả
lifecycle scripts và doctor:

```python
"""Connectivity probe for remote storage backends.

Shared by ``infra-up``, ``infra-down``, and ``doctor`` to validate
remote Qdrant and FalkorDB server reachability without duplicating
connection logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import BackendMode, RemoteStorageConfig
from .errors import BackendConnectionError


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single remote backend probe."""
    backend: str          # "qdrant" or "falkordb"
    url: str
    reachable: bool
    message: str
    cause: Optional[BaseException] = None


def probe_qdrant(config: RemoteStorageConfig) -> ProbeResult:
    """Check Qdrant server reachability from a RemoteStorageConfig."""
    if not config.qdrant_url:
        return ProbeResult("qdrant", "(not configured)", True, "skipped — no qdrant_url")
    try:
        from .qdrant_remote import get_remote_client
        client = get_remote_client(config.qdrant_url, api_key=config.qdrant_api_key)
        client.get_collections()
        return ProbeResult("qdrant", config.qdrant_url, True, "reachable")
    except Exception as exc:
        return ProbeResult("qdrant", config.qdrant_url, False, str(exc), cause=exc)


def probe_falkordb(config: RemoteStorageConfig) -> ProbeResult:
    """Check FalkorDB server reachability from a RemoteStorageConfig."""
    if not config.falkordb_uri:
        return ProbeResult("falkordb", "(not configured)", True, "skipped — no falkordb_uri")
    try:
        from tools.graph.driver.falkordb_driver import FalkorDBDriver
        driver = FalkorDBDriver(
            uri=config.falkordb_uri,
            password=config.falkordb_password,
            ssl=config.falkordb_ssl,
            graph="__probe__",
            _suppress_deprecation=True,
        )
        driver.execute_query("RETURN 1 AS ok")
        return ProbeResult("falkordb", config.falkordb_uri, True, "reachable")
    except Exception as exc:
        return ProbeResult("falkordb", config.falkordb_uri, False, str(exc), cause=exc)


def probe_all(config: RemoteStorageConfig) -> list[ProbeResult]:
    """Probe both backends and return combined results."""
    return [probe_qdrant(config), probe_falkordb(config)]
```

### 1.2 Project Config Scanner

Thêm helper function vào `mcp-lifecycle.py` để scan tất cả project configs
và phân loại local vs remote:

```python
def _scan_project_backends() -> list[dict[str, object]]:
    """Scan .cortext-harness/config/*.json and classify by backend mode.

    Returns list of dicts with keys: project_id, backend_mode, remote_config,
    config_path.
    """
    config_dir = ROOT / ".cortext-harness" / "config"
    if not config_dir.is_dir():
        return []
    projects: list[dict[str, object]] = []
    for config_path in sorted(config_dir.glob("*.json")):
        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        project_section = document.get("project", {})
        project_id = project_section.get("code") or config_path.stem
        backend = str(document.get("storage_backend") or "local")
        remote_section = document.get("remote")
        projects.append({
            "project_id": project_id,
            "backend_mode": backend,
            "remote_config": remote_section,
            "config_path": str(config_path),
        })
    return projects
```

### 1.3 Rewrite invoke_infra_up()

```python
def invoke_infra_up(*, provision: bool = False) -> None:
    """Initialize storage for all registered projects.

    Local projects get their instance tree created (same as storage-init).
    Remote projects get connectivity validated; with ``provision=True``
    the required collections and graphs are created on the remote servers.
    """
    from cortex_harness.storage.layout import ensure_layout

    # Always ensure local layout exists (backward compatible).
    resolved = _resolved_storage()
    ensure_layout(resolved)

    projects = _scan_project_backends()
    remote_projects = [p for p in projects if p["backend_mode"] == "remote"]
    local_count = len(projects) - len(remote_projects)

    if local_count:
        print(f"[infra-up] {local_count} local project(s) — storage initialized")

    if not remote_projects:
        print(f"[infra-up] data root: {resolved.data_root}")
        print(f"[infra-up] manifest : {resolved.manifest_path}")
        return

    from cortex_harness.storage.config import (
        RemoteStorageConfig, validate_backend_config,
    )
    from cortex_harness.storage.remote_probe import probe_all

    failures = 0
    for project in remote_projects:
        project_id = project["project_id"]
        remote_section = project["remote_config"]
        try:
            _, remote_config = validate_backend_config("remote", remote_section)
        except ValueError as exc:
            print(f"[infra-up] [fail] {project_id}: {exc}")
            failures += 1
            continue

        print(f"[infra-up] {project_id} (remote):")
        results = probe_all(remote_config)
        for result in results:
            tag = "[ok]" if result.reachable else "[fail]"
            print(f"[infra-up]   {tag} {result.backend}: {result.message}")
            if not result.reachable:
                failures += 1

        if provision and all(r.reachable for r in results):
            _provision_remote_project(project_id, remote_config)

    if failures:
        print(f"[infra-up] {failures} check(s) failed")
        raise SystemExit(1)
    print(f"[infra-up] all remote projects reachable")
```

### 1.4 Rewrite invoke_infra_down()

```python
def invoke_infra_down() -> None:
    """Tear down storage lifecycle.

    Local projects: no-op (files persist on disk).
    Remote projects: close cached remote clients.
    """
    from cortex_harness.storage.qdrant_remote import reset_remote_clients

    reset_remote_clients()
    print("[infra-down] remote client connections closed")
```

### 1.5 Dev CLI Update

Trong `cortex_harness/dev.py`:

```python
@cli.command("infra-up")
@click.option("--provision", is_flag=True, default=False,
              help="Create collections/graphs on remote servers.")
def infra_up(provision: bool):
    """Initialize local and remote storage for all projects."""
    _run_lifecycle("infra-up", PROVISION=provision)
```

### 1.6 Makefile Update

Không cần thay đổi Makefile — `make infra-up` đã delegate qua lifecycle
script. Tuy nhiên cần pass args:

```makefile
infra-up:
	$(LIFECYCLE) infra-up $(INFRA_ARGS)
```

Và `dev.py` set `INFRA_ARGS` qua env khi `--provision` được truyền.

## Acceptance Criteria

- [ ] `cortex_harness/storage/remote_probe.py` exists với `probe_qdrant`,
      `probe_falkordb`, `probe_all`
- [ ] `invoke_infra_up()` scan project configs và route local/remote
- [ ] `invoke_infra_down()` close remote clients
- [ ] `dev infra-up --provision` flag accepted
- [ ] Backward compatible: projects without `storage_backend` field work as local
- [ ] Existing tests updated (remove deprecation assertions)
