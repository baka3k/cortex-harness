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

## The Go/No-Go Matrix (to be filled)

| Language | parse+extract % | semantic % | graph-write % | embed % | Rust-able % | Verdict |
|----------|----------------:|-----------:|--------------:|--------:|------------:|---------|
| cplus | 48% (92.8s) | 42% (80.5s) | ~6% | ~? | **90%** | ✅ DONE (pilot) |
| go | ? | ? | ? | ? | ? | ? |
| rust | ? | ? | ? | ? | ? | ? |
| swift | ? | ? | ? | ? | ? | ? |
| java | ? | ? | ? | ? | ? | ? |
| kotlin | ? | ? | ? | ? | ? | ? |
| js | ? | ? | ? | ? | ? | ? |
| csharp | ? | ? | ? | ? | ? | ? |
| php | ? | ? | ? | ? | ? | ? |
| python | ? | ? | ? | ? | ? | ? |
| ts | ? | ? | ? | ? | ? | ? |
| sql | ? | ? | ? | ? | ? | ? |
| delphi | ? | ? | ? | ? | ? | ? |
| plsql | n/a (regex) | ? | ? | ? | ? | ? |

**Decision rule:** a language is a Rust port candidate only if:

```
parse_extract_pct + semantic_pct  ≥  50%
```

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
