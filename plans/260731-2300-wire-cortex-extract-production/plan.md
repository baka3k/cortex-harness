---
title: "Wire cortex_extract into Production Analyzers"
status: pending
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 3
scope: Connect Rust native extraction layer (cortex_extract) into every production analyzer's parse_*_file() so sync code uses Rust by default
priority: 9 — all Rust walkers are built but ZERO analyzers use them; this is the single missing step to realize the speedup
blockedBy: []
---

# Wire cortex_extract into Production Analyzers

## Overview

The Rust native extension (`cortex_extract`) has 11 language walkers built and differential-tested (cplus, go, rust, swift, java, js, ts, csharp, php, delphi, sql). **None are used in production.** Every analyzer runs pure-Python tree-sitter extraction unconditionally. This plan wires `cortex_extract` into each `parse_*_file()` with graceful fallback to pure Python.

### Current architecture (the gap)

```
dev.sh → dev.py sync code → incremental_sync.py
  → spawn subprocess per language → cplus_analyzer.py --root ...
    → parse_c_family_file()  ← ALWAYS pure Python (no cortex_extract)
  → go_analyzer.py --root ...
    → parse_go_file()         ← ALWAYS pure Python
  → rust_analyzer.py --root ...
    → parse_rust_file()       ← ALWAYS pure Python
  → ... (all 9 analyzers, all pure Python)
```

### Target architecture

```
  → cplus_analyzer.py
    → parse_c_family_file()
      → try cortex_extract.extract_cplus(path, root)     ← FAST PATH
      → except ImportError / Exception: pure Python      ← FALLBACK
  → go_analyzer.py
    → parse_go_file()
      → try cortex_extract.extract_go(path, root)        ← FAST PATH
      → except: pure Python
  → ... (all analyzers wired identically)
```

## Two Integration Patterns

The analyzers split into two families by return type:

### Family A — Dict-returning (direct swap)

| Analyzer | Function | cortex_extract function | Rust walker status |
|----------|----------|------------------------|-------------------|
| `go_analyzer.py` | `parse_go_file() -> Dict` | `extract_go(path, root)` | ✅ Tier 1 |
| `rust_analyzer.py` | `parse_rust_file() -> Dict` | `extract_rust(path, root)` | ✅ Tier 1 |
| `swift_analyzer.py` | `parse_swift_file() -> Dict` | `extract_swift(path, root)` | ✅ Tier 1 |

**Wiring:** Rename existing body to `_python_parse_*_file()`, add a 3-line fast-path wrapper.

### Family B — Tuple-returning (needs dict→tuple adapter)

| Analyzer | Function | Tuple size | cortex_extract function | Rust walker status |
|----------|----------|-----------|------------------------|-------------------|
| `cplus_analyzer.py` | `parse_c_family_file()` | 15-tuple | `extract_cplus(path, root)` | ✅ Phase 1 |
| `java_analyzer.py` | `parse_java_file()` | 9-tuple | `extract_java(path, root)` | ✅ Tier 2 |
| `ts_analyzer.py` | `parse_ts_file()` | 12-tuple | `extract_ts(path, root)` | ✅ Tier 2 |
| `csharp_analyzer.py` | `parse_csharp_file()` | 7-tuple | `extract_csharp(path, root)` | ✅ Tier 2 |
| `php_analyzer.py` | `parse_php_file()` | 6-tuple | `extract_php(path, root)` | ✅ Tier 2 |
| `delphi_analyzer.py` | `parse_delphi_file()` | 9-tuple | `extract_delphi(path, root)` | ✅ Tier 3 |

**Wiring:** Write a `_dict_to_tuple_*()` adapter that unpacks the dict payload into the exact positional tuple. Each adapter is language-specific (tuple arity and element types differ).

### Family C — Skip (no Rust walker, pure Python stays)

| Analyzer | Reason |
|----------|--------|
| `ts_backend_analyzer.py` | Backend-specific extraction (API endpoints, controllers) — no equivalent in cortex_extract |
| `sql_analyzer.py` | Regex-based, not tree-sitter (sql_lang.rs exists but analyzer is regex) |

## Phases

### Phase 1 — Shared `_rust_accel.py` module

**Deliverables:**
1. Create `code-tiny/tools/common/_rust_accel.py` — centralized import + feature detection:
   ```python
   try:
       import cortex_extract
       _AVAILABLE = True
   except ImportError:
       cortex_extract = None
       _AVAILABLE = False

   def is_available() -> bool: return _AVAILABLE
   def warn_fallback(lang: str) -> None: ...  # one-time log
   ```
2. Each analyzer imports this once: `from ..common._rust_accel import is_available, warn_fallback`
3. Delete or absorb `code-tiny/tools/cplus/_rust_fallback.py` (orphaned, superseded)

**Validation:** `python3 -c "from tools.common._rust_accel import is_available; print(is_available())"`

### Phase 2 — Wire Family A (dict-returning: go, rust, swift)

**Pattern (identical for all 3):**

```python
def parse_go_file(path, root=None):
    if root is None:
        root = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    try:
        from ..common._rust_accel import cortex_extract
        if cortex_extract is not None:
            return cortex_extract.extract_go(path, root)
    except Exception:
        warn_fallback("go")
    return _python_parse_go_file(path, root)
```

**Deliverables per analyzer:**
1. `go_analyzer.py`: rename `parse_go_file` → `_python_parse_go_file`, add wrapper
2. `rust_analyzer.py`: rename `parse_rust_file` → `_python_parse_rust_file`, add wrapper
3. `swift_analyzer.py`: rename `parse_swift_file` → `_python_parse_swift_file`, add wrapper

**Risk:** LOW — differential tests prove output parity (Rust dict == Python dict).

**Validation:** Run each analyzer on a fixture, verify output unchanged.

### Phase 3 — Wire Family B (tuple-returning: cplus, java, ts, csharp, php, delphi)

**Pattern (per analyzer, example for csharp 7-tuple):**

```python
def parse_csharp_file(path, root):
    try:
        from ..common._rust_accel import cortex_extract
        if cortex_extract is not None:
            d = cortex_extract.extract_csharp(path, root)
            return _dict_to_csharp_tuple(d)
    except Exception:
        warn_fallback("csharp")
    return _python_parse_csharp_file(path, root)

def _dict_to_csharp_tuple(d: dict) -> tuple:
    return (
        d["functions"], d["calls"], d["types"], d["namespaces"],
        d["relations"], d["file_def"], d["parse_meta"],
    )
```

**Deliverables per analyzer:**
1. Rename existing parse → `_python_parse_*_file`
2. Write `_dict_to_*_tuple()` adapter mapping dict keys → tuple positions
3. Add wrapper with try/except fallback

| Analyzer | Adapter mapping (dict key → tuple position) |
|----------|---------------------------------------------|
| cplus | functions, calls, types, namespaces, relations, function_types, fields, aliases, templates, file_def, includes, using_imports, using_namespaces, macros, parse_meta |
| java | functions, calls, classes, type_edges, function_types, relations, file_def, package, parse_meta |
| ts | functions, calls, types, namespaces, relations, render_edges, navigate_edges, file_def, parse_meta, api_calls, navigators, param_lists |
| csharp | functions, calls, types, namespaces, relations, file_def, parse_meta |
| php | functions, calls, types, namespaces, relations, file_def |
| delphi | functions, calls, types, namespaces, fields, relations, file_def, includes, parse_meta |

**Risk:** MEDIUM — tuple element ordering must match exactly. Each adapter needs a quick parity test.

**Validation:** For each analyzer: run on fixture, compare tuple output before/after wiring.

### Phase 4 — Build, install, and smoke test

**Deliverables:**
1. Rebuild `.so`: `cd rust-analyzer-core && PYO3_PYTHON=$(which python3.12) cargo build --release`
2. Copy to package: `cp target/release/libcortex_extract.dylib python/cortex_extract/cortex_extract.cpython-312-darwin.so`
3. Write `tests/test_production_wiring.py` — for each wired analyzer:
   - Call `parse_*_file()` on a fixture file
   - Verify it returns the expected type (dict or tuple)
   - Verify `cortex_extract` is actually used (monkeypatch or flag check)
4. Run full differential test suite: `pytest tests/test_*_differential.py`

**Validation:** ✅ All analyzers produce correct output with cortex_extract active.

## Validation Criteria

- [ ] Phase 1: `_rust_accel.py` importable from all analyzer subprocesses
- [ ] Phase 2: go, rust, swift use cortex_extract when available, fallback works
- [ ] Phase 3: cplus, java, ts, csharp, php, delphi use cortex_extract with correct tuple mapping
- [ ] Phase 4: `tests/test_production_wiring.py` passes for all wired analyzers
- [ ] Existing differential tests still pass (no regression)
- [ ] `sync code` command works end-to-end on a multi-language project

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Tuple adapter element order mismatch | Medium | High (wrong data in graph) | Per-analyzer parity test before wiring |
| cortex_extract import fails in subprocess | Low | Low (silent fallback to Python) | Graceful try/except, one-time warning log |
| Dict payload schema drift (Rust ≠ Python) | Low | Medium | Differential tests catch this |
| ts_backend_analyzer has no Rust equivalent | N/A | None | Skip — stays pure Python |
| Performance regression from try/except overhead | Very Low | Negligible | Import is cached by Python module system |

## Files to Create/Modify

| File | Action |
|------|--------|
| `code-tiny/tools/common/_rust_accel.py` | **NEW**: shared import + feature detection |
| `code-tiny/tools/cplus/_rust_fallback.py` | **DELETE**: superseded by _rust_accel |
| `code-tiny/tools/go/go_analyzer.py` | Modify: add fast-path wrapper |
| `code-tiny/tools/rust/rust_analyzer.py` | Modify: add fast-path wrapper |
| `code-tiny/tools/swift/swift_analyzer.py` | Modify: add fast-path wrapper |
| `code-tiny/tools/cplus/cplus_analyzer.py` | Modify: add wrapper + dict→tuple adapter |
| `code-tiny/tools/java/java_analyzer.py` | Modify: add wrapper + adapter |
| `code-tiny/tools/ts/ts_analyzer.py` | Modify: add wrapper + adapter |
| `code-tiny/tools/csharp/csharp_analyzer.py` | Modify: add wrapper + adapter |
| `code-tiny/tools/php/php_analyzer.py` | Modify: add wrapper + adapter |
| `code-tiny/tools/delphi/delphi_analyzer.py` | Modify: add wrapper + adapter |
| `tests/test_production_wiring.py` | **NEW**: integration smoke test |

## Estimated Effort

**Low-Medium** — Phase 2 is trivial (3 files × 5 lines). Phase 3 is mechanical (6 adapters, each ~15 lines). Total ~3-4 hours including testing.
