---
title: "Phase 02 — Testing & Validation"
plan: 260820-doctor-caller-config
phase: 2
status: pending
---

# Phase 02 — Testing & Validation

## Objective

Add tests covering the caller-aware scan behavior and verify the full
doctor flow picks up remote configs from the caller's project directory.

## New Tests

### `tests/test_doctor_remote.py` — Caller Config Tests

```python
class TestDoctorCallerConfig:
    def test_caller_remote_config_is_picked_up(self, tmp_path, monkeypatch):
        """Doctor finds remote project config from cwd even when ROOT has none."""
        # Setup: caller dir has remote config, ROOT has no config
        caller_project = tmp_path / "my-project"
        caller_config = caller_project / ".cortext-harness" / "config"
        caller_config.mkdir(parents=True)
        _write_config(caller_config, "my_app", {
            "project": {"code": "my_app"},
            "storage_backend": "remote",
            "remote": {
                "qdrant_url": "http://localhost:6333",
                "falkordb_uri": "redis://localhost:6379",
            },
        })

        # Simulate: cwd = caller_project, ROOT = cortex-harness (no configs)
        monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path / "cortex-harness")
        monkeypatch.chdir(caller_project)

        probe_results = [
            ProbeResult("qdrant", "http://localhost:6333", True, "reachable"),
            ProbeResult("falkordb", "redis://localhost:6379", True, "reachable"),
        ]
        from cortex_harness.storage import remote_probe as rp
        with mock.patch.object(rp, "probe_all", return_value=probe_results), \
             mock.patch.object(LIFECYCLE, "doctor_check", return_value=0) as check:
            failures = LIFECYCLE.doctor_remote_checks()

        assert failures == 0
        names = [c.args[0] for c in check.call_args_list]
        assert "remote:my_app:qdrant" in names
        assert "remote:my_app:falkordb" in names

    def test_same_dir_no_double_scan(self, tmp_path, monkeypatch):
        """When cwd == ROOT, configs are scanned only once."""
        config_dir = tmp_path / ".cortext-harness" / "config"
        config_dir.mkdir(parents=True)
        _write_config(config_dir, "proj", {
            "project": {"code": "proj"},
            "storage_backend": "local",
        })
        monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)

        projects = LIFECYCLE._scan_project_backends()
        assert len(projects) == 1
        assert projects[0]["project_id"] == "proj"
```

### `tests/test_make_lifecycle.py` — Scan Merge Test

```python
def test_scan_merges_root_and_caller_configs(self, tmp_path, monkeypatch):
    """_scan_project_backends merges ROOT + caller configs."""
    root_config = tmp_path / "repo" / ".cortext-harness" / "config"
    root_config.mkdir(parents=True)
    _write_config(root_config, "repo_proj", {
        "project": {"code": "repo_proj"},
    })

    caller_config = tmp_path / "caller" / ".cortext-harness" / "config"
    caller_config.mkdir(parents=True)
    _write_config(caller_config, "caller_proj", {
        "project": {"code": "caller_proj"},
        "storage_backend": "remote",
        "remote": {"qdrant_url": "http://localhost:6333"},
    })

    monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path / "repo")
    monkeypatch.chdir(tmp_path / "caller")

    projects = LIFECYCLE._scan_project_backends()
    ids = [p["project_id"] for p in projects]
    assert "repo_proj" in ids
    assert "caller_proj" in ids
    assert len(projects) == 2
```

## Validation

Run existing + new tests:

```bash
python -m pytest tests/test_doctor_remote.py tests/test_make_lifecycle.py -v
```

## Acceptance Criteria

- New tests pass.
- All existing tests in `test_doctor_remote.py` and `test_make_lifecycle.py`
  continue to pass.
- Manual verification: `dev doctor` from a project with remote config shows
  Qdrant/FalkorDB connectivity checks.
