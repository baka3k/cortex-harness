# Prediction Report: Rewrite GraphCode Parser in Rust

| Field | Value |
|-------|-------|
| **Date** | 2026-07-31 |
| **Depth** | deep |
| **Verdict** | 🟡 **CAUTION** |
| **Proposal** | Rewrite the GraphCode (tree-sitter) parser layer in Rust, keep the Semantic (ML embedding) layer in Python for performance. |

---

## Executive Summary

The proposal to rewrite the GraphCode parsing layer in Rust is **architecturally sound in intent but built on a flawed premise about where time is actually spent**. Tree-sitter parsing is *already* native C — the current Python code is merely AST-walking orchestration, so rewriting the walker in Rust yields a smaller-than-expected speedup while introducing a greenfield Rust codebase (zero Rust exists today), a new FFI boundary, and a build/distribution burden across 12+ duplicated analyzers. The real performance wins are available *without* Rust: parallelizing the currently-sequential parse loop, deduplicating the 12+ copy-pasted monolithic analyzers, and addressing the genuinely-I/O-bound embedding and graph-write phases. **Proceed only if you accept the maintenance cost of a Rust+Python bilingual stack in exchange for a measurable-but-not-transformational parsing speedup.**

---

## Verified Code Context (Phase 0)

> ⚠️ **Terminology correction:** The terms **"Rotley" and "GraphCode" do not exist anywhere in the codebase** (searched case-insensitive, zero matches). The real pipeline is: **tree-sitter parse → AST walk → dataclass extraction → content-addressed JSON cache** — all in-process Python, no separate preprocessing binary/tool. This report maps your "GraphCode" to the tree-sitter analyzer layer and "Rotley" to the JSON cache step.

| Finding | Evidence |
|---------|----------|
| **"Rotley" and "GraphCode" are not real** | Full-repo search for `rotley`, `Rotley`, `GraphCode`, `graph_code`, `graphcode` → **zero matches**. The real pipeline is in-process Python tree-sitter → dataclass → JSON cache. |
| **42 analyzer files, ~22 distinct grammars** | python, java, ts/tsx, js, c#, go, rust, kotlin, swift, c, c++, dart, php, perl, delphi/pascal, cobol, sql, plsql, vb6, vba, vb.net, vbscript + framework overlays (spring, mybatis, android, aspnet, flutter, etc.). |
| **Zero Rust in repo** | Glob `**/{Cargo.toml,*.rs}` → no files. Greenfield rewrite, no Rust toolchain, no PyO3/maturin precedent. |
| **Tree-sitter is already C-native** | `from tree_sitter import Language, Parser` in every analyzer — the C grammar library does parsing; Python only walks the resulting AST. `tree_sitter_languages` ships precompiled grammars. |
| **Parse loop is SEQUENTIAL (except VB)** | `python_analyzer.py:1402` — `for index, file_path in enumerate(selected_files)` — single-threaded. **Only `vb/vb_analyzer_base.py:215` uses ThreadPoolExecutor** — the sole concurrency in the entire tools tree. |
| **Parser NOT cached as singleton in most analyzers** | `_get_python_parser()` (python_analyzer.py:488) constructs a fresh `Parser()` on every file. **Exception: `cplus_analyzer.py:543-588` correctly caches module-level singletons** (`_CPP_PARSER`, `_C_PARSER`) with comment "tree-sitter Parser.parse() is stateless between calls, so sharing is safe". This is the reference pattern Python/Java/JS/TS should adopt — a one-line fix. |
| **All payloads loaded into memory at once** | python_analyzer.py:1390 comment: "Must be in-memory list (not generator) so semantic enrichment mutations are visible" — every function/class/call from every file held simultaneously + derived indexes. |
| **C++ has dual-parser fallback (libclang)** | `cplus_analyzer.py:2829-2869` — re-parses with libclang when tree-sitter reports too many ERROR nodes (threshold at line 65). `clang_parser.py` contains the libclang glue. This is language-specific complexity that would need careful handling in any Rust port. |
| **TS is most decomposed; others are monolithic** | `ts/agents/{parser_agent,traversal_agent,symbol_agent}.py` split parsing/walking/extraction. Python/Java/JS keep everything in one 1300–2700 line file. |
| **12+ duplicated monolithic analyzers** | `CodeEmbedder` + `QdrantWriter` copy-pasted into python, java, ts, js, csharp, kotlin, cplus, php, delphi, sql, plsql, android_kotlin analyzers. Each is 1300–2700 lines combining parse + cache + graph-write + embed. |
| **SemanticInferenceEngine is heuristic, not ML** | `common/semantic_inference.py:434` — regex/naming/type/body signals with weighted scoring. Language-agnostic intent taxonomy. No torch/transformers here. Builds the `note` field = the actual text fed to the embedder. |
| **TWO coexisting embedding stacks (not one)** | **Stack A** (newer/shared): `sentence-transformers` → `common/primary_vector_sync.py:375-388`. Used by go, rust, swift, perl, flutter, cobol. **Stack B** (legacy/duplicated): `torch`+`transformers` inside the copy-pasted `CodeEmbedder`. Used by java, js, ts, cplus, kotlin, delphi, php, sql, android_kotlin, plsql, csharp, python (12 files). |
| **`qdrant_client` declared but deliberately unused** | `intelligent_retrieval.py:93-101` comment: "talks REST directly so it doesn't have to import qdrant_client". All upserts/retrieval use raw HTTP REST. |
| **Embedding model reloads EVERY process** | No shared embedding daemon. Each analyzer invocation reloads jina-v3 weights. For multi-language repos → N model loads. Stack A instantiates inside `sync_vector_documents()`; Stack B inside `main()`. |
| **Embedding batch_size=4 (tiny), with stop-the-world gc** | Default `--batch-size 4` (sql_analyzer.py:2090). `gc.collect()` every 50 batches (sql_analyzer.py:1978). Qdrant upsert uses synchronous `?wait=true`. |
| **Numpy→list conversion overhead** | `primary_vector_sync.py:430`: `convert_to_numpy=True` then `.tolist()` per point → ~131K Python float objects per 1024-dim × 128-batch upsert. |
| **Embedding contract is already abstract** | `primary_vector_sync.py:352` accepts `embedder_factory`; `intelligent_retrieval.py` accepts `embedder: Callable[[str], List[float]]`. Could swap to fastembed/ONNX or remote API without rewriting the parse layer. |
| **Native-code precedent is a tree-sitter grammar, not app logic** | `cobol/lib/cobol.cpython-310-darwin.so` is a compiled tree-sitter COBOL grammar exporting `tree_sitter_cobol` — a CPython extension for grammar loading, not a pattern for rewriting application logic in Rust. |
| **Cache is trivial JSON I/O** | `common/analyzer_cache.py` — SHA1-hashed filename, mtime+size signature, atomic `os.replace`. Not a bottleneck. |

---

## Agreements (4+ personas aligned)

| # | Agreement | Personas |
|---|-----------|----------|
| A1 | **The parse loop being single-threaded is the cheapest, highest-ROI fix and requires zero Rust.** Multiprocessing/`concurrent.futures` in Python parallelizes the already-native tree-sitter parsing today. | Architect, Performance, Devil's Advocate, UX |
| A2 | **The 12+ duplicated analyzers are a bigger problem than Python-vs-Rust.** Deduplication into a shared core removes ~10k lines of copy-paste and is a prerequisite to *any* rewrite being maintainable. | Architect, Performance, Devil's Advocate, Security |
| A3 | **Keeping the ML embedding (CodeEmbedder) in Python is correct** — torch/transformers bind to the Python ML ecosystem; no benefit to porting. | Architect, Performance, Security, UX |
| A4 | **A Rust rewrite is viable *technically*** — tree-sitter has excellent Rust bindings (`tree-sitter` crate), and AST walking is CPU-bound work Rust handles well. The question is ROI, not feasibility. | Architect, Performance |

---

## Conflicts Table

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
|-------|-----------|----------|-------------|----|------------------|------------|
| **Is Rust the right tool?** | Long-term yes IF bilingual stack is accepted | Neutral — cares about the FFI boundary, not language | Marginal gain vs. parallelizing Python | Indifferent | **No — simpler alternatives unexplored** | **Devil's Advocate wins (per conflict rules: unvalidated assumption → CAUTION).** Mandate: benchmark Python-parallel first. |
| **FFI boundary risk** | Manageable via PyO3/maturin, well-trodden path | **New attack surface** — native parsing of untrusted source, memory safety matters even in Rust for path/regex handling | Serialization overhead at boundary (JSON/msgpack crossing) | Build/distribution complexity for end users | Wheel-building CI burden is real and often underestimated | **Security wins for auth/data concerns; Architect defers for the boundary design.** Require a defined serialization contract + fuzz testing. |
| **Biggest perf win location** | Coupling/dedup is the structural win | — | **Embedding + Neo4j writes are I/O-bound**, parsing is not the dominant cost | Faster parsing → faster user feedback on sync | "Rewrite in Rust" assumes parsing is the bottleneck — **unvalidated** | **Performance + Devil's Advocate align: profile before rewriting.** Parsing speedup is real but may be <30% of total wall-clock. |

---

## Risk Summary

| Risk | Severity | Persona | Mitigation |
|------|----------|---------|------------|
| Greenfield Rust — no team Rust expertise inferred, no toolchain/CI | **High** | Devil's Advocate | Start with ONE analyzer as a pilot; add Rust toolchain to CI; require a PyO3 wheel build before expanding. |
| FFI boundary introduces serialization cost that erases parsing gains | **High** | Performance | Benchmark end-to-end (file → payload dict in Python) including crossing. Use msgpack/cbor, not JSON, at the boundary. |
| Rewriting 12+ duplicated analyzers in Rust multiplies scope 12x | **High** | Architect | **Deduplicate into a shared parser core FIRST**, then port the core once. Never port the duplication. |
| Native parser processes untrusted source paths/content | **Medium** | Security | Rust's memory safety helps, but still require input validation, path traversal guards, and fuzz testing on the parser. |
| Distribution: users must install a Rust-compiled wheel per platform | **Medium** | UX | Ship prebuilt wheels (manylinux, macOS universal). Fallback to pure-Python path if wheel import fails. |
| Embedding/graph-write (the actual bottlenecks) untouched → disappointing end-to-end result | **High** | Performance | Profile the full pipeline first; if parsing is <30% of time, the Rust rewrite won't move the metric users feel. |
| Maintenance of bilingual stack (Rust core + Python orchestration + Python ML) | **Medium** | Architect | Clear ownership boundary: Rust = parse-only library producing a versioned payload contract; Python = everything else. |

---

## Per-Persona Detail

### 🏛 Architect

```yaml
concerns:
  - "Rewriting 12+ copy-pasted monolithic analyzers (python_analyzer.py is 2224 lines,
     java 1500+, cplus 2400+) in Rust multiplies the porting surface 12-fold. The
     duplication must be collapsed into ONE shared parser core BEFORE any port."
  - "The current architecture entangles four concerns in each analyzer file:
     (1) tree-sitter parse, (2) JSON cache, (3) Neo4j/FalkorDB graph write,
     (4) Qdrant ML embedding. A Rust rewrite must target ONLY concern #1 and leave
     a clean versioned payload contract (the ParseResult schema in Readme.md) as the
     boundary — otherwise Rust will creep into graph-write/embed territory."
  - "No Rust toolchain, no Cargo.toml, no PyO3/maturin exists in this repo. This is a
     net-new architectural style. The team must decide to own a bilingual stack long-term."
  - "The cobol .so precedent is a tree-sitter GRAMMAR, not application logic — it does
     not establish a pattern for rewriting the analyzer orchestration in native code."
recommendations:
  - "Phase 1: Extract a shared parse core in Python that all 12 analyzers delegate to
     (collapse the duplication). This alone is a major win and is the prerequisite."
  - "Phase 2: If parsing is profiled as the bottleneck, port ONLY the shared parse core
     to Rust as a PyO3 extension producing the same ParseResult payload."
  - "Define a frozen ParseResult schema (it already exists in tools/Readme.md) as the
     FFI contract. Rust outputs this exact shape; Python consumes it unchanged."
  - "Keep graph-write (LanguageCodeWriter) and embedding (CodeEmbedder) in Python —
     they are I/O and ML bound respectively, not CPU-bound parsing."
confidence: high
```

### 🔒 Security

```yaml
threats:
  - "New FFI boundary: native Rust code parsing untrusted/external source code paths.
     While Rust is memory-safe, the deserialization of its output back into Python
     (msgpack/JSON) is a new parsing surface that must be hardened."
  - "If Rust parser reads arbitrary file paths, path-traversal and symlink handling
     must be explicitly validated — C-native tree-sitter reads bytes, but the file
     enumeration layer in Rust could introduce traversal bugs."
  - "Build supply chain: Rust dependencies (tree-sitter crates, serde) add a new
     dependency audit surface (cargo audit) alongside the existing pip audit."
severity: medium
mitigations:
  - "Treat the Rust parser as untrusted-input code: fuzz test the parse entrypoint,
     validate all output via Pydantic/marshmallow schema before consumption."
  - "Pin and audit Rust dependencies with 'cargo audit' in CI, mirroring the pip audit."
  - "Keep path enumeration/sandboxing in Python (the trusted layer); pass only already-
     resolved absolute file paths + bytes to Rust. Rust should not walk the filesystem."
```

### ⚡ Performance

```yaml
bottlenecks:
  - "CRITICAL UNTESTED ASSUMPTION: the proposal assumes parsing is the dominant cost.
     The parse loop (python_analyzer.py:1402) is sequential, but tree-sitter parsing
     itself is C-native and fast. The likely bottlenecks are (a) the embedding phase
     (model inference + Qdrant upserts) and (b) Neo4j/FalkorDB batch writes — both I/O bound."
  - "Sequential parse loop: O(N) files processed one at a time. This is a Python-level
     inefficiency fixable with multiprocessing today — NO Rust needed."
  - "12 duplicated analyzers each rebuild regex/pattern tables per run. A shared core
     compiles these once."
metrics_impact:
  parsing_only: "Rust AST walker likely 2-5x faster than Python AST walker for CPU-bound
    traversal — but tree-sitter's C parse step is unchanged. Net per-file speedup may be
    modest since the C parse dominates the Python walk."
  end_to_end: "If parsing is 20-30% of wall-clock and embedding+graph-write is 70%,
     a 3x parsing speedup yields ~13-20% end-to-end improvement. Parallelizing the
     parse loop in Python could yield a SIMILAR or larger win at near-zero cost."
  embedding_verified_bottlenecks:
    - "Model reloads EVERY analyzer process (no daemon) → N loads for N languages.
       Fix: shared embedding service/daemon. Huge win, zero Rust."
    - "batch_size=4 is tiny → underutilizes GPU. Bumping to 16-32 is a one-line fix."
    - "gc.collect() every 50 batches is stop-the-world (sql_analyzer.py:1978)."
    - "numpy→.tolist() per point creates ~131K Python floats per 128-batch upsert."
    - "Synchronous Qdrant ?wait=true serializes the pipeline (could be async batched)."
    - "ALL of these are in Stack B (the 12 duplicated CodeEmbedders). Stack A
       (primary_vector_sync.py) already fixes some but adoption is incomplete."
  serialization: "Crossing the Rust→Python boundary per-file (serialize ParseResult,
    deserialize in Python) adds overhead that can erode parsing gains, especially for
    small files. Batch the boundary (parse N files, return one payload) to amortize."
alternatives:
  - "FIRST: parallelize the existing Python parse loop with concurrent.futures.ProcessPoolExecutor
     or multiprocessing.Pool. Near-zero code change, scales with core count."
  - "Profile with cProfile/py-spy on a real repo to confirm parsing is actually the
     bottleneck before committing to Rust."
  - "If single-language pilot is wanted, port the HOTTEST analyzer (e.g. java or cplus
     with 2400 lines) only, benchmark, then decide on expanding."
```

### 🎨 UX

```yaml
issues:
  - "Installation complexity: users currently 'pip install' a pure-Python package.
     A Rust extension requires a compiled wheel per platform (macOS arm64/x86_64,
     Linux manylinux, Windows). Missing wheels → build-from-source failures for
     non-developer users."
  - "Faster sync feedback IS a real UX win — the 'dev sync code' command's parse
     phase would complete faster, reducing user wait time. But this only helps if
     parsing is actually the wait they experience."
edge_cases:
  - "User on an uncommon platform (e.g. FreeBSD, older glibc) with no prebuilt wheel
     — must fall back to pure-Python path gracefully."
  - "Rust compile failure in a constrained CI/repo environment — must not break the
     existing pure-Python pipeline."
a11y_concerns:
  - "N/A (CLI tool, no UI) — but error messages from Rust panics across the FFI
     boundary must be caught and rendered as readable Python errors, not raw traces."
```

### 😈 Devil's Advocate

```yaml
assumptions_challenged:
  - "ASSUMPTION: 'GraphCode is slow because it's Python.' REALITY: tree-sitter parsing
     is C. The Python layer walks the AST and runs regex extraction. The premise
     conflates 'Python orchestration' with 'Python is doing the parsing.' It is not."
  - "ASSUMPTION: 'Rewriting in Rust will be faster.' REALITY: faster at WHAT? If the
     bottleneck is embedding (model inference + network to Qdrant) and graph writes
     (network to Neo4j), rewriting the parser changes the metric users actually feel
     by a small margin. No profiling data was provided to support the assumption."
  - "ASSUMPTION: 'The GraphCode and Semantic parts are cleanly separable.' REALITY:
     they are INTERTWINED in one file — python_analyzer.py imports torch+transformers
     AND tree-sitter in the same module. The 'separation' is conceptual, not actual.
     Separating them is itself a prerequisite project before any port."
simpler_alternatives:
  - "DO NOTHING (for Rust): parallelize the Python parse loop (1 day of work) and
     deduplicate the analyzers into a shared core (1 week). Benchmark. This likely
     captures 60-80% of the available speedup at 5% of the risk."
  - "Buy vs build: is there an off-the-shelf fast multi-language call-graph extractor?
     (e.g. tree-sitter CLI, scip-tools, lsif). Evaluate before writing a custom Rust parser."
  - "Profile-guided: spend 1 day with py-spy on the largest repo to find the ACTUAL
     hot path. The data may point somewhere unexpected."
worst_case: "The team spends 4-8 weeks rewriting parsers in Rust, ships it, and the
  end-to-end sync time drops only 10-15% because embedding+graph-write dominate.
  Meanwhile they now maintain a bilingual Rust+Python stack, wheels break on some
  platforms, and the 12 duplicated analyzers are now 12 duplicated Rust+Python pairs.
  The ROI is negative and morale drops."
```

---

## Recommendations (ordered by ROI)

1. **🔧 Profile first (1 day).** Run `py-spy record` on `dev sync code` against the largest available repo. Identify whether parsing, embedding, or graph-write dominates wall-clock. *Rationale: the entire proposal rests on the assumption that parsing is the bottleneck. Validate before committing weeks of work.*

2. **🔧 Parallelize the parse loop (1 day, zero Rust).** The loop at `python_analyzer.py:1402` (and equivalents in 12 analyzers) is single-threaded. Wrap it in `concurrent.futures.ProcessPoolExecutor` — **VB already proves this works** (`vb_analyzer_base.py:215` uses `ThreadPoolExecutor`). Tree-sitter parsing is CPU-bound and embarrassingly parallel. *Rationale: this alone may give a 4-8x parsing speedup on multi-core machines with near-zero risk.*

3. **🔧 Cache the Parser as singleton (hours, zero Rust).** Most analyzers construct a fresh `Parser()` per file. `cplus_analyzer.py:543-588` already proves the singleton pattern with `_CPP_PARSER`/`_C_PARSER` and the comment "Parser.parse() is stateless between calls, so sharing is safe". Apply this to Python/Java/JS/TS/Kotlin/etc. *Rationale: removes repeated object/language setup per file — a one-line fix per analyzer.*

4. **🔧 Deduplicate the 12 analyzers into a shared parse core (1 week).** Collapse the copy-pasted `CodeEmbedder`/`QdrantWriter`/parse logic into one parameterized core. The TS analyzer's `agents/` decomposition (parser/traversal/symbol agents) is the architectural model. *Rationale: this is a prerequisite to ANY rewrite being maintainable. Never port duplication.*

5. **🟡 THEN, if profiling confirms parsing is still the bottleneck after steps 1-4, pilot ONE analyzer in Rust (2-3 weeks).** Pick the hottest language. Build it as a PyO3 extension outputting the existing `ParseResult` schema. Benchmark end-to-end including the FFI crossing. *Rationale: de-risks the approach with bounded scope before committing to all 12.*

6. **🟡 If the pilot proves a clear win, expand to a shared Rust parse core (3-6 weeks).** One Rust library, parameterized by language grammar, consumed by all analyzers. Ship prebuilt wheels with a pure-Python fallback. *Rationale: port the shared core ONCE, not the duplication.*

---

## Next Steps (by verdict)

**CAUTION → address mitigations before proceeding:**

- [ ] Run `py-spy` profile on the full sync pipeline → confirm parsing is the bottleneck (not embedding/graph-write)
- [ ] Implement Python-level parse-loop parallelization (`ProcessPoolExecutor`) → measure baseline improvement
- [ ] Cache `Parser` as module-level singleton (apply the `cplus_analyzer.py:543-588` pattern to all analyzers)
- [ ] Collapse the 12 duplicated analyzers into a shared parse core (TS `agents/` decomposition as model) → mandatory regardless of Rust decision
- [ ] Re-evaluate: if parallelized + singleton-cached + deduplicated Python meets performance targets, Rust may be unnecessary
- [ ] If Rust still warranted → pilot ONE language via PyO3 with the frozen `ParseResult` contract, benchmark end-to-end, then decide on expansion

---

## Known Limitations

- Static analysis only — no runtime profiling was performed; the "parsing may not be the bottleneck" conclusion is inferred from architecture, not measured. **Step 1 (profile) resolves this.** However, the semantic-pipeline investigation (completed) has now verified concrete embedding-side bottlenecks (model reload per process, batch_size=4, gc stop-the-world, sync wait=true upserts, numpy→list overhead) that are independent of parsing and would remain untouched by a Rust parser rewrite.
- No access to the user's actual repo sizes / file counts — the speedup estimates assume moderate-to-large repos (1k–10k files). For very small repos, startup/overhead dominates and neither Rust nor parallelism helps.
