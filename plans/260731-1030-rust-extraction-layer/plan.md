---
title: "Rust Extraction Layer for Analyzer Pipeline"
status: draft
created: 2026-07-31
mode: hi-plan
source: profiling-driven Rust rewrite evaluation
target: code-tiny/tools/ (all language analyzers, pilot: cplus)
scope: Port tree-sitter AST walk + extraction + semantic enrichment to a Rust native extension; keep embedding/graph-write in Python
blockedBy: []
relatedPlans: [260731-1400-graph-write-optimization, 260731-1700-multi-language-rust-extraction, 260731-2200-jp1-shell-proc-batch-coverage]
supersededPhase6By: 260731-1700-multi-language-rust-extraction
---

# Rust Extraction Layer for Analyzer Pipeline

## Overview

Port the **CPU-bound Python extraction layer** (tree-sitter AST walk + dataclass build + semantic enrichment) to a Rust native extension via PyO3. The Python orchestration layer (graph-write, embedding, CLI) stays unchanged. The Rust extension produces the **exact same `ParseResult` payload** — Python consumes it without knowing whether it came from Python or Rust.

```
dev sync code
  → Python: scan files, manage cache, orchestrate pipeline
  → 🔧 Rust: parse file → AST walk → extract symbols/calls/relations → build payload dict
  → Python: resolve calls, build indexes, write to Neo4j/FalkorDB
  → Python: load embedding model (torch), embed, write to Qdrant
```

## Verified Performance Data (profiled on 2493 C++ files)

| Phase | Python (current) | Rust (estimated) | Saving |
|-------|-----------------|-------------------|--------|
| FULL PARSE + EXTRACT | 92.8s (82.8%) | ~12s (8 threads) | ~80s |
| SEMANTIC ENRICHMENT | 80.5s (skip in pilot) | ~10s (later phase) | ~70s |
| Tree-sitter C parse | 4.4s (already native) | ~4.4s (unchanged) | 0 |
| Cache I/O | 12.4s | 12.4s (stays Python) | 0 |
| Embedding | (not measured) | (stays Python) | 0 |
| **TOTAL (parse+extract)** | **112s** | **~24s** | **~88s (79%)** |

## Architectural Constraints (from profiling + user feedback)

1. **Full `_walk_tree` is mandatory** — every node in every file must be visited (headers, tiny files, generated code). No skipping or fast-pathing.
2. **ProcessPool/ThreadPool is blocked** — production analyzer OOMs at ~210MB/process × 8 = 1.7GB. Rust threads share one heap → no memory multiplication.
3. **GIL blocks ThreadPool** — confirmed: ThreadPool gave 1.0x speedup (GIL held during Python AST walk). Rust has no GIL.
4. **Embedding must stay Python** — `torch`/`transformers`/`sentence-transformers` bind to Python ML ecosystem.
5. **Graph-write stays Python** — Neo4j/FalkorDB drivers are async Python; migration in progress.
6. **ParseResult schema is frozen** — Rust must output the exact same dict shape Python produces (documented in `code-tiny/tools/Readme.md`).

## Boundary: What Rust Does vs What Python Does

### 🔧 RUST (CPU-bound, parallelizable, no external deps)

| Component | Responsibility | Why Rust |
|-----------|---------------|----------|
| **Parse engine** | Call tree-sitter C parse via Rust `tree-sitter` crate | Zero-copy byte access; same C grammar |
| **AST walker** | Walk every node, dispatch by `node.type` | No GIL → real multi-threading |
| **Symbol extractor** | Build `FunctionDef`, `TypeDef`, `FieldDef`, etc. | Compact structs vs Python dict overhead |
| **Call resolver** | Extract call edges, match callee names | CPU-bound pattern matching |
| **Relation builder** | EXTENDS, DECLARES, CONTAINS, USES_TYPE, etc. | Deterministic graph construction |
| **Semantic signals** | Naming/usage/type/body regex heuristics | Regex crate faster than Python `re` |
| **Payload builder** | Serialize to Python dict via PyO3 | Direct `PyDict` construction, no JSON crossing |
| **Multithreaded dispatch** | N worker threads on rayon, shared `Arc<>` indexes | One heap, no memory multiplication |

### 🐍 PYTHON (I/O-bound, ML-bound, async-bound)

| Component | Responsibility | Why Python |
|-----------|---------------|------------|
| **File scanning** | `os.walk`, skip-dirs, manifest diff | I/O-bound; existing `_scan_*_files` works |
| **Cache management** | JSON read/write, mtime signatures, atomic replace | I/O-bound; `analyzer_cache.py` unchanged |
| **Call graph indexes** | `function_index_by_name`, `class_methods`, etc. | Built FROM Rust payloads; needs full corpus view |
| **Graph writer** | `LanguageCodeWriter` → Neo4j/FalkorDB batch writes | Async driver, Cypher/query language |
| **Embedding model** | `CodeEmbedder` (torch, transformers, AutoModel) | ML ecosystem lock-in |
| **Qdrant writer** | REST upserts, collection management | Network I/O |
| **Message scan** | `run_message_scan_pipeline` | Post-parse pipeline |
| **CLI / orchestration** | `main()`, argparse, `build_call_graph()` async loop | Entry point; stays as-is |

## Phase Breakdown

> **📖 [phase-00-end-to-end-data-flow.md](phase-00-end-to-end-data-flow.md)** — Detailed data flow from tree-sitter → cache → Rust → Qdrant + FalkorDB. Read this first to understand the full pipeline boundary.

### Phase 1 — Rust crate skeleton + cplus pilot (parse only)

**Goal:** Rust native extension that can parse ONE C++ file and return the same payload dict as `parse_c_family_file()`.

**Deliverables:**
- `rust-analyzer-core/` Cargo workspace with `pyo3`, `tree-sitter`, `tree-sitter-cpp`, `tree-sitter-c`
- PyO3 module `cortex_extract` with one function: `extract_cplus(path, root) -> dict`
- Internally: parse bytes → walk AST → build `PyDict` payload matching `ParseResult` schema
- Python fallback: if `import cortex_extract` fails, use existing `parse_c_family_file()`

**Validation:** Run `profile_analyzer.py` with `--rust` flag; compare payload dict equality.

### Phase 2 — Multithreaded batch extraction

**Goal:** Rust extension processes ALL files in parallel using rayon, returning a list of payload dicts.

**Deliverables:**
- `cortex_extract.extract_cplus_batch(paths, root, threads) -> list[dict]`
- Internal: `rayon::scope` with N worker threads, each parses + extracts independently
- Memory: one shared heap, no per-thread duplication
- Progress callback via PyO3 `PyObject` (optional)

**Validation:** Run on 2493 C++ files; confirm speedup vs sequential; confirm memory < 500MB.

### Phase 3 — Call resolution + relation building (in Rust)

**Goal:** Move cross-file call resolution and relation inference into Rust.

**Deliverables:**
- Two-phase Rust API: `extract_batch()` → `resolve_batch(payloads, indexes)`
- Rust builds `function_index_by_name` etc. in parallel, resolves `callee_id` for all calls
- Returns fully-resolved payloads ready for Python graph-write

### Phase 4 — Semantic enrichment (in Rust)

**Goal:** Port `SemanticInferenceEngine.enrich_corpus()` to Rust.

**Deliverables:**
- Rust regex-based signal scoring (naming/type/usage/body)
- `cortex_extract.enrich_semantics(functions, calls) -> ()` (mutates in place via PyO3)
- Validates against Python implementation on same input

### Phase 5 — Python integration + fallback + CI

**Goal:** `cplus_analyzer.py` calls Rust by default, falls back to Python.

**Deliverables:**
- Modified `_load_or_parse_payload()` and `iter_payloads()` to try Rust first
- Modified `build_call_graph()` to call `cortex_extract.extract_cplus_batch()` when available
- Prebuilt wheels (macOS arm64/x86_64, Linux manylinux)
- Pure-Python fallback path with warning

### Phase 6 — Expand to other analyzers  → SUPERSEDED

> ⚠️ **This phase has been superseded by a dedicated full-scope plan.** The original
> assumption below ("parameterize by grammar = done") was invalidated by a codebase
> survey: there is no shared extraction core and no shared payload schema across the 17
> analyzers. See **[260731-1700-multi-language-rust-extraction](../260731-1700-multi-language-rust-extraction/plan.md)**
> for the tiered 4-tier roadmap (Phase 0 profiling gate → Phase 1 trait refactor →
> Tier 1/2/3/4 ports). That plan's Phase 1 refactors this pilot's C++ walker into the
> `LanguageProfile` dispatch-table architecture.

**Original (stub) goal — kept for history:** Parameterize Rust core by grammar; add
java, ts, python, etc. In practice `grammar.rs` only loads 5 grammars; the walker
remains C++-specific, so this never went beyond a placeholder.

## Detailed Design: cplus analyzer (pilot)

### Current Python architecture (simplified)

```
cplus_analyzer.py (5022 lines)
│
├── Dataclasses (line 159-305)
│   FileDef, NamespaceDef, TypeDef, FunctionDef, FunctionTypeDef,
│   FieldDef, AliasDef, TemplateDef, RelationEdge, CallEdge
│
├── Tree-sitter parse + AST walk
│   ├── _get_cpp_parser() / _get_c_parser()     (singleton, line 549-600)
│   ├── _parse_file(path, is_cpp)               (line 601)
│   ├── parse_c_family_file(path, root, is_cpp)  (line 1954) → returns 11-tuple
│   └── _walk_tree(node, source_bytes, ...)      (line 1114) → iterative DFS
│
├── Payload cache
│   └── _load_or_parse_payload()                 (line 2630) → cache or parse
│
├── Build call graph (3-pass)
│   ├── Pass 1 (line 3038): iter_payloads → build indexes
│   │   ├── function_index_by_name / _arity / _scope_name
│   │   ├── class_methods, using_namespaces_by_file
│   │   ├── includes_by_file, macros_by_file, base_relations
│   │   └── event_nodes, possible_call_relations
│   ├── _resolve_calls()                         (line 2086) → match callee_id
│   ├── Pass 2 (line 3755): iter_payloads → Neo4j write (streaming batches)
│   │   └── buf_files[], buf_functions[], buf_calls[], ...
│   └── Pass 3 (line 4533): iter_payloads → Qdrant embed
│       └── batch_funcs[] → embedder.embed() → qdrant_writer.upsert()
│
├── Semantic enrichment (optional, from common/semantic_inference.py)
│   └── SemanticInferenceEngine.enrich_corpus(functions, calls)
│
├── Embedding (must stay Python)
│   ├── CodeEmbedder (torch + transformers)
│   └── QdrantWriter (REST)
│
└── Graph write (must stay Python)
    └── LanguageCodeWriter → Neo4j/FalkorDB
```

### Target Rust + Python architecture

```
cplus_analyzer.py (trimmed ~3000 lines — orchestration only)
│
├── 🔧 Try import cortex_extract (Rust extension)
│   └── Fallback: _python_parse_fallback() wraps old parse_c_family_file()
│
├── build_call_graph()  — orchestration unchanged
│   ├── Scan files (Python os.walk — unchanged)
│   ├── 🔧 cortex_extract.extract_cplus_batch(paths, threads=8)
│   │   └── Returns list[dict] — same schema, 8x faster, single heap
│   ├── Build indexes from payloads (Python — needs full corpus view)
│   ├── _resolve_calls() (Python or Rust Phase 3)
│   ├── Stream to Neo4j (Python async — unchanged)
│   └── Embed + Qdrant (Python torch — unchanged)
│
└── Rust crate: cortex-extract-core/
    ├── src/lib.rs                    — PyO3 module registration
    ├── src/extractor.rs              — batch orchestrator (rayon)
    ├── src/parser.rs                 — tree-sitter parser factory
    ├── src/walker.rs                 — AST walker (iterative DFS)
    ├── src/symbols/
    │   ├── mod.rs                    — symbol dispatch
    │   ├── function.rs               — FunctionDef extraction
    │   ├── type_def.rs               — TypeDef extraction
    │   ├── namespace.rs              — NamespaceDef extraction
    │   ├── field.rs                  — FieldDef extraction
    │   ├── alias.rs                  — AliasDef extraction
    │   └── template.rs               — TemplateDef extraction
    ├── src/calls.rs                  — CallEdge extraction
    ├── src/relations.rs              — RelationEdge building
    ├── src/semantic.rs               — (Phase 4) heuristic signals
    ├── src/payload.rs                — PyDict builder (PyO3 → Python dict)
    └── Cargo.toml
```

### Data flow: single file (Rust path)

```
Python: path, root, is_cpp
    │
    ▼
Rust: extract_cplus(path, root)
    ├── parser.parse(source_bytes)           ← tree-sitter C (same grammar)
    ├── walk_tree(root_node, source_bytes)   ← iterative DFS (port of _walk_tree)
    │   ├── for each node: match node.type
    │   │   ├── function_definition → extract_function()
    │   │   ├── class_specifier → extract_type()
    │   │   ├── namespace_definition → extract_namespace()
    │   │   ├── call_expression → extract_call()
    │   │   └── ... (all node types from _walk_tree)
    │   └── collect into Rust Vec<FunctionDef>, Vec<CallEdge>, etc.
    ├── build_payload()                       ← convert Rust structs → PyDict
    │   ├── PyDict_SetItem("functions", PyList of PyDicts)
    │   ├── PyDict_SetItem("calls", PyList of PyDicts)
    │   └── ... (all ParseResult fields)
    └── return PyDict                         ← Python receives native dict
    │
    ▼
Python: receives dict — identical to asdict(parse_c_family_file())
    ├── Cache to JSON (unchanged)
    ├── Add to function_index (unchanged)
    └── Pass 2/3 processing (unchanged)
```

### Data flow: batch multithreaded (Phase 2)

```
Python: list of 2493 file paths
    │
    ▼
Rust: extract_cplus_batch(paths, threads=8)
    ├── rayon::scope:
    │   ├── Thread 1: parse file[0]   → extract → payload[0]
    │   ├── Thread 2: parse file[1]   → extract → payload[1]
    │   ├── ...
    │   └── Thread 8: parse file[7]   → extract → payload[7]
    │       (each thread reuses parser via thread_local!)
    │       (source bytes borrowed from mmap — zero copy)
    ├── Collect all payloads into Vec<PyDict>
    └── return Python list[dict]
    │
    ▼
Python: receives list of 2493 dicts
    ├── Build indexes (single pass over all payloads)
    ├── _resolve_calls()
    └── Stream to Neo4j / Qdrant
```

### Memory comparison

```
Python (current, sequential):
  2493 payloads × ~85KB each = ~210MB in memory
  + indexes: function_index_by_name, class_methods, etc. = ~50MB
  + buf_files, buf_functions (streaming, ~500 file batch) = ~40MB
  Total: ~300MB per process

Rust (8 threads, single heap):
  2493 payloads × ~40KB (compact Rust structs, zero-copy &str) = ~100MB
  + indexes: shared Arc<RwLock<>> = ~25MB (once, not per-thread)
  + thread stack overhead: 8 × 8MB = ~64MB
  Total: ~190MB — ONE process, 8 threads
  (vs Python ProcessPool: 8 × 300MB = 2.4GB → OOM)
```

## Implementation Contract

### ParseResult schema (frozen — Rust must match exactly)

```python
{
    "file_def":     {file_path, start_line, end_line, code, comment, summary, note},
    "functions":    [{symbol_id, qualified_name, name, kind, scope_name, file_path,
                      start_line, end_line, arity, code, comment, summary, note,
                      exported, start_byte, end_byte}],
    "calls":        [{caller_id, caller_file, caller_scope, call_line, call_column,
                      call_start_byte, call_branch_kind, call_loop_depth,
                      call_control_frames_json, call_type, call_arity,
                      callee_name, callee_id}],
    "types":        [{symbol_id, qualified_name, name, kind, file_path,
                      start_line, end_line, code, comment, summary, note}],
    "namespaces":   [{symbol_id, qualified_name, name, file_path,
                      start_line, end_line, code, comment, summary, note}],
    "relations":    [{source_id, source_label, target_id, target_label,
                      rel_type, properties}],
    "function_types": [{symbol_id, type_signature, file_path,
                        start_line, end_line, code}],
    "fields":       [{symbol_id, qualified_name, name, scope_name, type_signature,
                      file_path, start_line, end_line, code}],
    "aliases":      [{symbol_id, qualified_name, name, kind, target_name,
                      file_path, start_line, end_line, code}],
    "templates":    [{symbol_id, name, file_path, start_line, end_line, code}],
    "using_namespaces": [str],
    "using_imports":     {str: str},
    "includes":          [str],
    "macros":            {str: str},
    "parse_meta":        {parser_language, has_error, error_nodes, ...},
}
```

### PyO3 API

```rust
// Phase 1: single file
#[pyfunction]
fn extract_cplus(path: &str, root: &str) -> PyResult<PyObject> {
    // Returns Python dict matching ParseResult schema
}

// Phase 2: batch multithreaded
#[pyfunction]
fn extract_cplus_batch(paths: Vec<String>, root: &str, threads: usize) -> PyResult<Vec<PyObject>> {
    // rayon parallel: each thread parses + extracts independently
    // Returns list of dicts
}

// Phase 4: semantic enrichment
#[pyfunction]
fn enrich_semantics(py: Python, functions: &PyAny, calls: &PyAny) -> PyResult<()> {
    // Mutates function dicts in place (adds intent, signals, note, etc.)
}

// Phase 6: multi-language
#[pyfunction]
fn extract_batch(paths: Vec<String>, root: &str, language: &str, threads: usize) -> PyResult<Vec<PyObject>> {
    // Dispatch by language: cplus, java, python, ts, ...
}
```

### Python integration point

```python
# cplus_analyzer.py — modified _load_or_parse_payload()

try:
    import cortex_extract as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

def _load_or_parse_payload(file_path, root, ...):
    # ... cache check unchanged ...
    if cached_payload:
        return cached_payload
    if _RUST_AVAILABLE:
        payload = _rust.extract_cplus(file_path, root)
    else:
        payload = _python_parse_and_serialize(file_path, root)
    # ... cache write unchanged ...
    return payload
```

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| PyO3 `PyDict` construction overhead erases parsing gains | High | Benchmark Phase 1 early; if slow, use msgpack crossing instead |
| tree-sitter Rust crate grammar differs from Python binding | Medium | Run fixture-based diff test: Rust payload == Python payload |
| Rust thread-safety bugs in walker | Medium | Each thread uses `thread_local!` parser; shared state via `Arc<RwLock>` |
| Wheel distribution for multiple platforms | Medium | Use `maturin` CI builds; fallback to pure Python |
| Semantic enrichment parity (Phase 4) | Medium | Differential testing: same functions → same intent/signals |
| Scope creep into graph-write/embedding | Low | Hard boundary: Rust = extraction only; Python = everything else |

## Success Criteria

- [ ] Phase 1: `cortex_extract.extract_cplus()` returns payload identical to `parse_c_family_file()` for 10 fixture files
- [ ] Phase 2: 2493 C++ files extracted in <15s (vs 92.8s current) — 6x+ speedup
- [ ] Phase 2: Peak memory < 500MB (vs 210MB Python + risk of OOM with ProcessPool)
- [ ] Phase 4: Semantic enrichment matches Python output for 100% of test functions
- [ ] Phase 5: `cplus_analyzer.py` runs with Rust extension; fallback works when unavailable
- [ ] Phase 6: At least 3 more languages ported (java, python, ts)
