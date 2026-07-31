# Rust Extraction Layer — Phases 3 / 4 / 6 — 2026-07-31

## Context
The Rust Extraction Layer plan (`plans/260731-1030-rust-extraction-layer/`)
moves the CPU-bound Python analyzer pipeline (AST walk + dataclass build +
semantic enrichment) into a Rust PyO3 extension, leaving embedding and
graph-write in Python. Phases 1, 2, and 5 shipped previously (commit
`3bcfc79` — `cortex_extract.extract_cplus[_batch]` + Python fallback).
This commit closes the architectural loop by adding the three remaining
modules that were scaffolded but not implemented: call resolution
(Phase 3), semantic enrichment (Phase 4), and multi-language grammar
dispatch (Phase 6). The C++ walker remains the single source of truth
for symbol extraction — the new modules operate on its output.

## Change
**New modules:**
- `rust-analyzer-core/src/resolver.rs` — 8-hash `CallIndex`
  (`by_name`, `by_name_arity`, `by_scope_name`, `by_scope_name_arity`,
  `by_file_name`, `by_file_name_arity`, `by_qualified`,
  `by_qualified_arity`). `resolve_calls` walks the Python `_resolve_calls`
  priority order: qualified (120/110) > file-local (115/105) > scope-chain
  (100/90 minus depth) > global (70/50), ties broken on (score ASC,
  distance ASC, qualified_name ASC). `resolve_batch` hydrates dicts into
  Rust structs, runs the index build once, then `par_iter_mut` over
  payloads, writes `callee_id` back into the Python dicts in place.
- `rust-analyzer-core/src/semantic.rs` — 108 verb patterns + 6 body
  patterns pre-compiled via `once_cell::Lazy<Regex>`. Naming signal =
  verb prefix match (with `onXxx` event-handler bypass and short-name
  fallback to UNKNOWN@0.15). Body signal = dominant intent by per-intent
  match count. Confidence = `0.40·naming + 0.20·type + 0.30·usage + 0.10·body`
  + 0.05 exported bonus, rounded to 3 decimals. `enrich_corpus_py`
  mutates each function dict's `intent`, `signals`, `side_effect`,
  `doc_confidence`, `summary`, `inferred_doc` in place.
- `rust-analyzer-core/src/grammar.rs` — `Grammar` trait + 5
  implementations (`CppGrammar`, `CGrammar`, `JavaGrammar`,
  `PythonGrammar`, `JsGrammar`). `by_id` aliases (`cplus|cpp|c++` → `cpp`;
  `python|py` → `python`; `javascript|js|ts` → `javascript`). `by_path`
  walks the registry in declaration order so ambiguous `.h` defaults to
  C++ (the common case). `parse_root_kind` runs a real tree-sitter parse
  on each grammar and returns the root node kind as a wiring probe.

**Modified files:**
- `rust-analyzer-core/src/lib.rs:167-195` — registered `resolve_batch`,
  `enrich_corpus`, `supported_languages`, `detect_language`,
  `parse_root_kind`; added `mod grammar; mod resolver; mod semantic;`.
- `rust-analyzer-core/Cargo.toml:17-19` — added `tree-sitter-java`,
  `tree-sitter-python`, `tree-sitter-javascript` (existing
  `tree-sitter-cpp`/`tree-sitter-c` retained).
- `rust-analyzer-core/tests/test_rust_parity.py:156-258` — 5 new
  pytest cases: `test_resolve_batch_sets_callee_id`,
  `test_enrich_corpus_sets_intent`,
  `test_supported_languages_lists_grammars`,
  `test_detect_language_dispatches_by_extension`,
  `test_parse_root_kind_for_each_language`.

**Verification:**
```
cargo test --release --lib
  resolver::tests  → 5 passed
  semantic::tests  → 8 passed
  grammar::tests   → 8 passed
  21 passed; 0 failed

PYTHONPATH=. python tests/test_rust_parity.py
  10 passed in 0.05s (5 existing + 5 new)
```

## Impact
**Risk level:** medium. The resolver and semantic engine are the two
largest parity surfaces in the Python implementation (scoring priority
order, regex sets, confidence weighting); any drift against the
reference Python produces wrong `callee_id` links and wrong intents.
Mitigations:
- Differential parity tests compare Rust output against Python
  reference fixtures (`tests/test_rust_parity.py`).
- The resolver's `consider()` helper preserves the Python tie-break
  semantics (`score ASC, distance ASC, qualified_name ASC`).
- `enrich_corpus_py` only writes `summary` when the developer comment
  is empty — preserving the Python overwrite rule.

The grammar registry is low risk because it is wiring-only (no
per-language walker yet); Java/Python/JS still flow through the C++
walker as a placeholder. Per-language walker implementations are
explicit future work documented in
`plans/260731-1030-rust-extraction-layer/PHASE_3_4_6_STATUS.md`.

## Decision
- **Direct port of priority order in `resolver.rs` rather than a
  re-designed hybrid.** The Python algorithm has empirically-derived
  scoring constants (120/110/115/105/100/90/70/50) that downstream
  consumers depend on; re-tuning would silently change link targets.
  `consider()` was kept as a tiny helper to make the ASC tie-break
  readable.
- **Pre-compiled regexes via `once_cell::Lazy` rather than per-call
  `Regex::new`.** Python's `re` module caches compiled patterns; the
  Rust port uses static `Lazy` so the same pre-compilation happens
  once per process and per-function scoring is allocation-free for the
  non-match case.
- **Grammar trait + 5-language registry rather than per-language
  functions.** Allows incremental walker porting (one
  `walk_tree_<lang>` at a time) without breaking the registration
  surface. The C++ walker stays as the default body for unported
  languages, which is safe because callers only dispatch to languages
  they have registered for.
- **Aliases in `by_id` (`cplus`, `c++`, `py`, `ts`) rather than only
  canonical IDs.** Preserves the existing Python
  `parse_c_family_file(is_cpp=True)` callers and the unified
  MCP `language` parameter shape (`"cplus"`, `"python"`, `"ts"`,
  …) without forcing a rename.
- **`.h` resolves to C++ in `by_path`.** Consistent with the existing
  C++ pilot (`parser::default_is_cpp`); walking Cpp first in the
  registry means the C++ grammar wins before plain C for ambiguous
  extensions.

**Alternatives considered:**
- *Move call resolution back to Python.* Rejected — defeats the
  goal of single-GIL-pass dispatch and would require re-importing
  thousands of payloads into Python state.
- *Compile regexes lazily on first use.* Rejected — adds branch
  prediction cost in the hot path; `once_cell::Lazy` is the same
  idiom the rest of the Rust ecosystem uses.
- *Embed a per-language walker inside `grammar.rs`.* Rejected —
  blurs the boundary between grammar selection and extraction
  logic; separating them keeps each language's walker replaceable
  independently.

## References
- plan: `./plans/260731-1030-rust-extraction-layer/plan.md`
- status (this commit): `./plans/260731-1030-rust-extraction-layer/PHASE_3_4_6_STATUS.md`
- status (prior): `./plans/260731-1030-rust-extraction-layer/PHASE_1_2_5_STATUS.md`
- predecessor commit: `3bcfc79` — `feat(rust): implement cortex_extract
  PyO3 extension for Phase 1, 2, 5`
- source files:
  - `rust-analyzer-core/src/resolver.rs:1-557`
  - `rust-analyzer-core/src/semantic.rs:1-619`
  - `rust-analyzer-core/src/grammar.rs:1-247`
  - `rust-analyzer-core/src/lib.rs:167-195`
  - `rust-analyzer-core/Cargo.toml:17-19`
  - `rust-analyzer-core/tests/test_rust_parity.py:156-258`
