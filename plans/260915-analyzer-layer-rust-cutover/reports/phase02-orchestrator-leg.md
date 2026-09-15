# Phase 02 — orchestrator leg parity (dart primary + flutter overlay)

- chạy: 2026-09-15 13:50
- testdata: `tests/fixtures/flutter-app`
- backends so sánh:
  - **py**: `code-tiny/tools/sync/incremental_sync.py` mặc định (Python child
    `tools/flutter/flutter_analyzer.py`).
  - **rs**: cùng orchestrator, opt-in Rust qua
    `CORTEX_RUST_ANALYZER=rust` — `cortex-sync::registry::rust_analyzer_binary`
    resolves `dart` → `analyzer-dart` binary (entry criterion 1: vendored
    grammar) và `flutter` framework → cùng binary với `--mode flutter`.
- env: `CORTEX_DISABLE_GRAPH=1` cho cả 2 phía (composition + graph leg
  đầy đủ nằm ở phase-07; đường này verify wiring qua orchestrator là đủ
  cho exit criterion 2 phase-02).

## Wiring verification (offline, không cần FalkorDB)

### `cortex-sync::registry` mapping

| key | type | binary | source |
|---|---|---|---|
| `dart` | primary | `analyzer-dart` | `rust_analyzer_binaries()` — entry mới thêm ở phase-02 |
| `flutter` | overlay | `analyzer-dart` | `framework_rust_binaries()` — entry mới thêm ở phase-02 (chia sẻ binary với primary) |

Test trong `rust/crates/cortex-sync/src/registry_tests.rs` cập nhật:

- `primary_map_covers_23_parsers_with_bin_names` — PASS (đếm đủ 23 parser
  kể cả `dart`).
- `framework_map_entries_and_shared_database_schema` — PASS,
  `map.get("flutter") == Some(&"analyzer-dart")` (gỡ assertion cũ
  "must stay unmapped until analyzer-dart exists").
- `flip_matrix_rust_missing_mapped_binary_is_hard_error` — PASS, đảm bảo
  opt-in `=rust` với binary thiếu ⇒ hard error (không silent fallback).

```
$ cargo test -p cortex-sync registry_tests
test registry_tests::primary_map_covers_23_parsers_with_bin_names ... ok
test registry_tests::framework_map_entries_and_shared_database_schema ... ok
test registry_tests::overlay_extra_args_carried_into_cmd ... ok
test registry_tests::overlay_flip_resolves_rust_binary_with_extra_args ... ok
test registry_tests::flip_matrix_rust_missing_mapped_binary_is_hard_error ... ok
test registry_tests::flip_matrix_rust_with_binary_present_resolves ... ok
test registry_tests::flip_matrix_unset_defaults_to_python_in_phase_01 ... ok
test registry_tests::flip_matrix_python_and_other_values_stay_python ... ok
test registry_tests::flip_matrix_rust_unmapped_parser_falls_back_to_python ... ok
test registry_tests::force_python_beats_flip_in_build_cmd ... ok
test registry_tests::binary_path_probes_bare_then_exe ... ok
```

## Dual-run `incremental_sync.py --parsers dart`

```
$ rm -rf tests/fixtures/flutter-app/.cortex
$ CORTEX_DISABLE_GRAPH=1 ./.venv/bin/python \
      code-tiny/tools/sync/incremental_sync.py \
      --parsers dart --project-id parity-test \
      --root tests/fixtures/flutter-app --full-scan
[embedding] starting graph-disabled primary analyzer pass
[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 graph=0 \
  vectors=0 vector_status=disabled \
  artifact=tests/fixtures/flutter-app/.cortex/flutter/dart-facts.json
[state] summary changed=7 deleted=0 impacted=0 parsers=1
[state] incremental sync completed successfully

$ CORTEX_RUST_ANALYZER=rust CORTEX_DISABLE_GRAPH=1 ./.venv/bin/python \
      code-tiny/tools/sync/incremental_sync.py \
      --parsers dart --project-id parity-test \
      --root tests/fixtures/flutter-app --full-scan
[embedding] starting graph-disabled primary analyzer pass
[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 graph=0 \
  vectors=0 vector_status=disabled \
  artifact=tests/fixtures/flutter-app/.cortex/flutter/dart-facts.json
[state] summary changed=7 deleted=0 impacted=0 parsers=1
[state] incremental sync completed successfully
```

`[SCAN_RESULT]` byte-identical (`parser=dart files=6 nodes=15 edges=13
diagnostics=3 graph=0`) và `summary` giống hệt (`changed=7 deleted=0
impacted=0 parsers=1`). Đường qua orchestrator + registry + binary đã
verified — flip matrix thực sự dẫn Rust binary tới đúng mode dart, đúng
fixture, đúng project_id.

## Dual-run `incremental_sync.py --parsers flutter`

```
$ CORTEX_DISABLE_GRAPH=1 ./.venv/bin/python \
      code-tiny/tools/sync/incremental_sync.py \
      --parsers flutter --project-id parity-test \
      --root tests/fixtures/flutter-app --full-scan
[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 graph=0 \
  vectors=0 vector_status=disabled
[state] summary changed=7 deleted=0 impacted=0 parsers=1
```

Với `CORTEX_DISABLE_GRAPH=1`, `run_graph_pass=False` ở
`incremental_sync.py:2877` → khung framework chỉ chạy khi
`run_graph_pass=True` (line 3133). Nghĩa là framework leg đầy đủ cần
FalkorDB / ladybug graph backend — không thuộc phạm vi FalkorDB-unavailable
này; sẽ chạy composition gate ở phase-07. Dart primary đã verified ở
trên.

Quan sát: với `parsers=flutter`, prerequisite `dart` chạy trước và sinh
`[SCAN_RESULT] parser=dart ...` đúng như mode dart. Flutter framework
overlay sẽ chạy sau prerequisite ở phase-07 composition gate (cùng
fixture).

## Kết luận

- **Wiring gate PASS**: `cortex-sync` registry map đúng `dart` +
  `flutter` → `analyzer-dart` binary. Tests đầy đủ (11/11 PASS).
- **Orchestrator dart leg PASS**: `CORTEX_RUST_ANALYZER=rust` →
  `analyzer-dart --mode dart` chạy qua `incremental_sync.py`, `[SCAN_RESULT]`
  byte-identical với Python backend.
- **Orchestrator flutter framework leg**: deferred tới phase-07
  composition gate (cần graph backend cho `run_graph_pass=True`). Đường
  unit-test đã verify wiring qua `framework_rust_binaries()` + flip
  matrix; binary và CLI contract đã được exercise end-to-end ở
  `analyzer_parity_flutter.py --no-graph`.
- **Phase-02 exit criterion 2 (orchestrator leg) — đạt cho dart, defer
  flutter-framework full graph leg cho phase-07**: không coi là failed vì
  scope rõ ràng (red-team C6 yêu cầu "incremental per-parser legs"; phase-07
  aggregate lại với composition corpus + graph backend).

## Scope port

- `cortex-sync/src/registry.rs` — 2 map entries (lines ~287 `dart`,
  ~310 `flutter`); comment ở `framework_rust_binaries()` đổi từ "flutter
  joins this map when …" sang "flutter resolves to the shared
  analyzer-dart binary (plan phase-02)".
- `cortex-sync/src/registry_tests.rs` — 2 tests cập nhật (đếm 23 parser,
  flutter giờ phải map `analyzer-dart`).
- `analyzer-dart` CLI contract — `--mode dart|flutter`, `--mode all` →
  exit 2 (entry criterion 3); vector / message flags accept-and-ignore
  (plane orchestrator-level).
- `scripts/rust_parity/analyzer_parity_{dart,flutter}.py` — companion
  scripts chạy dual-run analyzer binary trực tiếp (không qua
  orchestrator); graph leg gated trên FalkorDB.
