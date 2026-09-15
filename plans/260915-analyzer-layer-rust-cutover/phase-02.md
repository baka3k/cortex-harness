# Phase 02 — Port analyzer-dart (dart primary + flutter overlay)

## Mục tiêu

Crate `analyzer-dart` thay `code-tiny/tools/flutter/` (1,659 LOC) cho cả mode `dart` (primary) và `flutter` (overlay).

## Entry criteria (red-team finding 12 — không đạt thì dừng trước khi code)

1. **Grammar pin verified**: `tree-sitter-dart` trên crates.io ở revision khớp PyPI 0.1.0 (Python đang dùng) — kiểm chứng bằng AST dump so sánh; nếu crates.io lag/không có → **vendored grammar** (git subtree của repo grammar) ghi vào `rust/grammar-versions.toml` (hiện chỉ có pin python/js/ts/php/java).
2. **Coordinate plan `260714-1603-flutter-analyzer-parser`** (in_progress = moving target): pause hoặc land trước khi freeze parity baseline; ghi quyết định vào report phase.
3. **`--mode` contract**: Python chấp nhận `dart|flutter|all` (`flutter_analyzer.py:168`) — quyết định tường minh: Rust binary hỗ trợ `dart|flutter`; `all` → hard error hướng dẫn chạy 2 lần hoặc map thành cả hai (chọn khi implement, ghi vào report).

## Scope code

| Thành phần | Ghi chú |
|---|---|
| `rust/crates/analyzer-dart/` (mới) | [[bin]] `analyzer-dart`; CLI contract byte-stable; `--mode dart\|flutter`; tree-sitter-dart + project-local symbol resolution + `detect_flutter_project` (đối chiếu bản "approximate" ở orchestrator phase09 report:112 — tránh duplicate) |
| `rust/grammar-versions.toml` | pin dart (entry criterion 1) |
| `rust/crates/cortex-sync/src/registry.rs` | map `dart` + overlay `flutter` → `analyzer-dart` |
| Embedding | dart ∈ `SHARED_VECTOR_CLI_PARSERS`: binary KHÔNG embed — embedding-input artifact emission theo contract frozen phase-01 (phase-06 kích hoạt); chấp nhận-and-ignore flags đến lúc đó |

## Parity gate

1. `scripts/rust_parity/analyzer_parity_dart.py` + `_flutter` (mẫu `_p08_common`): dual-run PY vs RS, graph diff 0 ngoài mask, `[SCAN_RESULT]` byte-identical, incremental counts.
2. **Orchestrator leg riêng** (red-team C6): dart/flutter chạy qua `sync_orchestrator_parity` (opt-in rust) trước khi merge phase.
3. `cargo test -p analyzer-dart` + clippy sạch.

## Exit criteria

- Parity PASS cả 2 mode + orchestrator leg; reports `phase02-dart-parity.md`, `phase02-flutter-parity.md`, `phase02-orchestrator-leg.md`.
- Grammar pin decision ghi rõ (crates.io revision hoặc vendored).
