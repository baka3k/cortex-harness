# Rust Extraction Layer — End-to-End Verification — 2026-07-31

## Context
Plan `260731-1030-rust-extraction-layer` covers Phases 1–6 of moving the
CPU-bound Python extraction layer into a Rust PyO3 extension. All six
phases were committed before this session (`3bcfc79` → `a5c8482` →
`ba16168` → `716f260`). The `hi-craft --full` invocation landed here as
a verification pass — no new code was authored. The goal was to confirm
the existing implementation still builds cleanly and that the full
Python integration surface (extension import, public API, batch,
multi-language dispatch) is wired correctly.

## Change
None — verification only.

End-to-end probes executed:
- `cargo test --release --lib` from `rust-analyzer-core/` →
  21 unit tests pass (`resolver::tests` 5, `semantic::tests` 8,
  `grammar::tests` 8); 52 dead-code warnings on retained helper fns.
- `PYTHONPATH=. python tests/test_rust_parity.py` →
  10 pytest cases pass (`simple_class`, `template_function`,
  `namespace_nested`, `macro_heavy`, `batch_parallel`,
  `resolve_batch_sets_callee_id`, `enrich_corpus_sets_intent`,
  `supported_languages_lists_grammars`,
  `detect_language_dispatches_by_extension`,
  `parse_root_kind_for_each_language`).
- `import cortex_extract` from Python (with
  `PYTHONPATH=rust-analyzer-core`) — extension loads,
  `supported_languages()` returns
  `['cpp','c','java','python','javascript']`,
  `detect_language()` maps `.cpp/.h → cpp`, `.java → java`,
  `.py → python`, `.js → javascript`.
- Public Python wrapper `cplus._rust_fallback` exercises
  `extract_cplus(simple_class.cpp, …)` → returns a dict with all
  15 ParseResult keys (`aliases`, `calls`, `fields`, `file_def`,
  `function_types`, `functions`, `includes`, `macros`, `namespaces`,
  `parse_meta`, `relations`, `templates`, `types`, `using_imports`,
  `using_namespaces`); 6 functions, 3 types extracted.
- `extract_cplus_batch(all_fixtures, threads=2)` → returns 4
  payloads matching the 4 input `.cpp/.h` fixture files.

## Impact
**Risk level:** low. No source files were modified; this entry
captures the green-build state the project was in when verified.

One pre-existing limitation was confirmed: Phase 6 ships a working
multi-language grammar registry and Python entry points, but the
extraction walker itself remains C++-specific. `extract_batch(language="java")`
or `"python"` would currently route through the C++ walker with
incorrect results. Per-language walker implementations
(`walk_tree_java`, `walk_tree_python`, `walk_tree_js`) are explicit
future work — flagged in `PHASE_3_4_6_STATUS.md`. Callers should
restrict `extract_batch` to `"cplus"` / `"c"` / `"cpp"` until those
walkers land.

## Decision
- **Verification-only run rather than new development.** The plan was
  already at "all phases committed" when this session started; the
  fastest signal to the user is "everything green, here's the proof,"
  not another speculative change.
- **Did not address the 52 dead-code warnings.** They are tracked in
  `PHASE_3_4_6_STATUS.md` as deliberate retention of helper fns for
  future use; touching them now would risk breaking parity with
  Python reference fixtures.
- **Did not port per-language walkers.** Out of scope for verification
  and explicitly deferred to the dedicated multi-language plan
  (`plans/260731-1700-multi-language-rust-extraction/`) which carries
  the tiered 4-tier roadmap for trait refactor + walker ports.

## References
- plan: `./plans/260731-1030-rust-extraction-layer/plan.md`
- status: `./plans/260731-1030-rust-extraction-layer/PHASE_1_2_5_STATUS.md`
- status: `./plans/260731-1030-rust-extraction-layer/PHASE_3_4_6_STATUS.md`
- related plan (multi-language walker ports, future work):
  `./plans/260731-1700-multi-language-rust-extraction/plan.md`
- extension entry: `rust-analyzer-core/src/lib.rs:197-210`
- Python fallback wrapper: `code-tiny/tools/cplus/_rust_fallback.py:1-50`
- parity tests: `rust-analyzer-core/tests/test_rust_parity.py`
- fixtures: `rust-analyzer-core/tests/fixtures/`
- prior log: `./docs/logs/2026-07-31-rust-extraction-phase-3-4-6.md`
