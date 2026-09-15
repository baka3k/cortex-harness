# Phase 08 — Dogfood → flip default + DELETE (1 commit cuối)

## Mục tiêu

Cutover bất đối xứng kết thúc theo red-team finding 3: **flip default + xoá scripts là 1 commit cuối duy nhất**, gated trên dogfood thật + rollback drill. Rollback sau commit = git revert (directive user).

## 1. Dogfood gate (trước commit)

- ≥1 kỳ dogfood sync thật trên stock repo người dùng với default flip CẤM — dùng opt-in `=rust` để đo architecture mới đã đóng băng (red-team A10/F3: default không đổi trong 01–07 nên dogfood đo hệ thống ổn định).
- Runbook umbrella yêu cầu 7 ngày liên tiếp sạch cho flip vận hành — plan này theo gate đó làm ops sign-off của phase (status plan không "done" trước khi gate này pass).

## 2. Flip cuối cùng (trong commit xoá)

| `CORTEX_RUST_ANALYZER` | semantics MỚI (cả cortex-sync + delegation target) |
|---|---|
| unset | **Rust-if-binary** (auto-flip phase-14) |
| `rust` | Rust binary; missing → hard error + hint build |
| `python` hoặc giá trị khác, hoặc missing-binary khi unset | **Loud "retired" error**: "Python analyzer plane retired at <commit>; rollback = git revert <tag>" — KHÔNG silent fallback (red-team S3/S4: mọi cell, cả delegated path) |

- `--version` build-commit handshake: orchestrator check binary version khớp expected; lệch → hard error (red-team F5 stale binary).
- Embedding/message artifacts thiếu/invalid từ child → hard-error, không bao giờ im lặng.

## 3. Delete list (chính xác)

| Xoá | Ghi chú |
|---|---|
| Entry `*_analyzer.py` + module riêng của: cobol, delphi, java, kotlin, android, vb×4, python, go, perl, shell, jp1, rust, swift, js, ts, php, sql, plsql, cplus (entry), csharp (entry + adapter), flutter (toàn bộ), project_topology, overlays (spring, servlet_jsp, mybatis, struts, aspnet×2, web_framework, database_schema) | ~84,780 LOC; grep audit per-dir: chỉ xoá file KHÔNG được import bởi phần giữ |
| GIỮ | `tools/common/`, `tools/sync/` (delegation target), `tools/graph/`, cplus clang plane, `csharp/roslyn_worker/`, `scripts/rust_parity/` (thêm header "PY side archived, fixtures = golden"), `cortex_harness/dev.py` dicts (parity-reference), message_scan.py nếu native port còn tham chiếu logic (quyết khi implement) |
| Nếu fallback end-state kích hoạt (phase-06 FAIL) | Delete list thu hẹp: giữ scripts của 7 vector parsers |

**Audit dispositions nêu đích danh** (red-team C9/F7): `cortex_harness/sync_processes.py:83`, `code-tiny/run_migration.py:8-12`, `code-tiny/tests/test_analyzer_provider_wiring.py:15-24`, `skills/code-graph-ingest/SKILL.md`, CI workflows (đã update phase-07), tests Python (skip phải **loud** — pytest report liệt kê skips, không `importorskip` âm thầm).

## 4. Rollback drill (red-team F9 — gate trước khi tuyên bố xong)

1. Scratch checkout tại tag trước delete; copy post-cutover graph (falkordb/ladybug dump) + qdrant collection.
2. Chạy sync Python-plane (revert state) trên copy đó → incremental cleanup phải hội tụ: node/rel diff 0 sau 2 kỳ sync, stale vectors dọn đúng.
3. Kết quả ghi `phase08-rollback-drill.md`.

## 5. Verification trước khi commit xoá

1. Grep audit theo disposition list → rỗng (ngoài phần giữ có chủ đích).
2. `cargo build --release` + `cargo clippy -D warnings` toàn workspace; CI green.
3. Sync smoke không env (default mới): mọi children binary mọi lane (native + delegated); summary khớp phase-07 baseline.
4. pytest: passes + skips đều được report rõ.

## 6. Bookkeeping

- Umbrella `260913-2130-rust-full-migration`: note "analyzer layer cutover + Wave-D gate closed by 260915-analyzer-layer-rust-cutover".
- Spike plan frontmatter: blockedBy resolution theo kết quả phase-06.
- Flutter plan `260714-1603`: chuyển reference-only (script đã archive).
- `reports/phase08-cutover.md`: commit hash, tag, LOC xoá thực tế, drill kết quả.

## Exit criteria

- Spawn path `dev sync code` 100% binary Rust mọi lane; dogfood gate 7 ngày pass (ops sign-off); rollback drill converge.
- Plan status → done sau khi drill + dogfood sign-off ghi vào report.
