---
title: "Multi-Language Rust Extraction Layer (Full 4-Tier)"
status: in-progress
created: 2026-07-31
mode: hi-plan
source: extends 260731-1030-rust-extraction-layer (cplus pilot) to all 17 base analyzers
target: code-tiny/tools/ (all language analyzers) + rust-analyzer-core/
scope: Port tree-sitter AST walk + extraction + semantic enrichment for every language analyzer to Rust; tiered by schema parity. Graph-write + embedding stay Python.
blockedBy:
  [
    260714-1603-flutter-analyzer-parser,
    260715-1629-perl-analyzer-parser,
  ]
relatedPlans:
  [
    260731-1030-rust-extraction-layer,
    260731-1400-graph-write-optimization,
    260714-1702-cobol-analyzer-parser,
    260713-1638-framework-parser-integration,
    260715-2011-aspnet-roslyn-analyzers,
    260731-1800-rust-extraction-tier1-rust-lang,
    260731-1830-rust-extraction-tier1-swift,
    260731-1900-rust-extraction-tier2-ts,
    260731-2000-rust-extraction-tier2-java-js-csharp-php-sql,
    260731-2100-rust-extraction-tier3-delphi-plsql,
  ]
supersedesPhase: "260731-1030-rust-extraction-layer / Phase 6 (stub)"
---

# Multi-Language Rust Extraction Layer (Full 4-Tier)

## Overview

Plan `260731-1030-rust-extraction-layer` proved the Rust extraction concept for **C++ only**. Its Phase 6 ("expand to java, ts, python…") is a **stub** — `grammar.rs` loads 5 grammars but the walker is C++-specific and the comment admits java/python/js *"use the same C++ walker as a placeholder."*

This plan turns Phase 6 into a real, full-scope roadmap covering **all 17 base analyzers**. The headline finding from the codebase survey: **there is no shared extraction core and no shared payload schema.** Each analyzer reimplements its own tree-sitter traversal and emits a *different* dict shape. So "multi-language" is not one parameterized walker — it is N per-language ports, tiered by how close each language is to the C++ contract.

```
rust-analyzer-core/
  └── today: ONE walker (C++ node dispatch) + ONE payload schema (cplus)
  └── target: LanguageProfile per language
        ├── node-type → extractor dispatch table   (per language)
        ├── payload schema builder                  (per language or shared per tier)
        └── semantic signal rules                   (per language)
      + shared: iterative DFS core, rayon batch, PyO3 crossing, regex engine
```

**Hard boundary (unchanged from the pilot plan):** Rust = extraction + semantic only. Graph-write (Neo4j/FalkorDB async) and embedding (torch/transformers) stay Python — they are I/O- and ML-bound, not CPU-bound.

## Why This Must Be Tiered (the survey)

A full read of every base analyzer (`code-tiny/tools/*/`) produced this reality. **`common/*.py` shares infrastructure only (cache, config, git-diff) — never the walk/extract logic.** Three distinct output architectures coexist:

| Tier | Languages | Payload schema vs cplus | Parser tech | Rust port effort |
|------|-----------|-------------------------|-------------|------------------|
| **1 — Schema-compatible** | go, rust, swift | **Same key names** (`types`, `fields`, `aliases`, `templates`, `using_namespaces`, `includes`, `macros`); `macros`/`function_types` may be empty | tree-sitter, standalone `parse_*_file` | **Low** — per-language node-type table only; reuse `build_payload` |
| **2 — Schema-divergent** | java, kotlin (pair), js, csharp, php, python, ts, sql | **Lệch key**: `classes` not `types`; missing `parse_meta`; ts adds `renders`/`navigates`/`api_calls`; python minimal | tree-sitter, standalone `parse_*_file` | **Medium** — per-language walker + per-language payload builder (schema adapter) |
| **3 — Regex / hybrid** | delphi, plsql | Minimal schema (`functions/calls/classes/namespaces/relations`) | delphi = tree-sitter **+ regex fallback**; plsql = **pure regex, no AST** | **High** — must port regex engine; plsql has no tree to walk |
| **4 — Different architecture** | cobol, flutter, perl | **No payload dict** — emit facts/records model (`header/nodes/edges/diagnostics/summary`) or `SymbolRecord`→`build_graph_rows` | staged pipeline; no `parse_*_file` | **Highest** — new Rust contract; **blocked** on each language's own parser plan |

**Profiling caveat (critical):** The C++ port was justified by profiling — 92.8s parse+extract + 80.5s semantic = 90% of runtime on 2493 files. **We have NO equivalent profile for Java/Python/TS/etc.** `profile_analyzer.py` already supports 12 of these languages (python, java, ts, js, go, csharp, kotlin, rust, cplus, php, sql, delphi, swift), but no saved results exist outside C++. **Porting a language that isn't a bottleneck is pure waste.** → Phase 0 is a mandatory gate.

> 📖 Full per-analyzer node-type + schema detail: see [phase-00-profiling-baseline.md](phase-00-profiling-baseline.md) appendix.

## The Data-Driven Gate (Phase 0)

Before any port, run the existing profiler on a representative corpus for each language and record `parse%`, `extract%`, `semantic%`, `embed%`. Output is a **go/no-go matrix**:

```
Language  | parse+extract% | semantic% | graph% | embed% | Rust-able% | VERDICT
----------|----------------|-----------|--------|--------|------------|--------
cplus     | 48% (92.8s)    | 42% (80s) | 6%     | ~?     | 90%        | ✅ DONE (pilot)
java      | ?              | ?         | ?      | ?      | ?          | ?
ts        | ?              | ?         | ?      | ?      | ?          | ?
...
```

**Decision rule:** a language is a Rust port candidate only if `parse+extract + semantic ≥ 50%` of its end-to-end runtime on a real corpus. Otherwise it stays Python (not worth the porting effort vs. the graph/embedding that dominate).

This prevents the single biggest risk: spending Tier-2/Tier-3 effort on a language whose bottleneck is actually graph-write or embedding.

## Phase Breakdown

### Phase 0 — Baseline profiling + go/no-go matrix  ⚠️ GATE

**Goal:** Establish per-language runtime breakdown; decide which languages are worth porting at all.

**Deliverables:**
- Run `profile_analyzer.py --language <lang> --target <corpus> --full` for all 12 supported languages
- Capture `parse%`, `extract%`, `semantic%`, `graph%`, `embed%` per language in `phase-00-profiling-baseline.md`
- Produce the **go/no-go matrix** (table above)
- Identify representative corpora for each language (may need to locate or synthesize)
- Extend profiler for vb/plsql if those fall in scope (currently unsupported)

**Validation:** Each language row has numbers from a real (≥500 file) corpus or is explicitly marked "skipped — no corpus".

### Phase 1 — LanguageProfile trait refactor (unblock Tier 1)

**Goal:** Refactor the C++-specific walker into a dispatch-table architecture so adding a language = adding a table, not copying the walker.

**Deliverables:**
- `LanguageProfile` struct: `{ id, grammar, node_dispatch: HashMap<&str, NodeHandler>, payload_schema }`
- Extract the current C++ `match node.kind()` arms into a `CPP_PROFILE` dispatch table
- The shared iterative-DFS core (`walker.rs`) iterates nodes and looks up `node_dispatch` — language-agnostic
- Verify C++ output is byte-identical after refactor (regression test against Phase 1 of pilot)

**Validation:** C++ payload equality before/after refactor on the 10 pilot fixtures.

### Phase 2 — Tier 1 ports: go, rust, swift  (schema-compatible)

**Goal:** Add the three languages that share the cplus schema — lowest effort, highest ROI if Phase 0 flags them.

**Per language:**
- Build `node_dispatch` table mapping that grammar's node types → existing extractors:
  - go: `struct_type`, `interface_type`, `method_declaration`, `field_declaration`, `selector_expression`
  - rust: `method_call_expression`, `macro_invocation`, `trait_item`, `function_signature_item`, `scoped_identifier`
  - swift: `protocol_declaration`, `init_declaration`, `deinit_declaration`, `subscript_declaration`, `macro_invocation`
- Reuse `build_payload` unchanged (same schema)
- Add `extract_batch(paths, root, language, threads)` dispatch (function already exists as stub)
- Python side: each `parse_*_file()` gets `try cortex_extract first, fallback to Python`

**Validation:** Per-language differential test — Rust payload == Python `parse_*_file()` output on fixtures.

### Phase 3 — Tier 2 ports: java, kotlin, js, csharp, php, python, ts, sql  (schema-divergent)

**Goal:** Port the 8 languages whose schema differs. java+kotlin share a schema (port as a pair).

**Key design decision — schema adapter:** These languages cannot reuse cplus `build_payload`. Two options:
- **(A) Per-language payload builder** in Rust — each language has its own `build_<lang>_payload` matching its Python `parse_*_file` return. Cleaner, more code.
- **(B) Unified superset schema** — normalize all to one superset dict. More invasive on the Python graph-write side.

→ **Recommend (A)**: keep the Python graph-write side untouched; Rust mimics each language's existing exact schema. The graph writer already consumes each language's distinct shape today.

**Per language:**
- `node_dispatch` table for that grammar
- Per-language `build_<lang>_payload` (e.g. java emits `classes` + `type_edges` + `package_def`, not `types` + `namespaces`)
- ts additionally extracts React/navigation: `renders`, `navigates`, `api_calls`, `navigators`, `param_lists`
- Extend `extract_batch` to route by language to the correct builder

**Validation:** Differential test per language: Rust output == Python `parse_*_file()` output.

### Phase 4 — Tier 3 ports: delphi, plsql  (regex / hybrid)

**Goal:** Port the two regex-dependent analyzers.

**delphi (tree-sitter + regex fallback):**
- Port the tree-sitter path (Tier-2 style walker for `tree_sitter_delphi`/`tree_sitter_pascal`)
- Port the regex fallback path (`parser_language = "regex_fallback"`) using Rust `regex` crate
- Decide at runtime which path based on grammar availability (mirrors Python's `delphi_tree_sitter` vs `regex_fallback` detection)
- Emits `uses_units` (not `using_namespaces/includes`)

**plsql (pure regex — NO AST):**
- There is no tree-sitter tree to walk. Port the `_PLSQL_*_RE` pattern set to Rust `regex`
- Output is reconstructed from regex matches, not AST traversal
- Lowest ROI candidate — confirm via Phase 0 before committing

**Validation:** Differential test: Rust regex output == Python regex output on PL/SQL fixtures.

### Phase 5 — Tier 4 ports: cobol, flutter, perl  (different architecture)  🔒 BLOCKED

**Goal:** Port the three analyzers that don't emit a payload dict at all — they emit a facts/records model.

**Blockers (must stabilize first):**
- 🔒 `260714-1603-flutter-analyzer-parser` (status: in_progress)
- 🔒 `260715-1629-perl-analyzer-parser` (status: pending)
- cobol parser is `complete-with-exclusions` — contract mostly stable

**Key design decision — new Rust contract:** These three cannot use `build_payload`. Options:
- **(A) Per-language facts builder** — Rust builds each one's native facts/records model directly (cobol `result.nodes/.edges`, flutter facts, perl `SymbolRecord`). Most faithful, most code.
- **(B) Unified normalized-facts IR** — define one Rust IR, translate per language, let Python map IR → graph. Bigger upfront design, less per-language code long-term.

→ **Defer this decision to Phase 5 kickoff** — it depends on where flutter/perl parser plans land. Do NOT design in the abstract now.

**Deliverables (sketch, pending unblock):**
- Per-language facts builder OR unified IR (decision at kickoff)
- Integration with each analyzer's existing staged pipeline (`cobol.pipeline`, `flutter.pipeline`, `perl.pipeline`)
- These don't plug into `extract_batch` — they need their own entry points

**Validation:** Differential test against each analyzer's `to_json()` / `build_graph_rows()` output.

### Phase 6 — Semantic enrichment per language

**Goal:** Port `SemanticInferenceEngine.enrich_corpus()` for each language. **Do not skip this** — for C++ it was 42% of runtime; the memory record is explicit that the Rust initiative must target BOTH extraction and semantic, never just extraction.

**Per language:**
- The naming/type/usage/body regex signal rules **differ per language** (e.g. Java getters `getX`, Python `snake_case`, Go `Get`/`Foo` exported-by-capital)
- `semantic.rs` (currently C++ regex set) becomes per-language signal rule sets
- `enrich_corpus(functions, calls)` already mutates in place — extend to take a `language` param

**Validation:** Differential: same functions → same `intent`/`confidence`/`signals` as Python per language.

### Phase 7 — Integration, fallback, CI, wheels

**Goal:** Production-ready: every analyzer calls Rust by default, falls back to Python; wheels for all platforms.

**Deliverables:**
- Each `<lang>_analyzer.py` `_load_or_parse_payload()` / `iter_payloads()` → try Rust, fallback Python
- `build_call_graph()` per analyzer → `cortex_extract.extract_batch(..., language)` when available
- Prebuilt wheels (macOS arm64/x86_64, Linux manylinux) via `maturin` CI
- Pure-Python fallback path with deprecation warning
- Extend `profile_analyzer.py` to any Tier-4 languages still unsupported (vb, plsql)

**Validation:** End-to-end `dev sync code` on a multi-language corpus with Rust enabled vs disabled; assert graph parity.

## Dependency Graph

```
Phase 0 (profile gate)
   │
   ├──► Phase 1 (trait refactor) ──► Phase 2 (Tier 1: go/rust/swift)
   │                                       │
   │                                       ▼
   │                                 Phase 3 (Tier 2: java/kotlin/js/
   │                                         csharp/php/python/ts/sql)
   │                                       │
   │                                       ▼
   │                                 Phase 4 (Tier 3: delphi/plsql)
   │
   ├──► Phase 5 (Tier 4: cobol/flutter/perl)  🔒 blocked on parser plans
   │
   └──► Phase 6 (semantic, per language) runs parallel to 2/3/4 per language

Phase 7 (integration/CI) consumes all of the above
```

**Note:** Phases 2/3/4 can be reordered by Phase 0's ROI ranking. If Phase 0 shows Java is the worst bottleneck, do Java (Tier 3) before go (Tier 2). Tier governs *effort*, not *priority* — Phase 0 governs priority.

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Porting a language that isn't actually a bottleneck | **High** | Phase 0 gate: no port without profiling proof of `parse+extract+semantic ≥ 50%` |
| Schema drift between Rust and Python per language | High | Differential test harness per language (Rust == Python on fixtures) — build in Phase 1 |
| Tier 4 parser contracts change (flutter/perl in flux) | High | Phase 5 blocked until those plans stabilize; don't design Tier 4 IR in the abstract |
| Semantic signal parity (Phase 6) — per-language rules | Medium | Differential testing per language; the rules genuinely differ (getX vs snake_case) |
| Per-language payload builder explosion (Phase 3 option A) | Medium | Extract shared builder helpers; accept some duplication (3 similar lines > premature abstraction) |
| plsql has no AST — regex port has low ROI | Medium | Confirm in Phase 0; if `graph/embed` dominates plsql runtime, skip the port |
| Wheel matrix for 5+ grammars | Medium | `maturin` CI; all grammars compile into one `.so`/`.pyd` |
| Scope creep into graph-write/embedding | Low | Hard boundary restated: Rust = extraction + semantic only |

## Success Criteria

- [x] Phase 0: go/no-go matrix complete for all 12 profiler-supported languages
- [x] Phase 1: C++ output byte-identical after trait refactor (no regression)
- [x] Phase 2 (Go): Rust output == Python output ✅ (commit 7cef6da)
- [ ] Phase 2 (Rust-lang): → see `260731-1800-rust-extraction-tier1-rust-lang`
- [ ] Phase 2 (Swift): → see `260731-1830-rust-extraction-tier1-swift`
- [ ] Phase 3 (TS): → see `260731-1900-rust-extraction-tier2-ts`
- [ ] Phase 3 (Java/JS/C#/PHP/SQL): → see `260731-2000-rust-extraction-tier2-java-js-csharp-php-sql`
- [ ] Phase 4 (Delphi/PLSQL): → see `260731-2100-rust-extraction-tier3-delphi-plsql`
- [ ] Phase 5: cobol/flutter/perl facts model built in Rust, == Python (after unblock)
- [ ] Phase 6: semantic enrichment parity per language (100% of test functions)
- [ ] Phase 7: every analyzer runs Rust-first with Python fallback; wheels published

## Per-Language Sub-Plans (independent, run in any order)

| Plan | Tier | Languages | Priority | Status |
|------|------|-----------|----------|--------|
| `260731-1800-rust-extraction-tier1-rust-lang` | 1 | Rust | 8 | pending |
| `260731-1830-rust-extraction-tier1-swift` | 1 | Swift | 7 (borderline) | pending |
| `260731-1900-rust-extraction-tier2-ts` | 2 | TypeScript | **1** (highest ROI) | pending |
| `260731-2000-rust-extraction-tier2-java-js-csharp-php-sql` | 2 | Java, JS, C#, PHP, SQL | 3-9 | pending |
| `260731-2100-rust-extraction-tier3-delphi-plsql` | 3 | Delphi, PLSQL | 4-5 | pending |

**Run with:** `/hi-craft --full plans/<plan-dir>/plan.md`

## Relationship to the Pilot Plan

This plan **supersedes Phase 6** of `260731-1030-rust-extraction-layer`. The pilot plan's Phases 1–5 (C++ extraction, batch, resolve, semantic, integration) remain the authoritative C++ work and are the foundation Phase 1 here refactors. The pilot's Phase 6 stub (`grammar.rs` placeholder walkers) is replaced by Phases 2–5 of this plan.
