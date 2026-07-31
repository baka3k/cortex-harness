# Multi-Language Rust Extraction — Phases 0 + 1 (Profile gate + trait refactor) — 2026-07-31

## Context

Plan `260731-1700-multi-language-rust-extraction` aims to extend the C++
Rust extraction pilot (Phases 1–6 committed before this session) to all
17 base analyzers via a tiered `LanguageProfile` dispatch architecture.
The plan's headline caveat: **Phase 0 is a gate** — no language gets
ported without profile evidence that parse+extract+semantic ≥ 50% of
end-to-end runtime. Without that, a multi-day Tier-2/Tier-3 port might
save only a few percent of total runtime.

The `hi-craft --full` invocation landed here to execute Phases 0 and 1
of the plan: fill the go/no-go matrix with real numbers, and refactor
the C++-specific walker into a `LanguageProfile` dispatch table so
adding a language = writing a profile, not copying the walker.

## Phase 0 — Profile gate (filled)

Ran `profiler/profile_analyzer.py` against 12 supported languages on
whatever corpora were available locally:

| Language | parse+extract | semantic | corpus | Rust-able % | Verdict |
|----------|--------------:|---------:|-------:|------------:|---------|
| cplus (pilot) | 48% (92.8s) | 42% (80.5s) | 2493 | **90%** | ✅ DONE |
| go | 58.3% | 41.7% | 100 (Go stdlib) | **100%** | ✅ GO |
| rust | 65.1% | 34.9% | 17 (rust-analyzer-core) | **100%** | ✅ GO |
| swift | 48.2% | 51.8% | 10 (tauri ios-api) | **~50%** | ⚠️ BORDERLINE |
| java | 60.4% | 39.6% | 4 (fixtures) | **100%** | ✅ GO |
| kotlin | 24.0% | 76.0% | 200 (AndroidStudio) | **24%** | ❌ NO (semantic-dominated) |
| js | 62.6% | 37.4% | 9 (meetily frontend) | **100%** | ✅ GO |
| csharp | 53.6% | 46.4% | 5 (serena test_repo) | **100%** | ✅ GO |
| php | 89.0% | 11.0% | 2 (fixtures) | **100%** | ✅ GO |
| python | 21.6% | 78.4% | 200 (cortex-harness) | **22%** | ❌ NO (semantic-dominated) |
| ts | 78.7% | 21.3% | 665 (payslip RN) | **100%** | ✅ GO |
| sql | 75.5% | 24.5% | 1 (fixture) | **100%** | ✅ GO |
| delphi | 68.8% | 31.2% | 9 (BLM) | **100%** | ✅ GO |
| plsql | n/a | n/a | 0 | **unknown** | ⏭️ SKIPPED — no corpus |

**Skip list:** python and kotlin are semantic-dominated; **keep Python**
for both. PL/SQL has no corpus on this machine and the analyzer is
regex-only — defer until a real corpus appears.

**Priority order** (by `Rust-able % × corpus × end-to-end seconds`):
1. **ts** (highest ROI per language) → Phase 3
2. **go** (lowest effort — Tier 1, schema-compatible) → Phase 2
3. **sql** / **delphi** (small corpora, quick Tier-2/3 ports)
4. **csharp**, **java** → Phase 3
5. **swift** borderline; defer until ≥500-file corpus
6. **rust** alongside Tier 1 for cohesion
7. **js, php** → Phase 3

**Caveat:** most languages ran on <10-file fixture corpora. Phase
percentages are stable across corpus size (they're internal to each
analyzer), but absolute seconds are noisy at small N. The verdict uses
phase-share only.

**Profiler fixes shipped with this Phase 0 work** (in
`profiler/profile_analyzer.py`):
- Made `parse_bytes_fn` resolution lazy so analyzers that lack
  `_parse_file` (java/kotlin/ts) don't crash on import.
- Fixed ts's `get_parser_fn` to point at `_get_ts_parser` (not the
  generic `_get_parser`).
- Made the parse-only phase skip when `get_parser_fn` requires args
  (ts needs a `language_name`).

## Phase 1 — `LanguageProfile` trait refactor

Refactored `rust-analyzer-core/src/walker.rs` from a C++-specific match
into a generic iterative DFS driver keyed by a new
`profile::LanguageProfile` trait:

```rust
pub trait LanguageProfile: Sync + Send {
    fn id(&self) -> &'static str;
    fn parser_language(&self) -> &'static str;
    fn dispatch<'a>(&self, node_kind: &str) -> Option<NodeHandler<'a>>;
}
```

- `NodeHandler` is a `fn` pointer with an explicit lifetime, so the
  lifetime unifies cleanly with the `Frame<'tree>` carried in the work
  queue (the original HRTB approach failed to compile due to variance).
- `CppProfile` is the worked example — it extracts every arm of the
  original `process_frame` match (`handle_using`, `handle_type_specifier`,
  `handle_function_definition`, etc.) verbatim.
- `walk_with_profile(profile, root, source, rel)` drives the DFS;
  unknown node kinds fall through to "push children" exactly like the
  original walker's default arm.
- `walker::new_walk_context()` and `walker::finalize_output()` are
  extracted so the profile path reuses them; `finalize_output` now
  takes `parser_language` so the meta-string comes from the profile,
  not a hardcoded `"cpp"`.
- `push_children`, `Frame` (and its fields), `declaration_type_text`,
  and `finalize_output` are now `pub(crate)` so the profile module can
  use them.

**Validation (regression-free):**

- `cargo test --lib --release` → **25 passed** (was 21; +4 profile
  tests: `cpp_profile_covers_pilot_node_kinds`,
  `cpp_profile_ids_match_grammar_registry`,
  `walk_with_profile_emits_parser_language`,
  `go_profile_scaffold_dispatches_empty`).
- `python tests/test_rust_parity.py` → **10 passed** (no C++ regression;
  `simple_class`, `template_function`, `namespace_nested`,
  `macro_heavy`, `batch_parallel`, `resolve_batch_sets_callee_id`,
  `enrich_corpus_sets_intent`, `supported_languages_lists_grammars`,
  `detect_language_dispatches_by_extension`,
  `parse_root_kind_for_each_language`).
- The Phase 6 multi-language registry still resolves
  `['cpp','c','java','python','javascript']`.

## Phase 2 — Tier 1 scaffold (partial)

Added a `GoProfile` scaffold in `profile.rs` that registers `id="go"`,
`parser_language="go"`, and an empty dispatch table. This is a
**placeholder** — full Phase 2 deliverable requires:

1. Add `tree-sitter-go = "0.23"`, `tree-sitter-rust = "0.23"`,
   `tree-sitter-swift = "0.23"` to `Cargo.toml`.
2. Add `GoGrammar`, `RustGrammar`, `SwiftGrammar` impls in
   `grammar.rs` returning those languages.
3. Populate the dispatch tables for each language's node types.
4. Wire `extract_batch(language="go" | "rust" | "swift", ...)` to pick
   the right profile.

The trait design is proven to accept non-C++ languages (GoProfile
compiles, registers, and `walk_with_profile` routes correctly).

**Decision:** did **not** attempt the full Tier 1 implementation in
this session. The per-language walker logic for go/rust/swift is
multi-day work (each grammar has its own quirks — go has `method_declaration`
on receivers, swift has `protocol_function_declaration` with
ownership semantics). The Phase 0 priority ordering and the Phase 1
trait scaffold are the prerequisites; the actual Phase 2 work belongs
in its own focused session.

## Files changed

- `plans/260731-1700-multi-language-rust-extraction/phase-00-profiling-baseline.md`
  — filled go/no-go matrix; methodology + priority list
- `profiler/profile_analyzer.py` — lazy `parse_bytes_fn`, fix ts
  `get_parser_fn`, skip parse-only phase when parser needs args
- `rust-analyzer-core/src/lib.rs` — `mod profile;`
- `rust-analyzer-core/src/walker.rs` — `pub(crate)` helpers,
  `new_walk_context`, `finalize_output(parser_language)`, `pub Frame`
  fields, `walk_tree` retained for pilot parity tests
- `rust-analyzer-core/src/profile.rs` — new: `LanguageProfile` trait,
  `NodeHandler`, `walk_with_profile`, `CppProfile` (worked example),
  `GoProfile` (Phase 2 scaffold), 4 tests
- `rust-analyzer-core/python/cortex_extract/__init__.py` — created so
  maturin's `python-source` setting resolves
- `profile_results_*.json`, `profile_outliers_*.json` — captured
  evidence (12 languages × 2 phase passes)

## Impact

**Risk level:** medium. The Phase 1 refactor is structural: it changes
the walker contract from a C++ match into a generic dispatch table.
Verified by 25 cargo tests + 10 Python parity tests (C++ output is
byte-identical). The Phase 0 matrix prevents the worst outcome
(porting a non-bottleneck language).

**Remaining work** (out of scope this session):
- Phases 2 (Tier 1 ports), 3 (Tier 2 ports), 4 (Tier 3 ports),
  5 (Tier 4 [BLOCKED]), 6 (semantic), 7 (integration/CI/wheels) —
  each is multi-day work and blocked on parser plans for Tier 4.
- The Phase 1 refactor + Phase 0 gate are the foundational
  prerequisites for those phases.

## References

- plan: `./plans/260731-1700-multi-language-rust-extraction/plan.md`
- gate data: `./plans/260731-1700-multi-language-rust-extraction/phase-00-profiling-baseline.md`
- profile trait: `rust-analyzer-core/src/profile.rs`
- walker: `rust-analyzer-core/src/walker.rs`
- prior log: `./docs/logs/2026-07-31-rust-extraction-verification.md`
- pilot log: `./docs/logs/2026-07-31-rust-extraction-phase-3-4-6.md`