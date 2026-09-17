# Phase 05 — Verification, Engine Parity, Benchmark, Runbook, Scope Audit

> Gate ra: M6 (không regress + benchmark trong absolute budget); M7 (scope discipline).

## Tasks

### 5.1 Full verification

- Toàn bộ `tests/test_vb6_*.py` + `test_common_analyzer_registry.py` + `test_vb6_message_scan_compat.py` + benchmark harness xanh.
- Chạy 1 sync thật (engine=antlr) trên fixture: `[vb6][summary]` line phản ánh resolution rate mới (không đổi format — observability contract giữ nguyên).

### 5.2 Engine parity check

- Script/test chạy CẢ hai engine (antlr, regex) trên fixture: parity set = **enums, constants, events** (red-team F15 — `properties` không thuộc parity vì ANTLR đại diện property qua functions[] kind property get/let/set; regex `properties` plane là lane riêng) — cùng SET `symbol_id` theo convention regex (F2), không đòi bằng field-level; chỉ symbol coverage + field bắt buộc của hydration.
- `declares[]`: assert chỉ antlr emit; regex path không crash khi payload thiếu plane (hydration tolerant — dataclass với default).

### 5.3 Benchmark (absolute budget guard)

- Chạy `tests/benchmark_vb6_parse_quality.py` trên corpus replicate hiện có: worker-internal ≤50 ms/file, JVM startup ≤5 s (AD-08). Ghi `benchmark-report.md` mới trong plan dir (delta so với plan trước).
- Nếu vượt: profile trước khi tối ưu — nghi phạm: controls ctx-walk (chỉ designer files) + comment token scan; tối ưu bằng early-exit, không đổi kiến trúc.

### 5.4 Runbook + docs (trong tools/vb)

- `docs/plans/260917-1628-*/resync-runbook.md`: các bước re-sync sau cache bump (mirror runbook plan trước: full re-sync 1 lần, expected summary line, rollback = env guards `VB6_PARSER_ENGINE=regex`, `VB6_ANTLR_STRIP_DESIGNER=1`).
- `code-tiny/tools/vb/README.md`: cập nhật phần worker planes + event wiring + guards; ghi rõ Declare/controls là ANTLR-only.
- `dev doctor` vb6 note: không đổi (java requirement như trước).

### 5.5 Scope audit (M7)

- `git diff --name-only <base-commit>..HEAD` phải là tập con: `code-tiny/tools/vb/**`, `tests/**`, `docs/plans/260917-1628-vb6-antlr-depth-upgrade/**`. Vi phạm → hoàn tác hoặc tách PR riêng có owner approval.
- Đối chiếu 13 external touchpoints (research §6): zero-mod còn nguyên (spot-check import/usage).

## Định nghĩa xong

- [ ] M6: mọi test xanh; benchmark trong budget; summary line đúng
- [ ] M7: diff scope sạch; audit ghi vào plan.md
- [ ] Runbook dry-run: re-sync fixture 1 lần thành công sau bump
- [ ] Plan close-out: cập nhật status + exclusions (nếu fallback AD-02/AD-04 dùng)
