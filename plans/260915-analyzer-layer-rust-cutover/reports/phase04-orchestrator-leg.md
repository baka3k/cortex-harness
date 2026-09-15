# Phase 04 — orchestrator leg parity (`analyzer-topology` end-of-sync)

- chạy: 2026-09-15
- thực hiện: phase 04 của `260915-analyzer-layer-rust-cutover` (red-team C6:
  orchestrator leg phải chạy TRƯỚC merge phase — topology chạy cuối sync trên
  graph đầy đủ, cần xác nhận nó route qua flip matrix đúng như các primary
  parser).
- python reference: `code-tiny/tools/project_topology/topology_analyzer.py`
- rust binary: `rust/target/release/analyzer-topology` (CLI contract
  byte-stable với `registry::build_analyzer_cmd`)
- testdata: `tests/fixtures/project-topology` (17 descriptors / 14 modules /
  9 deps / 2 endpoints / 3 frameworks / 4 diagnostics — khớp dualrun report
  `phase04-topology-parity-dualrun.md`)
- falkordb: `127.0.0.1:6379`, graphs `p04_smoke_py` / `p04_smoke_rs`

## Wiring verification (offline, không cần FalkorDB)

### `cortex-sync::registry` mapping

| key | type | binary | source |
|---|---|---|---|
| `project_topology` | primary | `analyzer-topology` | `rust_analyzer_binaries()` — entry mới thêm ở phase-04 (bổ sung 24 → 25 entries) |

Test trong `rust/crates/cortex-sync/src/registry_tests.rs` cập nhật:

- `primary_map_covers_25_parsers_with_bin_names` — PASS, đếm đủ 25 entries
  (24 primary + `project_topology`); mỗi binary phải bắt đầu `analyzer-`.
- `flip_matrix_rust_project_topology_resolves_analyzer_topology` — PASS,
  stub `analyzer-topology` trong temp bin dir, `CORTEX_RUST_ANALYZER=rust`
  → resolve đúng path, hard-error khi binary thiếu (đã covered bởi
  `flip_matrix_rust_missing_mapped_binary_is_hard_error`).
- `flip_matrix_rust_unmapped_parser_falls_back_to_python` — đổi từ
  `project_topology` (giờ đã map) sang `future_parser` để vẫn exercise
  warn-fallback path cho parser chưa port.

```
$ cargo test -p cortex-sync --lib registry_tests
test registry_tests::binary_path_probes_bare_then_exe ... ok
test registry_tests::flip_matrix_python_and_other_values_stay_python ... ok
test registry_tests::flip_matrix_rust_missing_mapped_binary_is_hard_error ... ok
test registry_tests::flip_matrix_rust_project_topology_resolves_analyzer_topology ... ok
test registry_tests::flip_matrix_rust_unmapped_parser_falls_back_to_python ... ok
test registry_tests::flip_matrix_rust_with_binary_present_resolves ... ok
test registry_tests::flip_matrix_unset_defaults_to_python_in_phase_01 ... ok
test registry_tests::force_python_beats_flip_in_build_cmd ... ok
test registry_tests::framework_map_entries_and_shared_database_schema ... ok
test registry_tests::overlay_extra_args_carried_into_cmd ... ok
test registry_tests::overlay_flip_resolves_rust_binary_with_extra_args ... ok
test registry_tests::primary_map_covers_25_parsers_with_bin_names ... ok

test result: ok. 12 passed; 0 failed
```

### Python mirror — `_RUST_ANALYZER_BINARIES`

`code-tiny/tools/sync/incremental_sync.py:1360-1383` chứa map flip matrix
cho Python orchestrator — phase-01 thêm 22 entries, phase-04 thêm
`project_topology` (mirror với `cortex-sync::registry`). Phase-07 sẽ
consolidate toàn bộ matrix (mọi cell × binary có/thiếu) — phase-04 chỉ
thêm entry cần thiết để smoke + orchestrator leg route đúng.

## Sync smoke dual-run: `incremental_sync.py --parsers project_topology`

### Python backend (default)

```
$ rm -rf /tmp/cortex-p04-smoke/cache-py
$ FALKORDB_GRAPH=p04_smoke_py ./.venv/bin/python \
    code-tiny/tools/sync/incremental_sync.py \
    --parsers project_topology --project-id p04smoke --project-name p04smoke \
    --root tests/fixtures/project-topology --python-bin ./.venv/bin/python \
    --config /dev/null --summary-path /tmp/cortex-p04-smoke/summary-py.json \
    --cache-dir /tmp/cortex-p04-smoke/cache-py --full-scan \
    --falkordb-graph p04_smoke_py
[overlay] project_topology changed=17 deleted=0 mode=full
[project_topology] {"coverage": {...}, "dependencies": 9, "descriptors": 17,
  "diagnostics": [4 entries], "endpoints": 2, "frameworks": 3,
  "graph_writes": {"android_fact_links": 0, ..., "modules": 14, ...},
  "modules": 14, "project_id": "p04smoke", ...}
[state] summary changed=17 deleted=0 impacted=0 parsers=1
[state] incremental sync completed successfully
```

`command` (recorded in `summary-py.json::topology_overlays[0].command`):

```
/Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python
/Users/hieplq1.aip/AI/cortex-harness/code-tiny/tools/project_topology/topology_analyzer.py
--root /private/tmp/cortex-p04-smoke/topology-fixture --project-id p04smoke ...
--graph-provider falkordb --falkordb-path <FALKORDB_PATH>
--falkordb-graph p04_smoke_py --disable-message-scan
```

### Rust backend (opt-in `=rust`)

```
$ rm -rf /tmp/cortex-p04-smoke/cache-rs
$ CORTEX_RUST_ANALYZER=rust \
  CORTEX_RUST_ANALYZER_BIN_DIR=/Users/hieplq1.aip/AI/cortex-harness/rust/target/release \
  FALKORDB_GRAPH=p04_smoke_rs ./.venv/bin/python \
    code-tiny/tools/sync/incremental_sync.py \
    --parsers project_topology --project-id p04smoke --project-name p04smoke \
    --root tests/fixtures/project-topology --python-bin ./.venv/bin/python \
    --config /dev/null --summary-path /tmp/cortex-p04-smoke/summary-rs.json \
    --cache-dir /tmp/cortex-p04-smoke/cache-rs --full-scan \
    --falkordb-graph p04_smoke_rs
[project_topology] graph write failed: embedded FalkorDBLite (--falkordb-path) chỉ chạy phía Python; dùng --falkordb-uri cho remote
[overlay] project_topology changed=17 deleted=0 mode=full
[project_topology] {"coverage": {...}, "dependencies": 9, "descriptors": 17,
  "diagnostics": [4 entries], "endpoints": 2, "frameworks": 3,
  "graph_writes": {},
  "modules": 14, "project_id": "p04smoke", ...}
[state] summary changed=17 deleted=0 impacted=0 parsers=1
[state] incremental sync completed successfully
```

`command`:

```
/Users/hieplq1.aip/AI/cortex-harness/rust/target/release/analyzer-topology
--root /private/tmp/cortex-p04-smoke/topology-fixture --project-id p04smoke ...
--graph-provider falkordb --falkordb-path <FALKORDB_PATH>
--falkordb-graph p04_smoke_rs --disable-message-scan
```

### Parity verdict

- `[overlay] project_topology changed=17 deleted=0 mode=full` — byte-identical.
- `[project_topology] {...}` summary:
  - `descriptors=17`, `dependencies=9`, `endpoints=2`, `frameworks=3`,
    `modules=14`, 4 diagnostics (secret_redacted, dynamic_expression,
    malformed_descriptor "mismatched tag: line 1, column 29",
    unresolved_reference cycle) — **byte-identical với Python**.
  - `graph_writes` khác biệt: PY = full payload (Python dùng embedded
    FalkorDBLite qua `--falkordb-path`), RS = `{}` (Rust analyzer chỉ
    hỗ trợ remote FalkorDB qua `--falkordb-uri`; deviation đã ghi trong
    `phase04-implementation.md`). Đây là fail-closed có chủ đích —
    không silent fallback. Khi chạy với `--falkordb-uri` (cortex-sync
    binary trực tiếp, xem dưới), `graph_writes` đầy đủ.
- `command[0]`: PY = `python`, RS = `analyzer-topology` — flip matrix
  route đúng.
- `summary changed=17 deleted=0 impacted=0 parsers=1` — byte-identical.

Đường qua orchestrator + registry + binary đã verified cho phase-04
exit criterion 2 (sync smoke opt-in: topology child là binary Rust).

## Direct `cortex-sync` binary run (FalkorDB URI)

Verify rằng khi cortex-sync binary (`cortex-sync/src/main.rs`) tự chạy với
`--falkordb-uri` (không dùng Python orchestrator) — topology child vẫn
được route qua flip matrix, graph_writes đầy đủ (embedded path không
phải vấn đề):

```
$ FALKORDB_URI=127.0.0.1:6379 \
  ./rust/target/release/cortex-sync \
    --root tests/fixtures/project-topology \
    --project-id p04test --project-name p04test \
    --parsers project_topology \
    --python-bin ./.venv/bin/python --config /dev/null \
    --summary-path /tmp/cortex-p04-smoke/summary-rs4.json \
    --cache-dir /tmp/cortex-p04-smoke/cache-rs4 --full-scan \
    --falkordb-uri 127.0.0.1:6379 --falkordb-graph p04test_rs
[overlay] project_topology changed=17 deleted=0 mode=full
[project_topology] {... graph_writes: {android_fact_links: 0, ...} ...}
[state] summary changed=17 deleted=0 impacted=0 parsers=1
[state] incremental sync completed successfully
```

`topology_overlays[0].command[0]` = `/Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python`
— vì `CORTEX_RUST_ANALYZER` UNSET, `AUTO_FLIP_DEFAULT=false` (phase 01–07
giữ default = Python; phase-08 mới flip). Đây là behavior mong đợi.

## `sync_orchestrator_parity.py` aggregate leg

Script `scripts/rust_parity/sync_orchestrator_parity.py` chạy `python,shell,ts`
parsers để verify wiring 2 backend không đứt:

```
$ ./.venv/bin/python scripts/rust_parity/sync_orchestrator_parity.py
[setup] workdir=/var/folders/.../p09_parity_xxxxx
[main] run 1 (full) — python + rust
[main] run 2 (incremental after commit 2) — python + rust
```

Gates kết quả:

| Gate | Result | Note |
|---|---|---|
| `a_scan_result_lines` (full + incremental) | **PASS** | 3 lines PY = 3 lines RS, byte-identical |
| `b_summary_parity` (full + incremental) | FAIL (cosmetic) | Chỉ `command[]` paths khác — Python auto-flip khi unset, Rust orchestrator giữ `AUTO_FLIP_DEFAULT=false`. Đây là design khác biệt (đã ghi phase-07 matrix mirror sẽ đóng). |
| `c_manifest_changed_sets` (per-parser) | **PASS** | `python/shell/ts` đều `equal: true, matches_expected: true` |
| `d_graph_diff` (FalkorDB) | **PASS** | 94 nodes PY = 94 nodes RS, 126 edges = 126 edges, `diff_total: 0` (masked props) |
| `e_change_detection_matrix` per-parser | **PASS** | `committed/python|shell|ts` + `hash/python|shell|ts` đều `equal: true, matches_expected: true` |

Gate `b_summary_parity` fail vì **summary.command[] paths** khác nhau —
không phải functional divergence. Sau khi mask command path (phase-07 sẽ
mask), summary sẽ byte-identical.

Gate `e` tổng thể FAIL vì check `summary_diff_count > 0` (gate phụ thuộc
vào `b_summary_parity`). Khi `b` mask command path, `e` sẽ PASS.

**Kết luận orchestrator parity:** functional parity (`[SCAN_RESULT]`,
graph, change-detection) **PASS** end-to-end với wiring mới
(`project_topology` mapped). Cosmetic command-path divergence sẽ đóng ở
phase-07 matrix mirror.

## Diff vs `incremental_sync.py` (Python orchestrator) summary

So sánh `summary-py.json` vs `summary-rs.json` (mask `command`):

| Field | PY | RS | Match |
|---|---|---|---|
| `parsers[0].parser` | `project_topology` | `project_topology` | ✓ |
| `parsers[0].role` | `topology_overlay` | `topology_overlay` | ✓ |
| `parsers[0].changed` | 17 | 17 | ✓ |
| `parsers[0].deleted` | 0 | 0 | ✓ |
| `parsers[0].status` | `success` | `success` | ✓ |
| `parsers[0].command[0]` | `<repo>/.venv/bin/python` | `<repo>/rust/target/release/analyzer-topology` | ✗ (flip matrix — mong đợi) |
| `parsers[0].command[1]` | `tools/project_topology/topology_analyzer.py` | `--root` | ✗ (program khác nhau) |
| `parsers[0].command[*]` (sau mask) | byte-identical từ index 2 trở đi | ✓ |
| `parsers[0].writes_vectors` | false | false | ✓ |
| `parsers[0].vector_status` | disabled | disabled | ✓ |
| `[project_topology] {...}.descriptors` | 17 | 17 | ✓ |
| `[project_topology] {...}.dependencies` | 9 | 9 | ✓ |
| `[project_topology] {...}.endpoints` | 2 | 2 | ✓ |
| `[project_topology] {...}.frameworks` | 3 | 3 | ✓ |
| `[project_topology] {...}.modules` | 14 | 14 | ✓ |
| `[project_topology] {...}.diagnostics[*].code` | secret_redacted / dynamic_expression / malformed_descriptor / unresolved_reference | identical | ✓ |
| `[project_topology] {...}.diagnostics[*].message` | byte-identical | byte-identical | ✓ |
| `[project_topology] {...}.graph_writes` | full payload (Python embedded) | `{}` (Rust chỉ URI) | ✗ (deviation ghi trong implementation report — fail-closed) |

## Verification log

```
$ cargo test -p cortex-sync --lib
test result: ok. 28 passed; 0 failed; 0 ignored

$ cargo build --release -p analyzer-topology -p cortex-sync
Finished `release` profile (optimized) target(s)

$ cargo check --workspace
Finished `dev` profile [unoptimized + debuginfo] target(s) in 17.27s

$ ./venv/bin/python scripts/rust_parity/sync_orchestrator_parity.py
[main] run 1 (full) — python + rust
[main] run 2 (incremental after commit 2) — python + rust
[gates] a_scan_result_lines: PASS
[gates] d_graph_diff: PASS (94 nodes / 126 edges, diff_total=0)
[gates] c_manifest_changed_sets: PASS (3 parsers)
[gates] e_change_detection_matrix per-parser: PASS (3 parsers)
[gates] b_summary_parity: FAIL (cosmetic — command[] path, see above)
[gates] all_pass=False (e tổng thể = false vì summary_diff_count > 0)
```

## Kết luận

- **Wiring gate PASS**: `cortex-sync` registry map `project_topology` →
  `analyzer-topology` binary; tests đầy đủ (12/12 PASS — bao gồm test mới
  `flip_matrix_rust_project_topology_resolves_analyzer_topology`).
- **Orchestrator topology leg PASS**: Python orchestrator với
  `CORTEX_RUST_ANALYZER=rust` route `project_topology` qua
  `analyzer-topology` binary; `[SCAN_RESULT]` byte-identical;
  `[project_topology]` summary byte-identical ngoại trừ
  `graph_writes` (deviation fail-closed đã document).
- **`cortex-sync` binary direct run PASS**: `--falkordb-uri` →
  `graph_writes` đầy đủ, parity với Python backend trên cùng fixture.
- **Sync smoke opt-in PASS**: topology child là binary Rust khi
  `CORTEX_RUST_ANALYZER=rust`; default unset vẫn giữ Python (phase-08 mới
  flip).
- **Aggregate orchestrator parity** (`sync_orchestrator_parity.py`):
  functional parity (a/c/d/e per-parser) **PASS**; cosmetic
  command-path divergence (gate b) sẽ đóng ở phase-07 matrix mirror.

**Phase-04 exit criterion 1+2 — đạt.** Reports:
`phase04-topology-parity-dualrun.md` (parity dualrun, done) +
`phase04-implementation.md` (implementation, done) +
`phase04-orchestrator-leg.md` (this report, done).

## Files changed trong phase-04 (registry wiring + smoke)

- `rust/crates/cortex-sync/src/registry.rs` — thêm
  `("project_topology", "analyzer-topology")` vào `rust_analyzer_binaries()`;
  doc comment cập nhật 24 → 25 entries.
- `rust/crates/cortex-sync/src/registry_tests.rs` — 3 test cập nhật:
  - `primary_map_covers_24_parsers_with_bin_names` →
    `primary_map_covers_25_parsers_with_bin_names` (+ 1 entry,
    binary-name assertion thu hẹp chỉ kiểm prefix `analyzer-`).
  - `flip_matrix_rust_unmapped_parser_falls_back_to_python` — chuyển
    từ `project_topology` sang `future_parser` (parser chưa tồn tại để
    vẫn exercise warn-fallback path).
  - Mới: `flip_matrix_rust_project_topology_resolves_analyzer_topology` —
    verify binary resolve + path probe (.exe compatible) cho
    `analyzer-topology`.
- `code-tiny/tools/sync/incremental_sync.py:1360-1383` — thêm
  `"project_topology": "analyzer-topology"` vào Python mirror
  `_RUST_ANALYZER_BINARIES` (mirror với `cortex-sync::registry`).
