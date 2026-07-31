# Phase 2: Multithreaded Batch Extraction

## Goal

Process all 2493 C++ files in parallel using Rust rayon threads. Returns a list of payload dicts. Proves the core performance win: 92.8s → <15s.

## Architecture

### Thread-per-file with rayon

```rust
// extractor.rs
use rayon::prelude::*;

#[pyfunction]
fn extract_cplus_batch(
    paths: Vec<String>,
    root: &str,
    threads: Option<usize>,
) -> PyResult<Vec<PyObject>> {
    let n_threads = threads.unwrap_or_else(|| num_cpus::get());
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(n_threads)
        .build()
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;

    let py = unsafe { Python::assume_gil_token_acquired() };

    // Parse + extract in parallel (NO GIL — pure Rust work)
    let payloads: Vec<Result<ExtractedPayload, String>> = pool.install(|| {
        paths.par_iter()
            .map(|path| extract_file(path, root))
            .collect()
    });

    // Re-acquire GIL to build PyDicts (must hold GIL for PyDict creation)
    let py = Python::acquire_gil();
    let py = py.python();

    let result: Vec<PyObject> = payloads
        .into_iter()
        .map(|p| match p {
            Ok(extracted) => build_payload(py, extracted),
            Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        })
        .collect::<PyResult<_>>()?;

    Ok(result)
}
```

### Key design decisions

**1. Parser reuse via `thread_local!`:**
```rust
thread_local! {
    static CPP_PARSER: RefCell<tree_sitter::Parser> = {
        let mut p = tree_sitter::Parser::new();
        p.set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("Error loading C++ grammar");
        RefCell::new(p)
    };
    static C_PARSER: RefCell<tree_sitter::Parser> = {
        let mut p = tree_sitter::Parser::new();
        p.set_language(&tree_sitter_c::LANGUAGE.into())
            .expect("Error loading C grammar");
        RefCell::new(p)
    };
}

fn get_parser(is_cpp: bool) -> &'static thread_local::LocalKey<RefCell<Parser>> {
    if is_cpp { &CPP_PARSER } else { &C_PARSER }
}
```

**2. No Python objects during parallel phase:**
- Extraction (walk_tree) produces Rust structs only — no PyDict, no GIL needed
- PyDict conversion happens AFTER parallel phase, in sequential GIL-held pass
- This is critical: PyO3 holds GIL during `PyDict::new()`

**3. Memory-bounded via streaming (optional):**
```rust
// For very large repos: process in chunks, yield results
fn extract_cplus_batch_stream(
    paths: Vec<String>,
    root: &str,
    threads: usize,
    chunk_size: usize,  // e.g. 500
) -> PyResult<Vec<PyObject>> {
    // Process chunk_size files at a time, build PyDicts, then free Rust structs
    // Keeps peak memory bounded regardless of file count
}
```

### GIL release pattern

```
Python calls cortex_extract.extract_cplus_batch()
    │
    ├── PyO3 releases GIL (pyo3::allow_threads)
    │
    ├──   Rust rayon: 8 threads parse + extract
    │     (no Python objects — pure Rust structs)
    │     Thread 1: file[0] → ExtractedPayload
    │     Thread 2: file[1] → ExtractedPayload
    │     ...
    │
    ├── PyO3 re-acquires GIL
    │
    ├──   Sequential: convert Vec<ExtractedPayload> → Vec<PyDict>
    │     (must hold GIL for Python object creation)
    │
    └── Return Vec<PyObject> to Python
```

## Python integration

```python
# cplus_analyzer.py — modified iter_payloads()

def iter_payloads(log_parse: bool):
    if _RUST_AVAILABLE and not incremental:
        # Batch path: parse all files at once in Rust
        payloads = _rust.extract_cplus_batch(all_file_paths, root, threads=8)
        for i, payload in enumerate(payloads, 1):
            if log_parse and verbose and (i == 1 or i % 500 == 0):
                print(f"[rust] parsed {i}/{total_files}")
            # Write to cache
            if parse_cache:
                rel = os.path.relpath(all_file_paths[i-1], root)
                sig = file_signature(all_file_paths[i-1])
                write_parse_cache(parse_cache_root, rel, sig, payload)
            yield payload
    else:
        # Sequential fallback (existing code)
        for index, file_path in enumerate(all_file_paths, start=1):
            yield _load_or_parse_payload(file_path, root, parse_cache_root, ...)
```

## Performance projection

| Config | Extract time | Speedup vs current |
|--------|-------------|-------------------|
| Python sequential (current) | 92.8s | 1.0x |
| Rust 1 thread (Phase 1) | ~35s | 2.7x |
| Rust 4 threads | ~12s | 7.7x |
| Rust 8 threads | ~8s | 11.6x |
| Rust 8 threads + semantic (Phase 4) | ~15s | 6.2x |

*Estimates based on: AST walk is 21x slower in Python than C parse; Rust walk ~3x faster than Python walk; 8 threads with overhead factor 0.85.*

## Memory projection

```
Rust batch (2493 files, 8 threads):
  source_bytes: mmap'd per file, freed after parse = ~0MB persistent
  ExtractedPayload structs: 2493 × ~15KB = ~37MB
  PyDict payloads (after conversion): 2493 × ~50KB = ~125MB
  thread_local parsers: 8 × ~5MB = ~40MB
  rayon overhead: ~10MB
  TOTAL: ~210MB (single process)

vs Python ProcessPool (8 processes):
  8 × (210MB payloads + 50MB indexes) = 2.08GB → OOM
```

## Validation

```bash
# Build
cd rust-analyzer-core && maturin develop --release

# Profile batch
python profile_analyzer.py --target /path/to/2493_cpp --language cplus --rust-batch --threads 8

# Expected output:
# 2b. FULL PARSE + EXTRACT (Rust batch, 8 threads):  ~8-12s  (vs 92.8s)
# Memory: <300MB peak
```

## Deliverables

- [ ] `cortex_extract.extract_cplus_batch()` function
- [ ] rayon thread pool with `thread_local!` parsers
- [ ] GIL-release-during-parallel pattern
- [ ] 2493 C++ files processed in <15s
- [ ] Peak memory < 500MB
- [ ] Payload equality vs Phase 1 single-file output
