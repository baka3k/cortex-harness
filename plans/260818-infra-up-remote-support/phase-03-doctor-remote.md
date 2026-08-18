# Phase 03 — Doctor Remote Checks

## Objective

Mở rộng `make doctor` để validate remote connectivity cho các project dùng
`storage_backend: "remote"`. Doctor scan project configs, probe từng remote
backend, và report kết quả cùng với local checks hiện tại.

## Scope

- `scripts/mcp-lifecycle.py`: `invoke_doctor()` extension
- Reuse `cortex_harness/storage/remote_probe.py` từ Phase 01

## Implementation

### 3.1 Remote Doctor Function

Thêm vào `scripts/mcp-lifecycle.py`:

```python
def doctor_remote_checks() -> int:
    """Check remote backend connectivity for all remote-mode projects.

    Returns the number of failed checks.
    """
    from cortex_harness.storage.config import (
        RemoteStorageConfig, validate_backend_config,
    )
    from cortex_harness.storage.remote_probe import probe_all

    projects = _scan_project_backends()
    remote_projects = [p for p in projects if p["backend_mode"] == "remote"]
    failures = 0

    if not remote_projects:
        doctor_check(
            "remote projects", True,
            "none configured", required=False,
        )
        return 0

    for project in remote_projects:
        project_id = project["project_id"]
        remote_section = project["remote_config"]

        try:
            _, remote_config = validate_backend_config("remote", remote_section)
        except ValueError as exc:
            failures += doctor_check(
                f"remote:{project_id}:config", False, str(exc),
            )
            continue

        results = probe_all(remote_config)
        for result in results:
            if result.backend == "qdrant" and not result.url.startswith("http"):
                # Not configured — skip silently
                continue
            if result.backend == "falkordb" and not result.url.startswith("redis"):
                continue
            failures += doctor_check(
                f"remote:{project_id}:{result.backend}",
                result.reachable,
                f"{result.url} — {result.message}",
            )

    return failures
```

### 3.2 Integrate into invoke_doctor()

Thêm call sau existing local checks (sau `doctor_process_checks`):

```python
def invoke_doctor() -> None:
    failures = 0
    # ... existing checks ...

    # Local storage checks
    try:
        resolved = _resolved_storage()
        # ... existing local path + round-trip checks ...
    except Exception as error:
        failures += doctor_check("local storage", False, str(error))

    doctor_process_checks(resolved)

    # ── Remote backend checks (new) ─────────────────────────────────
    try:
        failures += doctor_remote_checks()
    except Exception as error:
        failures += doctor_check("remote backends", False, str(error))

    # ... existing summary ...
```

### 3.3 Output Format

Doctor output cho remote checks follow existing convention:

```text
[doctor][ok]   remote:my_project:qdrant - http://qdrant.local:6333 — reachable
[doctor][ok]   remote:my_project:falkordb - redis://falkor.local:6379 — reachable
```

Hoặc khi fail:

```text
[doctor][fail] remote:my_project:qdrant - http://qdrant.local:6333 — Connection refused
```

Khi không có remote project nào:

```text
[doctor][ok]   remote projects - none configured
```

### 3.4 Force-Local Override Awareness

Khi env `CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1` được set, doctor report
rằng remote checks bị bypass:

```python
def doctor_remote_checks() -> int:
    if os.getenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL"):
        doctor_check(
            "remote backends", True,
            "bypassed (CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1)",
            required=False,
        )
        return 0
    # ... normal checks ...
```

## Acceptance Criteria

- [ ] `make doctor` report remote connectivity per remote project
- [ ] Mỗi remote project có 2 checks: qdrant + falkordb
- [ ] Skip unconfigured backends (no qdrant_url / no falkordb_uri)
- [ ] Respect `CORTEX_STORAGE_BACKEND_FORCE_LOCAL` env override
- [ ] Không có remote project → report "none configured" (optional, không fail)
- [ ] Output format consistent với existing doctor checks
