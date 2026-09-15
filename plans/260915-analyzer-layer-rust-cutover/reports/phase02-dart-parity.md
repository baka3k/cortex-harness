# Phase 02 — dart analyzer parity (python vs rust)

- chạy: 2026-09-15 13:53:05
- testdata: `tests/fixtures/flutter-app`
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- mode: `dart`
- grammar pin: PyPI `tree-sitter-dart==0.1.0` ↔ Rust vendored `dart-grammar-vendored@0.1.0` (efrenbl/tree-sitter-dart, parser.c LANGUAGE_VERSION 15 — `rust/grammar-versions.toml [dart]`); runtime tree-sitter 0.26 (PyPI) ↔ 0.25 (Rust workspace).

### testdata_full — [SCAN_RESULT]

- py: `[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 graph=0`
- rust: `[SCAN_RESULT] parser=dart files=6 nodes=15 edges=13 diagnostics=3 graph=0`


## Note

- Chạy `--no-graph`: graph diff skipped (FalkorDB unavailable). `[SCAN_RESULT]` byte-identical = artifact gate PASS. Composition + graph leg ở phase-07.

