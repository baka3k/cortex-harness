---
title: "dev doctor — Caller-Aware Config Resolution"
status: active
created: 2026-08-20
updated: 2026-08-20
mode: hi-plan --full
scope: scripts/mcp-lifecycle.py, tests/test_doctor_remote.py, tests/test_make_lifecycle.py
relatedPlans:
  - 260818-infra-up-remote-support
  - 260820-dev-init-backend-selection
  - 260817-storage-backend-adapter
blockedBy: []
blocks: []
---

# dev doctor — Caller-Aware Config Resolution

## Problem Statement

`dev doctor` luôn scan config từ `ROOT/.cortext-harness/config/` (cortex-harness
repo root, tính từ `Path(__file__).resolve().parents[1]`). Khi user chạy `dev doctor`
từ một project khác có `.cortext-harness/config/{env}.json` với
`storage_backend: "remote"`, doctor không đọc được config đó → hiển thị
`[doctor][ok]   remote projects - none configured` thay vì check Qdrant/FalkorDB
connectivity.

**Root cause:** `_scan_project_backends(config_dir=None)` default vào
`ROOT / ".cortext-harness" / "config"`. `ROOT` hardcode từ `__file__`, không
phải từ `cwd`. `dev.py` đã set `cwd=caller_directory` cho subprocess nhưng
`_scan_project_backends()` không dùng `cwd`.

## Cross-Plan Dependencies

- **`260818-infra-up-remote-support`** (active): `invoke_infra_up()` cũng dùng
  `_scan_project_backends()` — cùng bug. Fix `_scan_project_backends()` sẽ
  fix cả `infra-up`. Bidirectional: plan 260818 cần note caller-aware scan.
- **`260820-dev-init-backend-selection`** (done): `dev init` đã ghi
  `storage_backend`/`remote` vào config — config format đã ổn định, plan này
  chỉ fix reader side.
- **`260817-storage-backend-adapter`** (active): Không thay đổi adapter.

## Verified Current State

### Config Scan Flow

- `ROOT = Path(__file__).resolve().parents[1]` — cortex-harness repo root.
- `_scan_project_backends(config_dir=None)` → `ROOT / ".cortext-harness" / "config"`.
- cortex-harness repo **không có** file `.cortext-harness/config/*.json` nào.
- `_scan_project_backends()` luôn return `[]`.

### Doctor Flow

- `dev doctor` → `dev.py:_run_lifecycle("doctor")` → subprocess với
  `cwd=caller_directory`.
- `invoke_doctor()` gọi `doctor_remote_checks()` → `_scan_project_backends()` → `[]`.
- Doctor report "remote projects - none configured" bất kể caller config.

### infra-up Flow (same bug)

- `invoke_infra_up()` cũng gọi `_scan_project_backends()` → same issue.

### dev.py Dispatch

- `_run_lifecycle()` chỉ set `cwd`, không pass caller path as argument.
- `mcp-lifecycle.py` không có cách nào biết caller directory ngoài `Path.cwd()`.

## Target Architecture

### Scan Strategy: cwd + ROOT Merge

```
_scan_project_backends(config_dir=None)
    │
    ├─ primary = config_dir or ROOT / ".cortext-harness" / "config"
    ├─ caller  = Path.cwd() / ".cortext-harness" / "config"
    │
    ├─ Scan primary (existing behavior)
    ├─ Scan caller (if caller != primary and caller.is_dir())
    │
    └─ Merge: caller projects appended after primary projects
       (no dedup — different directories = different projects)
```

### Doctor Behavior After Fix

```
dev doctor  (from /path/to/my-project/)
    │
    ├─ (existing local checks: python, paths, round-trip)
    │
    └─ doctor_remote_checks():
          │
          ├─ _scan_project_backends() → scans ROOT config + cwd config
          │
          ├─ Finds /path/to/my-project/.cortext-harness/config/dev.json
          │   with storage_backend: "remote"
          │
          ├─ For each remote project:
          │     ├─ validate_backend_config()
          │     ├─ probe_qdrant() → reachable/unreachable
          │     └─ probe_falkordb() → reachable/unreachable
          │
          └─ Report per-backend status
```

### invoke_infra_up Behavior After Fix

Same scan logic — infra-up also picks up the caller's project config.

## Phases

1. [Phase 01 — Caller-aware scan in `_scan_project_backends()`](phase-01-caller-scan.md)
2. [Phase 02 — Testing & validation](phase-02-testing.md)

## Expected File Changes

### Lifecycle Scripts

- `scripts/mcp-lifecycle.py`:
  - `_scan_project_backends()` — add `Path.cwd()` config scan alongside ROOT.
  - No changes to `doctor_remote_checks()` or `invoke_doctor()` — they already
    call `_scan_project_backends()` correctly, just the scan root was wrong.

### Tests

- `tests/test_doctor_remote.py` — add test: caller dir has remote config,
  doctor picks it up.
- `tests/test_make_lifecycle.py` — add test: `_scan_project_backends()` merges
  ROOT + caller configs.

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Double-counting nếu cwd == ROOT | Skip caller scan khi `caller_config == primary_config`. |
| Caller dir không phải project (e.g. `/tmp`) | Forgiving: nếu `.cortext-harness/config/` không tồn tại → skip. |
| Order: ROOT projects appear before caller | Acceptable: doctor report tất cả, order không ảnh hưởng correctness. |
| infra-up cũng được fix (side effect) | Desired — cùng bug, cùng fix. |
| Security: doctor đọc config từ arbitrary cwd | cwd đã là user's choice (họ cd vào đó), không phải attack vector. |

## Success Criteria

- `dev doctor` từ project dir có `storage_backend: "remote"` hiển thị
  remote connectivity check cho project đó (Qdrant reachable?, FalkorDB reachable?).
- `dev doctor` từ cortex-harness root behavior không đổi (backward compatible).
- `make infra-up` từ project dir với remote config cũng pick up đúng.
- Test suite cover: caller-config scan, merged scan, skip when same dir.
