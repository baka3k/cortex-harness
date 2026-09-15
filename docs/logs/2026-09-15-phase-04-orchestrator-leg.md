# Phase 04 — analyzer-topology orchestrator leg — 2026-09-15

## Context
`plans/260915-analyzer-layer-rust-cutover/phase-04.md` exit criteria 2:
"sync smoke opt-in: topology child là binary Rust". Topology overlay chạy
cuối sync trên graph đầy đủ, cần xác nhận route qua flip matrix (red-team
C6 yêu cầu orchestrator leg riêng cho mỗi parser mới port TRƯỚC merge
phase). Implementation report `phase04-implementation.md` đã PASS dualrun
parity trên fixture `tests/fixtures/project-topology`; còn lại là wiring
registry (đã land ở `c85ae39` commit trước) + Python mirror + smoke
opt-in + aggregate orchestrator parity leg.

## Change

### Python mirror — `_RUST_ANALYZER_BINARIES`
`code-tiny/tools/sync/incremental_sync.py:1383-1387` thêm entry mirror
với `cortex-sync::registry::rust_analyzer_binaries()`:

```python
# Phase 04 (analyzer-layer-rust-cutover): `analyzer-topology` port
# replaces `tools/project_topology/topology_analyzer.py` for the
# topology overlay at end of sync. Mirrors `cortex-sync` registry
# `rust_analyzer_binaries()`.
"project_topology": "analyzer-topology",
```

(Rust registry wiring đã land ở commit `c85ae39` — không có thay đổi
Rust code trong phase-04 leg này.)

### Sync smoke dual-run
`incremental_sync.py --parsers project_topology` chạy 2 lần (default
Python vs `CORTEX_RUST_ANALYZER=rust`) trên `tests/fixtures/project-topology`:

| Field | Python | Rust (=rust) | Match |
|---|---|---|---|
| `command[0]` | `.venv/bin/python` | `analyzer-topology` binary | flip matrix ✓ |
| `[overlay] project_topology changed=17` | ✓ | ✓ | byte-identical |
| `[project_topology] {...}.descriptors` | 17 | 17 | byte-identical |
| `.dependencies/.endpoints/.frameworks/.modules` | 9/2/3/14 | 9/2/3/14 | byte-identical |
| `.diagnostics[*]` | 4 entries | 4 entries | byte-identical |
| `.graph_writes` | full payload | `{}` | ✗ deviation (fail-closed) |

`graph_writes` divergence: Python dùng embedded FalkorDBLite qua
`--falkordb-path`, Rust analyzer fail-closed (chỉ hỗ trợ remote qua
`--falkordb-uri`). Documented trong `phase04-implementation.md` "Deviation
nhỏ có chủ đích" — không silent fallback.

Direct `cortex-sync` binary run với `--falkordb-uri` →
`graph_writes` đầy đủ (parity với Python embedded).

### Aggregate `sync_orchestrator_parity.py` (parsers=python,shell,ts)
Gates:

| Gate | Result |
|---|---|
| `a_scan_result_lines` (full + incremental) | **PASS** (3 lines PY = 3 lines RS) |
| `c_manifest_changed_sets` per-parser | **PASS** (3 parsers) |
| `d_graph_diff` (FalkorDB) | **PASS** (94 nodes / 126 edges, diff_total=0) |
| `e_change_detection_matrix` per-parser | **PASS** (3 parsers × 2 modes) |
| `b_summary_parity` | FAIL (cosmetic — `command[]` paths diverge) |

`b_summary_parity` chỉ diverge trên command paths: Python orchestrator
auto-flips khi unset (binary có sẵn), Rust orchestrator giữ
`AUTO_FLIP_DEFAULT=false`. Phase-07 matrix mirror sẽ close gap này.

## Impact

- **Who/what:** Python orchestrator đã route topology qua `analyzer-topology`
  binary khi `CORTEX_RUST_ANALYZER=rust`. Opt-in smoke confirmed end-to-end.
  Plan phase-08 flip default → sau dogfood, topology sẽ chạy 100% binary.
- **Risk level:** **low** — wiring nhỏ (1 entry Python mirror), smoke PASS,
  aggregate parity functional (a/c/d/e) PASS. Chỉ có cosmetic divergence
  trên command paths (gate b) — không ảnh hưởng graph/scan/change-detection.
- **Downstream:** phase-05 (message-scan native) + phase-06 (embedding
  component gates) vẫn pin Python children qua `force_python`; phase-07
  composition gate reruns aggregate parity với 24 parsers + 12 overlays +
  topology; phase-08 flip default + retired-error mọi cell.

## Decision

- **Smoke dual-run đủ gate phase-04** — không cần sửa parity script
  `sync_orchestrator_parity.py`. Script cover `python,shell,ts` primary
  parsers; topology leg riêng qua `--parsers project_topology` (fixture
  topology) cover đúng scope. Phase-07 sẽ aggregate trên synthetic
  corpus cover 24+12+topology.
- **Direct `cortex-sync` binary run** thay vì mở rộng parity script —
  verify URI-backend parity (`graph_writes` đầy đủ) trên cùng fixture;
  output parity byte-identical với Python embedded.
- **Document deviation `graph_writes: {}` cho embedded path** trong report
  — không ghi đè bằng mask; thẳng thắn note fail-closed + URI alternative.
  Phase-08 retired-error đóng flip sau khi cortex-sync binary default =
  Rust (sẽ dùng URI-only, deviation biến mất).

## References

- plan: `plans/260915-analyzer-layer-rust-cutover/plan.md`
- phase: `plans/260915-analyzer-layer-rust-cutover/phase-04.md`
- implementation report: `plans/260915-analyzer-layer-rust-cutover/reports/phase04-implementation.md`
- dualrun parity: `plans/260915-analyzer-layer-rust-cutover/reports/phase04-topology-parity-dualrun.md`
- orchestrator leg report: `plans/260915-analyzer-layer-rust-cutover/reports/phase04-orchestrator-leg.md`
- commit: `d1dd8fa` (this session); `c85ae39` (registry wiring + analyzer-topology crate)
- Python mirror: `code-tiny/tools/sync/incremental_sync.py:1383-1387`
- Rust registry (committed trước): `rust/crates/cortex-sync/src/registry.rs:264-292`
- Test updates (committed trước): `rust/crates/cortex-sync/src/registry_tests.rs:87-104, 213-249`
- Smoke fixture: `tests/fixtures/project-topology/` (17 descriptors / 14 modules)
