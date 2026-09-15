# Python cleanup phase-01 — disposition audit — 2026-09-15

[inline:log-writer] — nhỏ, các decision đã trình bày đầy đủ trong artifact đã commit.

## Context

Craft --full của plan `260915-2230-python-legacy-cleanup` (dọn Python thừa sau Rust cutover).
Phase-02+ bị blockedBy vector-lane port (đang WIP) → lượt này thực hiện phase-01 (audit
read-only). Câu hỏi cốt lõi: Python nào còn LIVE sau khi phase-08 analyzer cutover xoá
~84,780 LOC.

## Change

- Commit `57f902d`: plan 5-phase + reports (disposition.md, smoke-delegation.md,
  research/refs-digest.md 90 findings) + closure lists + `scripts/audit_python_disposition.sh`
  (classifier tái chạy: AST import closure + path-spawn keeps + CI entry class).
- Kết quả: 582 .py = **97 LIVE / 104 PARITY / 8 ENTRY / 373 DEAD**.
- Smoke (scratch instance `cleanup-smoke`): `[cortex-sync] python-plane delegation` fired
  thực tế trên ladybug local (`orchestrator.rs:276`); embedded falkordb fail-closed
  (`graphops.rs:132`) — **sync-plane còn sống, phase-03 khoá nhánh B** (không xoá
  `incremental_sync.py` + closure 97 file).
- Reviewer (spawn, failure-modes) tìm 3 Critical / 2 High / 4 Medium — fix hết trong 1 cycle:
  `mcp-lifecycle.py` vẫn live cho infra/doctor (`lifecycle.rs:93-95`),
  `setup_constraints.py` path-spawn (`remote_probe.rs:439-446`), 8 file CI/pytest vào nhóm
  ENTRY protected, `check-delete` chuẩn hoá relpath + fail-closed out-of-repo.

## Impact

- Mọi phase xoá sau này (02–05) giờ có gate máy móc: `audit_python_disposition.sh
  check-delete/verify-live` — xoá nhầm file live sẽ exit 1.
- Risk thấp: phase-01 chỉ đọc + reports; smoke chạy trên scratch, fixture gốc đã trả về
  trạng thái sạch. Worktree có session song song sửa mcp/vector-lane — commit này scoped
  chặt, không chạm.

## Decision

- Không xoá sync-plane: smoke chứng minh `dev sync` trên local storage (falkordb/ladybug)
  vẫn đi qua Python `incremental_sync.py` (resolve_storage, journal lane). Muốn zero-Python
  ở đây phải port storage resolution + journal lane sang Rust — plan riêng.
- `cortex_harness/dev.py` giữ: là repo-root marker (`cortex-dev/src/util.rs:16,22`).
- Deviation-accepted: smoke 2 run thay vì 3 (run 3 không tăng thông tin — rationale trong
  smoke-delegation.md).

## References

- plan: ./plans/260915-2230-python-legacy-cleanup/plan.md
- commit: 57f902d
- disposition: ./plans/260915-2230-python-legacy-cleanup/reports/disposition.md
