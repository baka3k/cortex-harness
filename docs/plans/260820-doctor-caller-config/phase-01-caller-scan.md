---
title: "Phase 01 — Caller-Aware Scan in _scan_project_backends()"
plan: 260820-doctor-caller-config
phase: 1
status: pending
---

# Phase 01 — Caller-Aware Scan

## Objective

Make `_scan_project_backends()` also scan the caller's project config directory
(`Path.cwd() / ".cortext-harness" / "config"`) in addition to the default ROOT
config directory, so `doctor_remote_checks()` and `invoke_infra_up()` pick up
the project the user is working in.

## Changes

### `scripts/mcp-lifecycle.py` — `_scan_project_backends()`

**Current code (line ~309):**

```python
def _scan_project_backends(config_dir: Path | None = None) -> list[dict[str, object]]:
    base = config_dir if config_dir is not None else ROOT / ".cortext-harness" / "config"
    if not base.is_dir():
        return []
    projects: list[dict[str, object]] = []
    for config_path in sorted(base.glob("*.json")):
        # ... parse and append
    return projects
```

**New code:**

```python
def _scan_project_backends(config_dir: Path | None = None) -> list[dict[str, object]]:
    base = config_dir if config_dir is not None else ROOT / ".cortext-harness" / "config"
    projects: list[dict[str, object]] = []
    _collect_from_dir(base, projects)

    # Also scan the caller's project config when it differs from the
    # primary directory.  ``dev doctor`` and ``make infra-up`` set
    # ``cwd`` to the caller's project root, so Path.cwd() resolves to
    # the user's working tree rather than the cortex-harness repo.
    if config_dir is None:
        try:
            caller_config = Path.cwd() / ".cortext-harness" / "config"
            caller_resolved = caller_config.resolve()
            base_resolved = base.resolve()
            if caller_resolved != base_resolved:
                _collect_from_dir(caller_config, projects)
        except (OSError, RuntimeError):
            # Path.cwd() raises FileNotFoundError when the working
            # directory was deleted; resolve() can raise OSError on
            # broken symlinks.  Neither should block the primary scan.
            pass

    return projects


def _collect_from_dir(
    config_dir: Path,
    out: list[dict[str, object]],
) -> None:
    """Parse every ``*.json`` in *config_dir* and append to *out*."""
    if not config_dir.is_dir():
        return
    for config_path in sorted(config_dir.glob("*.json")):
        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(document, dict):
            continue
        project_section = document.get("project", {})
        project_id = (
            str(project_section.get("code") or "").strip()
            or config_path.stem
        )
        backend = str(document.get("storage_backend") or "local")
        remote_section = document.get("remote")
        out.append({
            "project_id": project_id,
            "backend_mode": backend,
            "remote_config": remote_section,
            "config_path": str(config_path),
        })
```

**Rationale:**
- Extract inner loop into `_collect_from_dir()` to avoid duplicating the
  parse logic for the second scan.
- Only scan caller when `config_dir is None` — explicit callers (tests,
  future code) that pass a specific directory get deterministic behavior.
- Compare resolved paths to skip when cwd is inside the cortex-harness repo
  (e.g., user runs `make doctor` from the repo root).

## Acceptance Criteria

- `_scan_project_backends()` returns configs from both ROOT and caller
  directories when they differ.
- No change when cwd == ROOT.
- Malformed JSON in caller directory silently skipped (same as ROOT).
- Existing tests pass unchanged.
