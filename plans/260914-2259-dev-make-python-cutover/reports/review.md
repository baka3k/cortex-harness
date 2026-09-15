# Review report — dev/make Python cutover (phase 01–07)

**Ngày:** 2026-09-15 • **Reviewer:** independent agent (general-purpose, read-only) •
**Scope:** commits `59330ba..HEAD` (7 phase commits + 2 fix cycles) •
**Modes:** hi-craft --full (review MUST, max 3 fix cycles)

## Vòng 1 — VERDICT FAIL, 6.5/10

1 CRITICAL + 5 MAJOR. Verification độc lập của reviewer: build OK, cortex-dev tests
23/23, pytest 61+34, bridge-grep 0.

- **C1 (security):** `dev import-db` extract tar không có data-filter — member
  symlink/hardlink cho phép ghi file tuỳ ý; Python reference dùng
  `tarfile.filter="data"`.
- **M1:** bridge-ban test tồn tại nhưng không CI nào chạy.
- **M2:** parity GATE 1 đỏ vĩnh viễn (migrate/ensure-ort rust-only) — không có
  allowlist → exit code vô nghĩa.
- **M3:** `resolve_project_doc_targets` abort toàn bộ lookup khi 1 config json hỏng
  (python `continue` từng file).
- **M4:** GATE 7 embedded probe không phát hiện được discovery-mất (shape-blind).
- **M5:** test binary-only tự skip trong CI (workflow không build rust).

## Fix cycle 1 — PASS, 8/10

C1: `tar -tvzf` + type gate (`-`|`d`) trước extract — reviewer tự craft archive
symlink → bị từ chối, target ngoài không bao giờ được tạo (e2e). M1/M5: CI build
binary + chạy đủ 4 suite. M2: allowlist `RUST_ONLY_ROOT_SUBS` + subset compare →
**76/76 exit 0**. M3: per-file continue. M4: live probe hạ thành observation-parity
(sandbox-hostile, honest) + **synthetic-table parity test mới**
(`tests/test_embedded_discovery_parity.py`: cùng ProcessRecord tổng hợp feed vào cả
2 engine, cả 2 trả `[9999]`). Cheap minors: env truthiness, sidecar repo-anchored,
purge `path_for` abort, ORT sha256 verify.

## Fix cycle 2 — PASS, 9/10 ("Shippable as-is")

5 điểm còn lại: discovery-test binary resolution (debug-first + env + skip guard);
bỏ inert listing name-parse (giữ type gate + tar sanitization, ghi rõ trust
boundary); safe_parser unicode alnum; device setdefault truthiness; bỏ dead
`has_uri` (withdrawn — code gốc đã parity-correct).

## Follow-up cuối (đã commit)

CI build thêm debug binary để synthetic discovery gate chạy trên runner sạch.

## Residuals chấp nhận (documented, none blocking)

- Journal precreate `File::create` 0644 vs 0o600; purge parent-dir cleanup/fsync.
- Makefile missing-binary exit 127 (thiếu hint D1); `stop_rust_mcp` TERM-only.
- storage_backup lease release trên early-exit (flock giải phóng khi process exit).
- Python-launcher reference tests còn pin — archive cùng `mcp-lifecycle.py` khi 5
  shim actions port xong.
- 12 test fail pre-existing trong `tests/test_incremental_sync_*.py` +
  `test_mcp_acceptance_matrix.py` — reproduce trên baseline KHÔNG có thay đổi của
  plan này (thuộc workstream change-db).

## Gate vận hành còn lại (theo plan, ngoài scope code)

Windows smoke trước flip; dogfood 1 tuần trong window umbrella; archive
`mcp-lifecycle.py` sau khi 5 shim action port (doctor/start/stop/infra-up/infra-down).
