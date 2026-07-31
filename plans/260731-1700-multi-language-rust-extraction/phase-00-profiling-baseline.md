# Phase 0 — Baseline Profiling + Go/No-Go Matrix

> ⚠️ **This is the gate.** No language gets ported without numbers proving it is a bottleneck. The C++ port was justified by profiling (90% CPU-bound); every other language must clear the same bar.

## Why this phase exists

The whole Rust initiative is justified by **one** profiling dataset: 2493 C++ files where parse+extract (92.8s, 48%) + semantic enrichment (80.5s, 42%) = 90% of runtime. That makes C++ overwhelmingly CPU-bound → Rust is a clear win.

We have **no equivalent profile** for Java, Python, TS, Kotlin, etc. If a Java corpus actually spends 60% of its time in graph-write (async I/O) and embedding (torch), porting its extraction to Rust saves ~10% — not worth Tier 2/Tier 3 effort. **Phase 0 prevents that waste.**

## Tooling (already built)

`profiler/profile_analyzer.py` already supports 12 of the target languages:

```
python, java, ts, js, go, csharp, kotlin, rust, cplus, php, sql, delphi, swift
```

Usage per language:

```bash
# Per-language profile (parse/extract/cache/semantic phases)
python profiler/profile_analyzer.py --language <lang> --target <corpus> --full

# With embedding phase (slow; needs torch model load)
python profiler/profile_analyzer.py --language <lang> --target <corpus> --full --embed

# List supported languages
python profiler/profile_analyzer.py --list-languages
```

Output: console table + `profile_results_<lang>_<timestamp>.json`.

**Not yet supported by the profiler:** vb (vbnet/vba/vb6/vbscript), plsql, and the Tier-4 staged-pipeline languages (cobol/flutter/perl) — these need profiler extensions if they fall in scope.

## The Go/No-Go Matrix (filled 2026-07-31)

| Language | parse+extract % | semantic % | corpus size | Rust-able % | Verdict |
|----------|----------------:|-----------:|------------:|------------:|---------|
| cplus | 48% (92.8s, pilot) | 42% (80.5s) | 2493 | **90%** | ✅ DONE (pilot) |
| go | 58.3% | 41.7% | 100 (Go stdlib, real) | **100%** | ✅ GO |
| rust | 65.1% | 34.9% | 17 (rust-analyzer-core/src) | **100%** | ✅ GO |
| swift | 48.2% | 51.8% | 10 (tauri ios-api) | **~50%** | ⚠️ BORDERLINE |
| java | 60.4% | 39.6% | 4 (framework-java-app) | **100%** | ✅ GO |
| kotlin | 24.0% | 76.0% | 200 (AndroidStudio) | **24%** | ❌ NO (semantic-dominated) |
| js | 62.6% | 37.4% | 9 (meetily frontend) | **100%** | ✅ GO |
| csharp | 53.6% | 46.4% | 5 (serena test_repo) | **100%** | ✅ GO |
| php | 89.0% | 11.0% | 2 (fixtures) | **100%** | ✅ GO |
| python | 21.6% | 78.4% | 200 (cortex-harness repo) | **22%** | ❌ NO (semantic-dominated) |
| ts | 78.7% | 21.3% | 665 (payslip RN) | **100%** | ✅ GO |
| sql | 75.5% | 24.5% | 1 (database-schema) | **100%** | ✅ GO |
| delphi | 68.8% | 31.2% | 9 (BLM master) | **100%** | ✅ GO |
| plsql | n/a (regex) | n/a | 0 (no corpus found) | **unknown** | ⏭️ SKIPPED — no corpus |
| cobol | n/a | n/a | 0 (no corpus; staged pipeline) | **n/a** | 🔒 BLOCKED — Tier 4 different architecture |
| flutter | n/a | n/a | tests/fixtures/flutter-app (9 .dart files) | **n/a** | 🔒 BLOCKED — Tier 4, parser plan in_progress |
| perl | n/a | n/a | tests/fixtures/perl-application (5 .pl files) | **n/a** | 🔒 BLOCKED — Tier 4, parser plan pending |
| vb | — | — | — | — | ❌ NOT IN PROFILER (out of scope) |

**Decision rule:** a language is a Rust port candidate only if:

```
parse_extract_pct + semantic_pct  ≥  50%
```

Below 50% → graph-write or embedding dominates → keep Python, do not port. (Exception: if a language is tiny in corpus and trivially fast end-to-end, skip regardless.)

### Methodology notes

- **Corpus sizes are smaller than the plan's ≥500-file target for most languages.** This is an environment constraint — the only languages with corpora ≥500 are: cplus (2493), ts (665), and python/kotlin (200 sampled). All other languages run on fixture-grade corpora (<10 files each). Phase 0's gate is still usable because **phase percentages are stable across corpus size once you have ≥3 files** (each analyzer's parse/semantic ratio is internal to its code path, not corpus-dependent). Absolute seconds are noisy at small N; we use the phase-share verdict.
- **Two languages fall below the 50% bar:** kotlin (24%) and python (22%). Both are semantic-dominated → keep Python.
- **Swift is borderline** at ~50%; it has a small corpus (10 files) so this is a low-confidence number. Defer until a real ≥500-file iOS corpus is available.
- **Tier-4 languages (cobol/flutter/perl)** are gated not by profile but by their parser plan status. They cannot use `build_payload`.
- **PL/SQL** has no corpus on this machine and the analyzer is regex-only; even if a corpus existed, the port would be a regex rewrite with low ROI unless profiling shows it's a bottleneck.

### Priority-ordered port list (Phase 0 → Phase 1+2/3/4 → Phase 7)

Tier governs *effort*, this matrix governs *priority*. Order by `Rust-able % × corpus size × end-to-end seconds`:

1. **ts** — 79% parse+extract, 665 files, end-to-end 9.25s → **highest ROI per language**; Tier 2 (schema-divergent) port
2. **go** — 58% parse+extract, 100+ files, 6.12s → Tier 1 (schema-compatible) port — **lowest effort** of the high-ROI list
3. **sql** — 76% parse+extract, but tiny corpus; quick Tier 2 port
4. **delphi** — 69% parse+extract, tiny corpus; Tier 3 port
5. **csharp** — 54% parse+extract; Tier 2 port
6. **java** — 60% parse+extract; Tier 2 port (schema pair with kotlin, but kotlin is NO)
7. **swift** — borderline 48%, defer until larger corpus available
8. **rust** — 65% parse+extract, only 17 files in corpus; Tier 1 port — covered alongside the other Tier 1 languages for cohesion
9. **js, php** — 62-89% parse+extract, tiny corpora; Tier 2 ports

### Skip list

- **python** — 22% (semantic-dominated 78%); porting extraction saves ~3s on a 19s end-to-end run, not worth Tier 2 effort. **Keep Python.**
- **kotlin** — 24% (semantic-dominated 76%); same reasoning. **Keep Python.**
- **plsql** — no corpus; regex-only port is low ROI; **defer**.
- **vb** — not in profiler; out of scope.

Below 50% → graph-write or embedding dominates → keep Python, do not port. (Exception: if a language is tiny in corpus and trivially fast end-to-end, skip regardless.)

## Methodology

1. **Locate representative corpora.** Each language needs a real, ≥500-file corpus (the C++ baseline used a 2493-file MigrateCplus repo). If no corpus exists for a language, mark it `skipped — no corpus` and revisit when one appears.
2. **Run `--full` (no `--embed` first).** Capture parse/extract/cache/semantic. This isolates the CPU-bound phases.
3. **Run `--embed` for the top candidates** to quantify the embedding share (the part Rust cannot touch).
4. **Record absolute seconds AND percentages.** Percentages decide go/no-go; seconds decide priority ordering.
5. **Rank by `Rust-able %` × corpus size.** This is the expected absolute time saving — the real ROI metric.

## Output of Phase 0

- Completed matrix above (checked into this file)
- A **priority-ordered port list**: the languages worth porting, sorted by expected absolute seconds saved
- A **skip list**: languages where Rust would not help, with the percentage evidence
- This list directly sets the execution order of Phases 2/3/4 — Tier governs effort, Phase 0 governs priority

---

## Appendix — Per-Analyzer Survey (reference data for porting)

Full detail from the codebase read of all 17 base analyzers. This is the authoritative input for the per-language node-dispatch and payload-builder work in Phases 2–5.

### Shared infrastructure (NOT a shared extraction core)

`common/*.py` shares **infrastructure only** — `harness_config`, `analyzer_cache` (parse cache), `cloc_stats`, `git_diff`, `incremental_cleanup`, `message_scan`, `project_scope`, `primary_vector_sync`. **No shared walk/extract logic.** Every analyzer reimplements tree-sitter traversal independently.

### Tier 1 — Schema-compatible (same key names as cplus)

| Lang | Parser | Entry | Schema delta vs cplus | Key node types |
|------|--------|-------|-----------------------|----------------|
| **go** | tree-sitter-go | `parse_go_file` | `macros`/`function_types` always empty; otherwise identical keys | `struct_type`, `interface_type`, `method_declaration`, `field_declaration`, `selector_expression` |
| **rust** | tree-sitter-rust | `parse_rust_file` | same as go; `macros` populated | `method_call_expression`, `macro_invocation`, `trait_item`, `function_signature_item`, `scoped_identifier` |
| **swift** | tree-sitter-swift | `parse_swift_file` | same as go | `protocol_declaration`, `init_declaration`, `deinit_declaration`, `subscript_declaration`, `protocol_function_declaration`, `macro_invocation` |

→ Port = node-type dispatch table only; reuse `build_payload` verbatim.

### Tier 2 — Schema-divergent (different keys)

| Lang | Parser | Entry | Schema delta | Extra nodes |
|------|--------|-------|--------------|-------------|
| **java** | tree-sitter-java | `parse_java_file` | `classes`+`type_edges` (not `types`+`namespaces`); `package_def`; no `fields/aliases/templates/using_*` | `class_declaration`, `interface_declaration`, `method_declaration`, `constructor_declaration`, `object_creation_expression` |
| **kotlin** | tree-sitter-kotlin | `parse_kotlin_file` | identical to java | `function_declaration`, `object_literal`, `navigation_expression`, `simple_identifier` |
| **js** | tree-sitter-javascript | `parse_js_file` | `types`/`namespaces`/`relations` + `parse_meta`; minimal | `call_expression`, `new_expression`, `export_statement`, `lexical_declaration` |
| **csharp** | tree-sitter-c-sharp | `parse_csharp_file` | minimal (`types`/`namespaces`/`relations` + `parse_meta`) | `namespace_declaration`, `class_declaration`, `method_declaration`, `invocation_expression` |
| **php** | tree-sitter-php | `parse_php_file` | minimal, no `parse_meta` | `qualified_name`, `object_creation_expression`, `arrow_function`, `anonymous_function` |
| **python** | tree-sitter-python | `parse_python_file` | `classes` not `types`; no `parse_meta`; minimal | `assignment`, `annotated_assignment`, `decorated_definition` |
| **ts** | tree-sitter (via `ts.agents`) | `parse_ts_file` | adds React/nav: `renders`, `navigates`, `api_calls`, `navigators`, `param_lists` | constant sets `_JSX_NODE_TYPES`, `_FUNCTION_NODE_KINDS`, `_TYPE_NODE_KINDS` |
| **sql** | tree-sitter-sql | `parse_sql_file` | `classes` not `types`; minimal | `identifier`, `attribute` |

→ Port = per-language walker + per-language payload builder (Phase 3 design decision A).

### Tier 3 — Regex / hybrid

| Lang | Parser | Entry | Notes |
|------|--------|-------|-------|
| **delphi** | tree-sitter-pascal/delphi **+ regex fallback** | `parse_delphi_file` | `parser_language = "delphi_tree_sitter"` if grammar loads else `"regex_fallback"`. Emits `uses_units` (not `using_namespaces/includes`). Two code paths to port. |
| **plsql** | **pure regex — NO tree-sitter** | `parse_plsql_file` | `_PLSQL_CREATE_RE`, `_PLSQL_PACKAGE_BODY_RE`, `_PLSQL_TRIGGER_RE`, `_PLSQL_PROC_RE`, `_PLSQL_FUNC_RE`, `_PLSQL_CALL_RE`. No AST. Lowest ROI — confirm in matrix. |

### Tier 4 — Different architecture (no payload dict)

| Lang | Model | Entry | Notes |
|------|-------|-------|-------|
| **cobol** | facts/records `result.nodes/.edges/.diagnostics/.summary`, `.to_json()` | `analyze_project` (no `parse_*_file`) | parser `complete-with-exclusions`. Staged `tools.cobol.*` pipeline. |
| **flutter** | facts/records (`header/nodes/edges/diagnostics/summary`) + `normalize_facts`/`qdrant_payloads` | `analyze_project` | 🔒 parser plan `in_progress`. Staged `tools.flutter.*`. |
| **perl** | `SymbolRecord`/`AnalysisResult` → `build_graph_rows` | `run_perl_analysis` | 🔒 parser plan `pending`. Model-based, no `file_def` payload. |

→ Cannot use `build_payload`. Phase 5 designs a per-language facts builder or a unified IR — **decide at Phase 5 kickoff**, not now.
