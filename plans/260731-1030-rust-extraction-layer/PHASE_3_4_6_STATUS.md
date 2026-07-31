# Rust Extraction Layer — Phase 3/4/6 Status Report

## Phases Delivered (this commit)

### Phase 1 — Rust crate skeleton + cplus pilot ✅
_(unchanged from PHASE_1_2_5_STATUS.md)_

### Phase 2 — Multithreaded batch extraction ✅
_(unchanged from PHASE_1_2_5_STATUS.md)_

### Phase 3 — Call resolution + relation building in Rust ✅ (NEW)
- `rust-analyzer-core/src/resolver.rs`
- 8-hash `CallIndex` (by_name, by_name_arity, by_scope_name, by_scope_name_arity,
  by_file_name, by_file_name_arity, by_qualified, by_qualified_arity)
- Scoring algorithm mirrors `_resolve_calls` priority order:
  qualified (120/110) > file-local (115/105) > scope-chain
  (100/90 minus depth) > global (70/50)
- PyO3 entry point `cortex_extract.resolve_batch(payloads)` mutates
  `callee_id` in each call dict in place
- Tests: `scope_chain`, `closest_scope_wins_when_only_scope_chain_matches`,
  `closest_scope_wins_when_no_file_local_tie`, `file_local_match_wins_over_global`,
  `qualified_match_wins_over_global`

### Phase 4 — Semantic enrichment in Rust ✅ (NEW)
- `rust-analyzer-core/src/semantic.rs`
- Pre-compiled verb patterns (108 entries) via `once_cell::Lazy<Regex>`
- Pre-compiled body patterns (6 intents × ~8 regexes each)
- Naming signal (verb prefix matching + onXxx handler prefix + short-name fallback)
- Body signal (per-intent match counting, dominant intent wins)
- Confidence score: 0.40·naming + 0.20·type + 0.30·usage + 0.10·body
  with +0.05 exported bonus (matches `ConfidenceScorer.score_dict`)
- Subject extraction (verb strip → camelCase split → article drop)
- Summary generation (templates per intent + arity/async enrichments)
- PyO3 entry point `cortex_extract.enrich_corpus(functions, calls)` mutates
  `intent`, `signals`, `side_effect`, `doc_confidence`, `summary`,
  `inferred_doc` in place
- Tests: `naming_signal_getter`, `naming_signal_constructor`,
  `naming_signal_unknown_short`, `body_signal_io_read`,
  `body_signal_validation`, `extract_subject_get_user`,
  `analyze_returns_summary`, `analyze_preserves_developer_comment`

### Phase 5 — Python integration + fallback ✅
_(unchanged from PHASE_1_2_5_STATUS.md)_

### Phase 6 — Multi-language dispatch ✅ (NEW)
- `rust-analyzer-core/src/grammar.rs`
- New dependencies: `tree-sitter-java`, `tree-sitter-python`,
  `tree-sitter-javascript` (in addition to existing c/cpp)
- `Grammar` trait + per-language implementations (Cpp, C, Java, Python, Js)
- `registry()` returns all five grammars
- `by_id(id)` resolves aliases: `cplus`, `cpp`, `c++` → `cpp`;
  `python`, `py` → `python`; `javascript`, `js`, `ts` → `javascript`
- `by_path(path)` auto-detects by extension; Cpp walks first so
  ambiguous `.h` defaults to C++ (the common case)
- PyO3 entry points:
  * `supported_languages()` — list all registered language IDs
  * `detect_language(path)` — resolve grammar for a path
  * `parse_root_kind(language, source)` — parse + return tree-sitter
    root kind as a grammar-wiring sanity probe

## Verification

```
cargo test --release --lib
  resolver::tests  → 5 passed
  semantic::tests  → 8 passed
  grammar::tests   → 8 passed
  (existing tests) → 0 regressions
  21 passed; 0 failed

PYTHONPATH=. python tests/test_rust_parity.py
  test_simple_class PASSED
  test_template_function PASSED
  test_namespace_nested PASSED
  test_macro_heavy PASSED
  test_batch_parallel PASSED
  test_resolve_batch_sets_callee_id PASSED    [Phase 3]
  test_enrich_corpus_sets_intent PASSED       [Phase 4]
  test_supported_languages_lists_grammars PASSED
  test_detect_language_dispatches_by_extension PASSED
  test_parse_root_kind_for_each_language PASSED
  10 passed in 0.05s
```

## Notes

- Phases 3/4/6 complete the architecture without rewriting any
  per-language walker — the C++ walker remains the single source of
  truth for symbol extraction, and the new modules operate on its
  output.
- Per-language walker implementations for Java/Python/JS remain future
  work — Phase 6 delivers the dispatch skeleton (grammar loading,
  registry, extension-based detection) so each language can be ported
  incrementally by adding a `walk_tree_<lang>` function.
- 52 cargo warnings (mostly pre-existing dead-code in helper fns left
  for future use; new modules contribute a small number of `unused`
  imports now resolved).
- `cortex_extract.so` must be rebuilt and copied to
  `rust-analyzer-core/cortex_extract.so` after `cargo build --release`
  (existing workflow in PHASE_1_2_5_STATUS.md).