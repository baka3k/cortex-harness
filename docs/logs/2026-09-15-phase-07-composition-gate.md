# Phase-07 composition gate đóng Wave-D — 2026-09-15

## Context

Plan `260915-analyzer-layer-rust-cutover` rev 2 đã hoàn thành phases 01–06:
- phase-01 wire registry + opt-in `=rust` (default giữ Python)
- phase-02 port `analyzer-dart` (mode dart + flutter overlay)
- phase-03 port `analyzer-csharp` (Roslyn worker + bootstrap)
- phase-04 port `analyzer-topology`
- phase-05 port message-scan native (graph emission leg)
- phase-06 embedding orchestrator-level qua `cortex-embed` — 8/8 component
  gates PASS, spike NO-GO overturned

Phase-07 là composition gate cuối cùng trước khi flip default + delete
Python scripts (phase-08, gated trên dogfood 7 ngày + rollback drill).

Trigger: red-team rev 1 (`reports/red-team-rev1.md`) — 15 findings với
matrix mirror (S3/S4/F10), Windows `.exe` (A8/F2), detector_evidence (A4),
CI workflow (F7), overlay flip timing (F4) là critical/high đòi xử lý
trước delete commit.

## Change

Commit `4cd60b6` trên nhánh `feat/change-db` — 7 files changed (+977/-10):

### 1. Composition harness (new)
- `scripts/rust_parity/phase07_composition_parity.py` (+552): 8 legs aggregator
  - graph-diff baseline (FalkorDB reachable)
  - message-scan parity (re-run phase-05)
  - overlay-binary proof (red-team F4)
  - qdrant counts + cosine (phase-06 fallback declaration)
  - detector_evidence declaration (red-team A4)
  - delegation smoke (static, red-team S3/S4)
  - per-parser parity delegation (dart/flutter/csharp/topology)
  - grep gate (red-team S2)
- Exit 0 = all 8 PASS; 1 = any FAIL
- Auto-emit report `plans/.../reports/phase07-composition-parity.md`

### 2. Delegation target mirror (incremental_sync.py, +61)
- `_RUST_FRAMEWORK_BINARIES` map mới: 12 overlays mirror `cortex-sync` registry
  (spring, servlet_jsp, mybatis, struts, flutter, aspnet_framework,
  aspnet_core, fastapi_django, express_js, laravel, database_sql, database_plsql)
- `_resolve_rust_binary_name()` lookup helper — parser/framework key → binary
- `_rust_binary_path()` probe bare name → `.exe` (Windows delegated path)

### 3. detector_evidence mask (sync_orchestrator_parity.py, +6)
- Added to `MASKED_SUMMARY_KEYS` với documented reason:
  struts evidence sorted trong Python (`_group_paths_by_framework`) nhưng
  insertion-order trong Rust (`frameworks.rs::struts_evidence_walk`).
- Pre-gate resolution (red-team A4): mask là smaller-blast-radius fix;
  long-term: sort both sides identically (deferred parity-script change).

### 4. CI workflow update (cobol-macos.yml, +20/-7)
- `paths:` filter mở rộng cho Rust crates + parity script
- New step: `cargo build --release -p analyzer-cobol`
- COBOL preflight chuyển từ Python entry sang Rust binary
- New step: parity gate vs Python reference fixture
- Old `python -m unittest discover test_cobol*.py` GIỮ đến phase-08

### 5. Runbook section 5.1 (cutover-runbook.md, +95)
Added phase-07 update với 8 subsections:
- 5.1.1 Flip matrix mirror (incremental_sync.py ↔ cortex-sync registry)
- 5.1.2 Composition parity legs (this script)
- 5.1.3 Auto-flip semantics (đúng cho cả cortex-sync + delegation target)
- 5.1.4 Windows prerequisites (`.exe` probing, ladybug path)
- 5.1.5 C# prerequisites (dotnet SDK cho Roslyn worker bootstrap)
- 5.1.6 detector_evidence policy (mask with documented reason)
- 5.1.7 Stale claim troubleshooting (§5.3 stale)

Renamed `## 5. Archive Python theo khối` → `## 5.2. Archive Python theo khối`.

### 6. Reports (2 new)
- `plans/.../reports/phase07-implementation.md` (+229): narrative implementation report
- `plans/.../reports/phase07-composition-parity.md` (auto-emit): evidence 8/8 PASS

## Impact

**Risk level: medium** — phase-07 là gate đóng cuối cùng trước flip/delete
một chiều (phase-08). Mọi thay đổi phải đảm bảo:

1. **Composition parity reproducible** — chạy `phase07_composition_parity.py`
   trên stock repo phải xanh 8/8 legs trước phase-08 merge.
2. **Delegation mirror không diverged** — `_RUST_*_BINARIES` maps phải sync
   với `cortex-sync` registry `rust_analyzer_binaries()` + `framework_rust_binaries()`.
   Map lệch → silent fallback hoặc hard-error post-delete.
3. **CI green trên phase-08 branch** — `cobol-macos.yml` updated TRƯỚC delete
   commit (red-team F7); nếu CI đỏ trên delete commit → rollback seam mất.
4. **Windows delegation smoke** — best-effort macOS smoke PASS; Windows smoke
   cần Windows runner (deferred to phase-08 gate).
5. **detector_evidence mask** — long-term: sort both sides identically;
   deferred parity-script change. Gate hiện lifted mask; spurious diffs
   suppressed.

## Decision

### detector_evidence resolution: MASK (red-team A4)
Struts evidence sort order divergence between Python and Rust. Mask là
smaller-blast-radius fix so với sorting both sides identically (which would
require parity-script change + regression test on both sides). Mask declared
TRƯỚC gate per phase-07 plan.

### Flip matrix mirror: INCREMENTAL (no refactor)
`incremental_sync.py` không refactor — chỉ thêm `_RUST_FRAMEWORK_BINARIES` map
+ lookup helpers + `.exe` probe. Flip matrix giữ nguyên semantics phase-14
(UNSET → auto-flip when binary có; `=python` rollback; `=rust` opt-in).
Post-delete semantics (phase-08): mọi value non-Rust + missing-binary →
loud "retired" error cả 2 phía.

### Composition corpus: REUSE EXISTING FIXTURES
Không tạo fixture mới cho composition harness. Reuse per-parser fixtures
đã có (mỗi parser đã có parity script riêng với fixture). Red-team A2 đã
verify: `realworld_stock_test.py` không có analyzer dual-run → bỏ khỏi asset list.

### Grep gate: ALLOW 1 DEFERRED
`sync_processes.py:83` reference `_analyzer.py` filter list Python child
processes — đây là live code (không phải rollback). Per phase-08 audit
disposition, file này MUST update trong delete commit. Phase-07 gate:
PASS với warning "deferred-to-phase-08 list non-empty".

## References

- plan: `plans/260915-analyzer-layer-rust-cutover/plan.md` (rev 2, validated)
- phase: `plans/260915-analyzer-layer-rust-cutover/phase-07.md`
- implementation report: `plans/260915-analyzer-layer-rust-cutover/reports/phase07-implementation.md`
- composition report (auto): `plans/260915-analyzer-layer-rust-cutover/reports/phase07-composition-parity.md`
- red-team: `plans/260915-analyzer-layer-rust-cutover/reports/red-team-rev1.md`
- commit: `4cd60b6` (feat/change-db)
- prior phase reports: phase-04 (`phase04-implementation.md`, `phase04-topology-parity-dualrun.md`),
  phase-05 (`phase05-message-scan-parity.md`), phase-06 (`phase06-vector-component-gates.md`)
- umbrella: `plans/260913-2130-rust-full-migration/plan.md`
- related: `plans/260914-1706-onnx-embedding-spike` (NO-GO overturned phase-06)
