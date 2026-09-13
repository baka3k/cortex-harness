# Phase 04 analyzer framework + python analyzer port (parity PASS) — 2026-09-14

## Context

Phase 04 của `plans/260913-2130-rust-full-migration/plan.md` — phase template cho toàn bộ
Wave D (analyzers batch 1–3 + overlays, phase 05–08): dựng khung `cortex-analyzer-framework`
và port `code-tiny/tools/python/python_analyzer.py` (2.2k) sang Rust làm exemplar. Gate yêu
cầu graph diff rỗng (ngoài mask) giữa backend Python và Rust trên stock thật + testdata,
cả FULL lẫn incremental.

## Change

- **Crate mới `rust/crates/cortex-analyzer-framework/`**: `cli.rs` (CLI contract khớp
  `_build_analyzer_cmd` của orchestrator — gồm flag lane vector/message/cache nhận-và-bỏ-qua,
  `CORTEX_DISABLE_GRAPH`, `--falkordb-uri` scheme validation fail-loud; contract test
  replay dòng lệnh orchestrator), `scan.rs` (COMMON_SCAN_EXCLUDE + fnmatch normcase-darwin),
  `manifest.rs` (`load_manifest_paths`), `ts.rs` (decode `errors="ignore"`, cursor walk),
  `semantic.rs` (port `semantic_inference.py` + `call_graph_builder.py` +
  `confidence_scorer.py` — float parity cho `doc_confidence`, usage-regex memoized),
  `traits.rs` (`Analyzer` + `AnalyzerContext`), `summary.rs`.
- **Crate mới `rust/crates/analyzer-python/`**: parse tree-sitter (walk/docstring/signature/
  base-classes/self-fields/entrypoint/imports), semantic enrich, call resolution
  (arity → self-field type → caller scope), IMPORTS/OVERRIDES, write_all
  `use_full_writers files_variant=with_imports`, incremental cleanup, `[SCAN_RESULT]`.
- **Sửa latent bug phase-03** `rust/crates/cortex-graph-writer/src/project_scope.rs`:
  `prepare_project_scope_parameters` không đệ quy vào `$rows` (array-of-map) → mọi node
  viết từ Rust thiếu `project_id_normalized`; Python inject qua `enrich_project_scope`
  đệ quy. Phase-03 không bắt được vì fixture "stock" normalize là no-op. Fix: enrich
  đệ quy list + sibling `*_normalized` kể cả None — đúng cho cả falkordb/ladybug store.
- **python_analyzer.py** (tham chiếu): call rows giờ mang `project_id` tường minh —
  `rust/crates/analyzer-python/src/resolve.rs` mirror. Giữ `_require_call_project_scope`
  pass cả khi không có journal env (production dùng journal metadata cùng giá trị).
- **`rust/grammar-versions.toml`**: pin `tree-sitter-python` 0.25.0 cả 2 bên. Phát hiện:
  `tree-sitter-languages` 1.10.2 wheels build trên ABI cũ, **raise TypeError** với
  tree-sitter ≥0.25 → `_get_python_parser` thực tế fallback sang `tree_sitter_python`
  crate; pin theo grammar crate, không theo tsl.
- **Harness `scripts/rust_parity/analyzer_parity.py`** + CI wrapper
  `tests/test_rust_analyzer_parity.py` (testdata mặc định, stock opt-in qua
  `CORTEX_PARITY_STOCK=1`; report CI ghi tmp path để không clobber gate report).
- Testdata mới `tests/fixtures/python-analyzer/` (decorators, nested class, unicode,
  self-field resolution, entrypoint).

## Impact

- Risk: **medium** — đây là template mọi analyzer Wave D build trên; bug project_scope
  đã sửa ảnh hưởng正向 cả phase-03 writer (đúng hơn, không breaking: fixture cũ giữ nguyên
  hành vi vì giá trị normalize trùng).
- Hiệu năng: stock 115 .py — python 4.7s vs rust 1.5s (không đặt gate).
- Python giữ default backend; swap qua flag orchestrator khi dogfood pass (phase 09+).

## Decision

- Embedding/Qdrant **không port** (key decision #3 plan) — flags nhận-và-bỏ-qua để
  contract CLI đầy đủ; message scan/flows/llm-summary là plane Python.
- Call rows mang `project_id` tường minh thay vì attach journal runtime cho analyzer Rust
  (journal plane giữ Python/CLI đến phase 09 — quyết định phase-03 giữ nguyên).
- Grammar pin theo grammar crate riêng thay vì `tree-sitter-languages` vì tsl wheels gãy
  ABI với tree-sitter mới; parity empirically PASS trên corpus thật.
- Reviewer (fresh-context) chấm 6/10 với 0 critical — đã fix 6/7 major trước commit:
  is_generic_name lowercase, CLI flags orchestrator, CORTEX_DISABLE_GRAPH, project_id
  abspath, report evidence, Analyzer trait exemplar; từ chối đổi graph-name precedence
  (đọc lại argparse default: flag → env → project_id khớp Python).

## References

- plan: ./plans/260913-2130-rust-full-migration/plan.md (phase-04.md updated)
- report: ./plans/260913-2130-rust-full-migration/reports/phase04-analyzer-parity.md
- commit: 8fff6ac
- harness: scripts/rust_parity/analyzer_parity.py
- review: agent verdict 6/10 → majors fixed, parity re-run ALL PASS sau fix
