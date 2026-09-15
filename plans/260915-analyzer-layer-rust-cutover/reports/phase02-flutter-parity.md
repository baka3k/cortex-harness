# Phase 02 — flutter analyzer parity (python vs rust)

- chạy: 2026-09-15 13:53:07
- testdata: `tests/fixtures/flutter-app`
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`
- mode: `flutter` (overlay; analyzer-dart binary chia sẻ với `dart`)
- grammar pin: PyPI `tree-sitter-dart==0.1.0` ↔ Rust vendored `dart-grammar-vendored@0.1.0` (cùng sdist efrenbl/tree-sitter-dart).

### testdata_full — [SCAN_RESULT]

- py: `[SCAN_RESULT] parser=flutter files=6 nodes=15 edges=13 diagnostics=3 graph=0`
- rust: `[SCAN_RESULT] parser=flutter files=6 nodes=15 edges=13 diagnostics=3 graph=0`


## Note

- Chạy `--no-graph`: graph diff skipped (FalkorDB unavailable). `[SCAN_RESULT]` byte-identical = artifact gate PASS. Composition + graph leg ở phase-07.

