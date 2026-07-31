#!/usr/bin/env python3
"""
Analyzer Pipeline Profiler for cortex-harness.

Answers the key question: "Is parsing actually the bottleneck,
or is it embedding / graph-write / something else?"

Measures each pipeline phase independently using the REAL analyzer
code paths (no reimplementation):

  1. SCAN     — file discovery (os.walk)
  2. PARSE    — tree-sitter C parse  (parser.parse)
  3. EXTRACT  — AST walk + dataclass build
  4. CACHE    — JSON cache write + read
  5. SEMANTIC — heuristic enrichment (regex, no ML)
  6. EMBED    — model inference (optional, --embed)

Also compares configurations:
  - Fresh parser per file  vs  Singleton parser (cplus pattern)
  - Sequential             vs  Parallel (ThreadPool / ProcessPool)

Usage:
  python profile_analyzer.py --target /path/to/code --language python
  python profile_analyzer.py --target . --language python --parallel 8
  python profile_analyzer.py --target . --language python --embed
  python profile_analyzer.py --target . --language python --full
  python profile_analyzer.py --list-languages

Output:
  - Console summary table
  - profile_results_<lang>_<timestamp>.json
"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import os
import sys
import time
import tracemalloc
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import concurrent.futures
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

# ─── Path setup (so imports like `tools.python.python_analyzer` work) ───
_HERE = Path(__file__).resolve().parent
_CODE_TINY = _HERE / "code-tiny"
if str(_CODE_TINY) not in sys.path:
    sys.path.insert(0, str(_CODE_TINY))


# ═══════════════════════════════════════════════════════════
#  Language registry
# ═══════════════════════════════════════════════════════════

@dataclass
class LangConfig:
    """Naming convention for each language analyzer.

    Handles three irregularities across analyzers:
    1. Parser getter name varies: _get_python_parser vs _get_parser vs _get_cpp_parser
    2. Parse function signature varies: parse_python_file(path, root) vs parse_c_family_file(path, root, is_cpp)
    3. Return type varies: tuple of dataclasses vs Dict[str, Any] (go/rust/swift)
    """
    module: str
    scan_fn: str
    get_parser_fn: str
    parse_full_fn: str
    parse_bytes_fn: str
    extensions: Tuple[str, ...]
    # For cplus: needs is_cpp detection per file
    needs_cpp_detection: bool = False
    # For go/rust/swift: scan takes optional selected_rel_paths
    scan_takes_selected: bool = False
    # For go/rust/swift: parse returns Dict not tuple
    returns_dict: bool = False


LANGUAGES: Dict[str, LangConfig] = {
    "python":   LangConfig("tools.python.python_analyzer",         "_scan_python_files",   "_get_python_parser",   "parse_python_file",   "_parse_file", (".py", ".pyi")),
    "java":     LangConfig("tools.java.java_analyzer",              "_scan_java_files",     "_get_java_parser",     "parse_java_file",     "_parse_file", (".java",)),
    "ts":       LangConfig("tools.ts.ts_analyzer",                  "_scan_ts_files",       "_get_ts_parser",       "parse_ts_file",       "_parse_file", (".ts", ".tsx")),
    "js":       LangConfig("tools.js.js_analyzer",                  "_scan_js_files",       "_get_js_parser",       "parse_js_file",       "_parse_file", (".js", ".jsx", ".mjs", ".cjs")),
    "go":       LangConfig("tools.go.go_analyzer",                  "_scan_go_files",       "_get_parser",          "parse_go_file",       "_parse_file", (".go",), scan_takes_selected=True, returns_dict=True),
    "csharp":   LangConfig("tools.csharp.csharp_analyzer",          "_scan_csharp_files",   "_get_csharp_parser",   "parse_csharp_file",   "_parse_file", (".cs",)),
    "kotlin":   LangConfig("tools.kotlin.kotlin_analyzer",          "_scan_kotlin_files",   "_get_kotlin_parser",   "parse_kotlin_file",   "_parse_file", (".kt", ".kts")),
    "rust":     LangConfig("tools.rust.rust_analyzer",              "_scan_rust_files",     "_get_parser",          "parse_rust_file",     "_parse_file", (".rs",), scan_takes_selected=True, returns_dict=True),
    "cplus":    LangConfig("tools.cplus.cplus_analyzer",            "_scan_c_family_files", "_get_cpp_parser",      "parse_c_family_file", "_parse_file", (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".c", ".h"), needs_cpp_detection=True),
    "php":      LangConfig("tools.php.php_analyzer",                "_scan_php_files",      "_get_php_parser",      "parse_php_file",      "_parse_file", (".php",)),
    "sql":      LangConfig("tools.sql.sql_analyzer",                "_scan_sql_files",      "_get_sql_parser",      "parse_sql_file",      "_parse_file", (".sql",)),
    "delphi":   LangConfig("tools.delphi.delphi_analyzer",          "_scan_delphi_files",   "_get_delphi_parser",   "parse_delphi_file",   "_parse_file", (".pas", ".dpr", ".dpk")),
    "swift":    LangConfig("tools.swift.swift_analyzer",             "_scan_swift_files",    "_get_parser",          "parse_swift_file",    "_parse_file", (".swift",), scan_takes_selected=True, returns_dict=True),
}


# ═══════════════════════════════════════════════════════════
#  Timing helpers
# ═══════════════════════════════════════════════════════════

class Timer:
    """Context-manager wall-clock + memory tracker."""
    def __init__(self, label: str):
        self.label = label
        self.wall: float = 0.0
        self.peak_mem_mb: float = 0.0
        self._t0: float = 0.0
        self._mem_snap1: Any = None

    def __enter__(self):
        gc.collect()
        self._t0 = time.perf_counter()
        tracemalloc.start()
        self._mem_snap1 = tracemalloc.take_snapshot()
        return self

    def __exit__(self, *exc):
        self.wall = time.perf_counter() - self._t0
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        try:
            diff = snap2.compare_to(self._mem_snap1, "lineno")
            self.peak_mem_mb = sum(stat.size_diff for stat in diff if stat.size_diff > 0) / (1024 * 1024)
        except Exception:
            self.peak_mem_mb = 0.0


def pctile(sorted_list: List[float], p: float) -> float:
    """Percentile of a sorted list."""
    if not sorted_list:
        return 0.0
    k = (len(sorted_list) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_list) - 1)
    if f == c:
        return sorted_list[f]
    return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)


def fmt_ms(seconds: float) -> str:
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}µs"
    if seconds < 1.0:
        return f"{seconds * 1000:.1f}ms"
    return f"{seconds:.2f}s"


def fmt_mb(mb: float) -> str:
    if mb < 1:
        return f"{mb * 1024:.0f}KB"
    return f"{mb:.1f}MB"


# ═══════════════════════════════════════════════════════════
#  Result containers
# ═══════════════════════════════════════════════════════════

@dataclass
class PhaseResult:
    name: str
    wall_seconds: float
    peak_mem_mb: float
    item_count: int = 0
    per_item_ms: List[float] = field(default_factory=list)
    notes: str = ""

    @property
    def items_per_sec(self) -> float:
        return self.item_count / self.wall_seconds if self.wall_seconds > 0 else 0

    def summary_line(self) -> str:
        p50 = pctile(sorted(self.per_item_ms), 50) if self.per_item_ms else 0
        p99 = pctile(sorted(self.per_item_ms), 99) if self.per_item_ms else 0
        rate = f"{self.items_per_sec:.0f} items/s" if self.item_count else ""
        per = f"p50={p50:.2f}ms p99={p99:.2f}ms" if self.per_item_ms else ""
        mem = f"mem={fmt_mb(self.peak_mem_mb)}" if self.peak_mem_mb else ""
        parts = [f"{fmt_ms(self.wall_seconds):>8s}", rate, per, mem]
        if self.notes:
            parts.append(self.notes)
        return "  ".join(p for p in parts if p)


# ═══════════════════════════════════════════════════════════
#  Stub heavy optional imports (torch, transformers, etc.)
#  so parse-only profiling works without ML deps installed.
# ═══════════════════════════════════════════════════════════

def _stub_optional_imports():
    """Inject fake modules for torch/transformers/requests etc.
    so the analyzer module loads even without ML deps installed.
    Only PARSING functions work — embedding/graph-write will fail
    if actually called (expected when profiling without those deps).
    """
    import types

    class _StubCallable:
        """Fake callable that returns stub instances for any access."""
        def __init__(self):
            self._stub = True
        def __call__(self, *a, **kw):
            return _StubCallable()
        def __getattr__(self, name):
            return _StubCallable()
        def __bool__(self):
            return False
        def __iter__(self):
            return iter([])
        def __contains__(self, item):
            return False

    class _StubModule(types.ModuleType):
        def __getattr__(self, name):
            if name in ("__all__", "__path__", "__file__"):
                return [] if name != "__file__" else ""
            return _StubCallable()

    for mod_name in (
        "torch", "torch.cuda", "torch.backends", "torch.backends.mps",
        "transformers",
        "neo4j", "neo4j.exceptions",
        "falkordb",
        "qdrant_client",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = _StubModule(mod_name)


# ═══════════════════════════════════════════════════════════
#  Module-level worker for ProcessPool compatibility
# ═══════════════════════════════════════════════════════════

def _worker_parse(task: Tuple) -> Any:
    """Picklable worker function for parallel parsing.
    task = (filepath, target_root, module_name, parse_fn_name, is_cpp_or_None)
    """
    # Inject stubs in child process (they don't inherit from parent)
    if "torch" not in sys.modules:
        _stub_optional_imports()
    fp, target, mod_name, parse_fn_name, is_cpp = task

    # Fallback: if C parser unavailable, force is_cpp=True for .c/.h files
    if is_cpp is False:
        try:
            import tree_sitter_c  # noqa: F401
        except ImportError:
            is_cpp = True  # treat as C++ to avoid RuntimeError

    mod = importlib.import_module(mod_name)
    fn = getattr(mod, parse_fn_name)
    if is_cpp is not None:
        return fn(fp, target, is_cpp)
    return fn(fp, target)


# ═══════════════════════════════════════════════════════════
#  Profiler
# ═══════════════════════════════════════════════════════════

class Profiler:
    def __init__(self, target: str, language: str, cache_dir: Optional[str] = None):
        self.target = os.path.abspath(target)
        self.language = language
        self.config = LANGUAGES[language]
        self.cache_dir = cache_dir or os.path.join(os.getcwd(), ".cache", "profile_tmp")
        self.results: List[PhaseResult] = []
        self.scanned_files: List[str] = []
        self.payloads: List[Dict[str, Any]] = []

        # Import the analyzer module.
        # NOTE: many analyzers import torch/transformers/requests at top-level
        # (the coupling problem identified in the prediction report).
        # We stub them out so parse-only profiling works without ML deps.
        _stub_optional_imports()
        try:
            self.mod = importlib.import_module(self.config.module)
        except ImportError as e:
            raise ImportError(
                f"Cannot import {self.config.module}: {e}\n"
                f"  Even with stubs, a parse-only dependency (tree_sitter) may be missing.\n"
                f"  Install with: pip install tree-sitter tree-sitter-languages tree-sitter-{language}"
            ) from e
        self.scan_fn = getattr(self.mod, self.config.scan_fn)
        self.get_parser_fn = getattr(self.mod, self.config.get_parser_fn)
        self.parse_full_fn = getattr(self.mod, self.config.parse_full_fn)
        # parse_bytes_fn is only used by _parse_bytes_for_file (legacy phase), so resolve lazily.
        try:
            self.parse_bytes_fn = getattr(self.mod, self.config.parse_bytes_fn)
        except AttributeError:
            self.parse_bytes_fn = None
        self._c_parser = None  # lazy: for cplus .c files
        # Per-file detail tracking for outlier analysis
        # {filepath: {ts_parse_ms, extract_ms, file_size_bytes, line_count, funcs, calls, classes}}
        self.file_details: Dict[str, Dict[str, Any]] = {}

    def _is_cpp_file(self, filepath: str) -> bool:
        """Detect C vs C++ for a file (cplus analyzer needs this).
        When tree_sitter_c isn't installed, treat everything as C++."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext in (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"):
            return True
        # For .c and .h files, try C parser; if unavailable, fall back to C++
        if not self._c_parser_available:
            return True
        return False

    @property
    def _c_parser_available(self) -> bool:
        """Check if the C parser is installed (cached)."""
        if not hasattr(self, "_c_parser_avail_cache"):
            if not self.config.needs_cpp_detection:
                self._c_parser_avail_cache = True
                return True
            try:
                import tree_sitter_c
                self._c_parser_avail_cache = True
            except ImportError:
                self._c_parser_avail_cache = False
        return self._c_parser_avail_cache

    def _get_parser_for_file(self, filepath: str):
        """Get the right parser for a given file (handles cplus C/C++ split)."""
        if self.config.needs_cpp_detection:
            if self._is_cpp_file(filepath):
                return self.get_parser_fn()  # _get_cpp_parser
            else:
                if self._c_parser is None:
                    self._c_parser = getattr(self.mod, "_get_c_parser")()
                return self._c_parser
        return self.get_parser_fn()

    def _parse_bytes_for_file(self, filepath: str):
        """Call _parse_file with correct signature per language."""
        if self.parse_bytes_fn is None:
            return None
        if self.config.needs_cpp_detection:
            is_cpp = self._is_cpp_file(filepath)
            return self.parse_bytes_fn(filepath, is_cpp)
        return self.parse_bytes_fn(filepath)

    def _parse_full_for_file(self, filepath: str):
        """Call parse_*_file with correct signature per language."""
        if self.config.needs_cpp_detection:
            is_cpp = self._is_cpp_file(filepath)
            return self.parse_full_fn(filepath, self.target, is_cpp)
        return self.parse_full_fn(filepath, self.target)

    # ── Phase 1: Scan ──────────────────────────────────────

    def phase_scan(self) -> PhaseResult:
        with Timer("SCAN") as t:
            if self.config.scan_takes_selected:
                self.scanned_files = self.scan_fn(self.target)
            else:
                self.scanned_files = self.scan_fn(self.target)
        r = PhaseResult("1. SCAN (file discovery)", t.wall, t.peak_mem_mb, len(self.scanned_files))
        self.results.append(r)
        return r

    # ── Phase 2: Parse (tree-sitter only, no extraction) ───

    def phase_parse_ts_only(self, singleton: bool = True) -> PhaseResult:
        label = "2a. TREE-SITTER PARSE ONLY (C parse, no extraction)"
        if singleton:
            label += " [singleton parser]"
        else:
            label += " [fresh parser per file]"
        # Some analyzers (e.g. ts) require language_name arg to get_parser; skip parse-only for those.
        try:
            self.get_parser_fn()
        except TypeError:
            r = PhaseResult(label + " [skipped: parser requires args]", 0.0, 0.0, len(self.scanned_files))
            self.results.append(r)
            return r

        per_file: List[float] = []
        total_bytes = 0
        # For non-cplus languages, the singleton parser is straightforward.
        # For cplus, we cache both C and C++ parsers (lazy: C parser may not be installed).
        cpp_parser_singleton = self.get_parser_fn() if singleton else None
        c_parser_singleton = None  # lazy: initialized on first .c file

        def _get_c_parser_lazy():
            nonlocal c_parser_singleton
            if c_parser_singleton is None and singleton:
                try:
                    c_parser_singleton = getattr(self.mod, "_get_c_parser")()
                except Exception:
                    c_parser_singleton = False  # mark as unavailable
            return c_parser_singleton if c_parser_singleton else cpp_parser_singleton

        with Timer(label) as t:
            for fp in self.scanned_files:
                t0 = time.perf_counter()
                if self.config.needs_cpp_detection:
                    is_cpp = self._is_cpp_file(fp)
                    if singleton:
                        parser = cpp_parser_singleton if is_cpp else _get_c_parser_lazy()
                    else:
                        parser = self.get_parser_fn() if is_cpp else _get_c_parser_lazy()
                else:
                    parser = self.get_parser_fn() if singleton else cpp_parser_singleton
                with open(fp, "rb") as fh:
                    source = fh.read()
                total_bytes += len(source)
                _ = parser.parse(source)
                elapsed = (time.perf_counter() - t0) * 1000
                per_file.append(elapsed)
                # Track per-file detail
                if fp not in self.file_details:
                    self.file_details[fp] = {}
                self.file_details[fp]["ts_parse_ms"] = elapsed
                self.file_details[fp]["file_size_bytes"] = len(source)

        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(self.scanned_files), per_file,
                        f"total={fmt_mb(total_bytes / (1024*1024))} source")
        self.results.append(r)
        return r

    # ── Phase 3: Full parse + extract (dataclass build) ────

    def phase_parse_full(self) -> PhaseResult:
        label = "2b. FULL PARSE + EXTRACT (tree-sitter + AST walk + dataclass)"
        per_file: List[float] = []
        self.payloads = []

        with Timer(label) as t:
            for fp in self.scanned_files:
                t0 = time.perf_counter()
                result = self._parse_full_for_file(fp)
                elapsed = (time.perf_counter() - t0) * 1000
                self.payloads.append(result)
                per_file.append(elapsed)

                # Track per-file detail
                if fp not in self.file_details:
                    self.file_details[fp] = {}
                d = self.file_details[fp]
                d["extract_ms"] = elapsed
                fsize = d.get("file_size_bytes", 0)
                if not fsize:
                    try:
                        fsize = os.path.getsize(fp)
                        d["file_size_bytes"] = fsize
                    except OSError:
                        fsize = 0

                # Count extracted items for this file
                if self.config.returns_dict and isinstance(result, dict):
                    d["funcs"] = len(result.get("functions", []))
                    d["calls"] = len(result.get("calls", []))
                    d["classes"] = len(result.get("classes", result.get("types", [])))
                elif isinstance(result, tuple):
                    d["funcs"] = len(result[0]) if len(result) > 0 else 0
                    d["calls"] = len(result[1]) if len(result) > 1 else 0
                    d["classes"] = len(result[2]) if len(result) > 2 else 0

                # Estimate line count from file size (cheaper than reading)
                try:
                    with open(fp, "rb") as fh:
                        d["line_count"] = fh.read().count(b"\n") + 1
                except OSError:
                    d["line_count"] = 0

        # Count extracted items — handle both tuple and dict returns
        if self.config.returns_dict:
            total_funcs = sum(len(p.get("functions", [])) for p in self.payloads)
            total_calls = sum(len(p.get("calls", [])) for p in self.payloads)
            total_classes = sum(len(p.get("classes", p.get("types", []))) for p in self.payloads)
        else:
            total_funcs = sum(len(p[0]) for p in self.payloads) if self.payloads else 0
            total_calls = sum(len(p[1]) for p in self.payloads) if self.payloads else 0
            total_classes = sum(len(p[2]) for p in self.payloads) if self.payloads else 0
        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(self.scanned_files), per_file,
                        f"funcs={total_funcs} calls={total_calls} classes={total_classes}")
        self.results.append(r)
        return r

    # ── Phase 4: Payload → dict (asdict serialization) ─────

    def phase_to_dicts(self) -> PhaseResult:
        label = "3. PAYLOAD→DICT (asdict serialization)"
        per_file: List[float] = []
        dict_payloads: List[Dict[str, Any]] = []

        with Timer(label) as t:
            for payload in self.payloads:
                t0 = time.perf_counter()
                if isinstance(payload, dict):
                    # go/rust/swift already return dicts — no serialization needed
                    dict_payloads.append(payload)
                elif isinstance(payload, tuple):
                    # tuple-returning analyzers (python, java, ts, etc.)
                    d = {}
                    field_names = ("functions", "calls", "classes", "namespaces",
                                   "relations", "file_def")
                    # cplus returns 11 items; standard analyzers return 6
                    extra_names = ("function_types", "fields", "aliases", "templates",
                                   "using_namespaces", "using_imports", "includes",
                                   "macros", "parse_meta")
                    all_names = field_names + extra_names
                    for idx, item in enumerate(payload):
                        key = all_names[idx] if idx < len(all_names) else f"item_{idx}"
                        if isinstance(item, list):
                            d[key] = [asdict(x) if hasattr(x, "__dataclass_fields__") else
                                      (x.__dict__ if hasattr(x, "__dict__") else x) for x in item]
                        elif hasattr(item, "__dataclass_fields__"):
                            d[key] = asdict(item)
                        elif hasattr(item, "__dict__"):
                            d[key] = item.__dict__
                        else:
                            d[key] = item
                    dict_payloads.append(d)
                else:
                    dict_payloads.append({"raw": str(payload)})
                per_file.append((time.perf_counter() - t0) * 1000)

        self._dict_payloads = dict_payloads
        # If returns_dict, serialization is effectively zero — note it
        note = "(already dict — no asdict overhead)" if self.config.returns_dict else ""
        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(dict_payloads), per_file, note)
        self.results.append(r)
        return r

    # ── Phase 5: Cache write (JSON) ────────────────────────

    def phase_cache_write(self) -> PhaseResult:
        label = "4. CACHE WRITE (JSON serialize + atomic write)"
        from tools.common.analyzer_cache import write_parse_cache, file_signature, safe_cache_root
        cache_root = safe_cache_root(self.cache_dir, f"{self.language}_profile", project_root=self.target)
        parse_cache_root = os.path.join(cache_root, "parse")
        os.makedirs(parse_cache_root, exist_ok=True)

        per_file: List[float] = []
        with Timer(label) as t:
            for fp, payload in zip(self.scanned_files, self._dict_payloads):
                sig = file_signature(fp)
                rel = os.path.relpath(fp, self.target)
                t0 = time.perf_counter()
                write_parse_cache(parse_cache_root, rel, sig, payload)
                per_file.append((time.perf_counter() - t0) * 1000)

        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(self._dict_payloads), per_file)
        self.results.append(r)
        return r

    # ── Phase 6: Cache read ────────────────────────────────

    def phase_cache_read(self) -> PhaseResult:
        label = "5. CACHE READ (JSON load + signature check)"
        from tools.common.analyzer_cache import load_parse_cache, file_signature, safe_cache_root
        cache_root = safe_cache_root(self.cache_dir, f"{self.language}_profile", project_root=self.target)
        parse_cache_root = os.path.join(cache_root, "parse")

        per_file: List[float] = []
        hits = 0
        with Timer(label) as t:
            for fp in self.scanned_files:
                sig = file_signature(fp)
                rel = os.path.relpath(fp, self.target)
                t0 = time.perf_counter()
                data = load_parse_cache(parse_cache_root, rel, sig)
                per_file.append((time.perf_counter() - t0) * 1000)
                if data:
                    hits += 1

        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(self.scanned_files), per_file,
                        f"hits={hits}/{len(self.scanned_files)}")
        self.results.append(r)
        return r

    # ── Phase 7: Semantic enrichment ───────────────────────

    def phase_semantic(self) -> PhaseResult:
        label = "6. SEMANTIC ENRICHMENT (regex heuristics, no ML)"
        try:
            from tools.common.semantic_inference import SemanticInferenceEngine
        except Exception as e:
            r = PhaseResult(label, 0, 0, 0, notes=f"SKIPPED: {e}")
            self.results.append(r)
            return r

        engine = SemanticInferenceEngine()
        all_funcs: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []
        for d in self._dict_payloads:
            all_funcs.extend(d["functions"])
            all_calls.extend(d["calls"])

        with Timer(label) as t:
            engine.enrich_corpus(all_funcs, all_calls)

        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(all_funcs),
                        notes=f"enriched {len(all_funcs)} funcs")
        self.results.append(r)
        return r

    # ── Phase 8: Embedding inference ───────────────────────

    def phase_embedding(self, model_name: str, batch_size: int) -> PhaseResult:
        label = f"7. EMBEDDING INFERENCE (model={model_name}, batch={batch_size})"
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as e:
            r = PhaseResult(label, 0, 0, 0, notes=f"SKIPPED: {e}")
            self.results.append(r)
            return r

        texts: List[str] = []
        for d in self._dict_payloads:
            for func in d["functions"]:
                texts.append(func.get("note") or func.get("code") or func.get("name", ""))

        if not texts:
            r = PhaseResult(label, 0, 0, 0, notes="no texts to embed")
            self.results.append(r)
            return r

        # Resolve device
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
        except Exception:
            pass

        # Load model
        t_load_start = time.perf_counter()
        model = SentenceTransformer(model_name, device=device,
                                    trust_remote_code="jina" in model_name.lower())
        model_load_secs = time.perf_counter() - t_load_start

        # Embed
        with Timer(label) as t:
            vectors = model.encode(
                texts,
                batch_size=max(1, batch_size),
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )

        r = PhaseResult(label, t.wall, t.peak_mem_mb, len(texts),
                        notes=f"model_load={fmt_ms(model_load_secs)} device={device} "
                              f"dim={len(vectors[0]) if len(vectors) else '?'}")
        self.results.append(r)
        return r

    # ── Phase 9: Parallel parse comparison ─────────────────

    def phase_parallel(self, workers: int, pool_type: str = "thread") -> PhaseResult:
        """Parallel parse benchmark with streaming (low memory).
        Results are counted then discarded — no payload retention.
        This avoids OOM on large repos (the production 'load all payloads
        into memory' pattern at python_analyzer.py:1390 is intentionally
        NOT replicated here).
        """
        label = f"8. PARALLEL FULL PARSE ({pool_type}pool, {workers} workers)"
        target = self.target
        files = self.scanned_files
        mod_name = self.config.module
        parse_fn_name = self.config.parse_full_fn
        needs_cpp = self.config.needs_cpp_detection

        # Build picklable tasks
        tasks = []
        for fp in files:
            if needs_cpp:
                ext = os.path.splitext(fp)[1].lower()
                is_cpp = ext in (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")
                tasks.append((fp, target, mod_name, parse_fn_name, is_cpp))
            else:
                tasks.append((fp, target, mod_name, parse_fn_name, None))

        total_funcs = 0
        processed = 0
        peak_count = 0  # max results in flight at any time

        executor_cls = ThreadPoolExecutor if pool_type == "thread" else ProcessPoolExecutor

        # Use chunked submission to bound memory:
        # submit max(in_flight) tasks, then as each completes submit next.
        # This caps concurrent payloads in memory.
        max_in_flight = workers * 2  # small buffer

        with Timer(label) as t:
            with executor_cls(max_workers=workers) as pool:
                task_iter = iter(tasks)
                in_flight = {}

                # Prime the pump
                for _ in range(min(max_in_flight, len(tasks))):
                    try:
                        task = next(task_iter)
                    except StopIteration:
                        break
                    in_flight[pool.submit(_worker_parse, task)] = task

                while in_flight:
                    # Wait for ANY future to complete
                    done, _ = concurrent.futures.wait(
                        in_flight, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for future in done:
                        task = in_flight.pop(future)
                        result = future.result()
                        processed += 1

                        # Count then DISCARD — no retention
                        if isinstance(result, dict):
                            total_funcs += len(result.get("functions", []))
                        elif isinstance(result, tuple) and len(result) > 0:
                            total_funcs += len(result[0])
                        del result  # explicit free

                        # Submit next task to keep pipeline full
                        try:
                            next_task = next(task_iter)
                            in_flight[pool.submit(_worker_parse, next_task)] = next_task
                        except StopIteration:
                            pass

                    peak_count = max(peak_count, len(in_flight))

                    # Progress
                    if processed % 500 == 0 and processed > 0:
                        elapsed = time.perf_counter() - t._t0
                        rate = processed / elapsed if elapsed > 0 else 0
                        print(f"  [parallel] {processed}/{len(tasks)} files "
                              f"({rate:.0f} files/s, {len(in_flight)} in-flight)")

        r = PhaseResult(label, t.wall, t.peak_mem_mb, processed,
                        notes=f"funcs={total_funcs}, max_in_flight={peak_count}, "
                              f"streaming=True")
        self.results.append(r)
        return r

    # ── Report ─────────────────────────────────────────────

    def print_report(self):
        print("\n" + "=" * 100)
        print(f"  PROFILING REPORT: {self.language} analyzer  |  target: {self.target}")
        print(f"  files: {len(self.scanned_files)}  |  date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)

        # Summary table
        print(f"\n{'PHASE':<55} {'WALL':>8}  {'RATE':>14}  {'PER-FILE':>22}  {'MEM':>10}  NOTES")
        print("-" * 140)

        for r in self.results:
            p50 = pctile(sorted(r.per_item_ms), 50) if r.per_item_ms else 0
            p99 = pctile(sorted(r.per_item_ms), 99) if r.per_item_ms else 0
            rate = f"{r.items_per_sec:.0f}/s" if r.item_count else ""
            per = f"p50={p50:.2f}ms p99={p99:.2f}ms" if r.per_item_ms else ""
            mem = fmt_mb(r.peak_mem_mb) if r.peak_mem_mb else ""
            print(f"{r.name:<55} {fmt_ms(r.wall_seconds):>8}  {rate:>14}  {per:>22}  {mem:>10}  {r.notes}")

        print("-" * 140)

        # Key insights
        print("\n📊 KEY INSIGHTS:")
        parse_wall = sum(r.wall_seconds for r in self.results if "PARSE" in r.name or "EXTRACT" in r.name)
        embed_wall = sum(r.wall_seconds for r in self.results if "EMBED" in r.name)
        cache_wall = sum(r.wall_seconds for r in self.results if "CACHE" in r.name)
        sem_wall = sum(r.wall_seconds for r in self.results if "SEMANTIC" in r.name)
        total_wall = parse_wall + embed_wall + cache_wall + sem_wall

        if total_wall > 0:
            print(f"   Parse+Extract:  {fmt_ms(parse_wall):>8}  ({parse_wall/total_wall*100:5.1f}% of measured)")
            print(f"   Cache I/O:      {fmt_ms(cache_wall):>8}  ({cache_wall/total_wall*100:5.1f}%)")
            print(f"   Semantic:       {fmt_ms(sem_wall):>8}  ({sem_wall/total_wall*100:5.1f}%)")
            print(f"   Embedding:      {fmt_ms(embed_wall):>8}  ({embed_wall/total_wall*100:5.1f}%)")
            print(f"   ─────────────────────────────")
            print(f"   TOTAL:          {fmt_ms(total_wall):>8}")

            if embed_wall > 0:
                ratio = embed_wall / parse_wall if parse_wall > 0 else float('inf')
                print(f"\n   ⚡ Embedding is {ratio:.1f}x SLOWER than parsing")
                print(f"   → Rewriting parser in Rust would save at most {parse_wall/total_wall*100:.0f}% of total time")
            elif parse_wall > 0:
                print(f"\n   ⚡ Parsing dominates ({parse_wall/total_wall*100:.0f}%) — Rust rewrite could help")
        else:
            print("   (no phases measured)")

        print("\n" + "=" * 100 + "\n")

    # ── Outlier file analysis ─────────────────────────────

    def print_outliers(self, top_n: int = 20):
        """Print the slowest files and size/complexity distribution."""
        if not self.file_details:
            print("\n⚠️  No per-file details collected (run parse phases first).\n")
            return

        # Merge details into sortable list
        rows = []
        total_extract = 0.0
        for fp, d in self.file_details.items():
            extract_ms = d.get("extract_ms", 0)
            total_extract += extract_ms
            rows.append({
                "path": fp,
                "extract_ms": extract_ms,
                "ts_parse_ms": d.get("ts_parse_ms", 0),
                "size_bytes": d.get("file_size_bytes", 0),
                "lines": d.get("line_count", 0),
                "funcs": d.get("funcs", 0),
                "calls": d.get("calls", 0),
                "classes": d.get("classes", 0),
            })

        # Sort by extract_ms descending
        rows.sort(key=lambda r: r["extract_ms"], reverse=True)

        # ── Top N slowest files ──
        print("\n" + "=" * 130)
        print(f"  🔍 OUTLIER FILES — Top {top_n} slowest by FULL PARSE + EXTRACT")
        print("=" * 130)

        # Compute cumulative percentage
        cum_ms = 0.0
        print(f"\n  {'#':>3}  {'TIME':>8}  {'%':>5}  {'CUM%':>5}  {'LINES':>7}  {'SIZE':>8}  {'FUNCS':>5}  {'CALLS':>5}  {'CLASSES':>7}  {'ms/1k LOC':>9}  FILE")
        print("  " + "─" * 126)

        for i, r in enumerate(rows[:top_n], 1):
            cum_ms += r["extract_ms"]
            pct_val = r["extract_ms"] / total_extract * 100 if total_extract > 0 else 0
            cum_pct = cum_ms / total_extract * 100 if total_extract > 0 else 0
            lines = r["lines"]
            ms_per_kloc = (r["extract_ms"] / lines * 1000) if lines > 0 else 0
            rel = os.path.relpath(r["path"], self.target)
            if len(rel) > 60:
                rel = "..." + rel[-57:]
            print(f"  {i:>3}  {fmt_ms(r['extract_ms']/1000):>8}  {pct_val:4.1f}%  {cum_pct:4.1f}%  "
                  f"{lines:>7}  {fmt_mb(r['size_bytes']/(1024*1024)):>8}  "
                  f"{r['funcs']:>5}  {r['calls']:>5}  {r['classes']:>7}  "
                  f"{ms_per_kloc:>8.1f}  {rel}")

        # ── Pareto analysis ──
        print(f"\n  📊 PARETO ANALYSIS (what % of files cause what % of time):")
        thresholds = [1, 5, 10, 20, 50]
        for pct_t in thresholds:
            n_files = max(1, int(len(rows) * pct_t / 100))
            time_for_n = sum(r["extract_ms"] for r in rows[:n_files])
            time_pct = time_for_n / total_extract * 100 if total_extract > 0 else 0
            print(f"   Top {pct_t:>2}% of files ({n_files:>4} files) = {time_pct:5.1f}% of parse time")

        # ── Size distribution ──
        sizes = sorted(r["size_bytes"] for r in rows)
        lines_all = sorted(r["lines"] for r in rows)
        p99_idx = min(int(len(sizes) * 0.99), len(sizes) - 1)
        p99_lines_idx = min(int(len(lines_all) * 0.99), len(lines_all) - 1)
        print(f"\n  📏 FILE SIZE DISTRIBUTION:")
        print(f"   Size:   min={fmt_mb(sizes[0]/(1024*1024)):>8}  "
              f"p50={fmt_mb(sizes[len(sizes)//2]/(1024*1024)):>8}  "
              f"p99={fmt_mb(sizes[p99_idx]/(1024*1024)):>8}  "
              f"max={fmt_mb(sizes[-1]/(1024*1024)):>8}")
        print(f"   Lines:  min={lines_all[0]:>8}  "
              f"p50={lines_all[len(lines_all)//2]:>8}  "
              f"p99={lines_all[p99_lines_idx]:>8}  "
              f"max={lines_all[-1]:>8}")

        # ── Speed vs size correlation ──
        # Group by size buckets and show avg parse time
        print(f"\n  ⚡ PARSE TIME vs FILE SIZE (avg ms per size bucket):")
        buckets = [
            ("< 100 lines",     [r for r in rows if r["lines"] < 100]),
            ("100-500 lines",   [r for r in rows if 100 <= r["lines"] < 500]),
            ("500-1K lines",    [r for r in rows if 500 <= r["lines"] < 1000]),
            ("1K-5K lines",     [r for r in rows if 1000 <= r["lines"] < 5000]),
            ("5K-10K lines",    [r for r in rows if 5000 <= r["lines"] < 10000]),
            ("10K+ lines",      [r for r in rows if r["lines"] >= 10000]),
        ]
        print(f"   {'BUCKET':<18} {'COUNT':>6} {'AVG MS':>8} {'MAX MS':>8} {'AVG ms/kLOC':>12}")
        print(f"   {'─'*56}")
        for label, items in buckets:
            if not items:
                continue
            avg_ms = sum(r["extract_ms"] for r in items) / len(items)
            max_ms = max(r["extract_ms"] for r in items)
            total_loc = sum(r["lines"] for r in items)
            avg_per_kloc = (avg_ms / (total_loc / len(items)) * 1000) if total_loc > 0 else 0
            print(f"   {label:<18} {len(items):>6} {avg_ms:>8.1f} {max_ms:>8.1f} {avg_per_kloc:>11.1f}")

        print("\n" + "=" * 130 + "\n")

    def save_outliers_json(self, top_n: int = 50):
        """Save outlier file details to a separate JSON."""
        if not self.file_details:
            return None

        rows = []
        total_extract = 0.0
        for fp, d in self.file_details.items():
            extract_ms = d.get("extract_ms", 0)
            total_extract += extract_ms
            rows.append({
                "file": fp,
                "extract_ms": round(extract_ms, 3),
                "ts_parse_ms": round(d.get("ts_parse_ms", 0), 3),
                "file_size_bytes": d.get("file_size_bytes", 0),
                "line_count": d.get("line_count", 0),
                "funcs": d.get("funcs", 0),
                "calls": d.get("calls", 0),
                "classes": d.get("classes", 0),
            })

        rows.sort(key=lambda r: r["extract_ms"], reverse=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"profile_outliers_{self.language}_{ts}.json"
        data = {
            "language": self.language,
            "target": self.target,
            "file_count": len(self.scanned_files),
            "total_extract_ms": round(total_extract, 3),
            "timestamp": ts,
            "top_slowest": rows[:top_n],
            "all_files": rows,
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        print(f"💾 Outlier details saved to: {filename}\n")
        return filename

    def save_json(self):
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"profile_results_{self.language}_{ts}.json"
        data = {
            "language": self.language,
            "target": self.target,
            "file_count": len(self.scanned_files),
            "timestamp": ts,
            "phases": [
                {
                    "name": r.name,
                    "wall_seconds": round(r.wall_seconds, 6),
                    "peak_mem_mb": round(r.peak_mem_mb, 2),
                    "item_count": r.item_count,
                    "per_item_ms_p50": round(pctile(sorted(r.per_item_ms), 50), 4) if r.per_item_ms else None,
                    "per_item_ms_p90": round(pctile(sorted(r.per_item_ms), 90), 4) if r.per_item_ms else None,
                    "per_item_ms_p99": round(pctile(sorted(r.per_item_ms), 99), 4) if r.per_item_ms else None,
                    "per_item_ms_max": round(max(r.per_item_ms), 4) if r.per_item_ms else None,
                    "notes": r.notes,
                }
                for r in self.results
            ],
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        print(f"💾 Detailed results saved to: {filename}\n")
        return filename


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Profile the cortex-harness analyzer pipeline to find bottlenecks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic profile (scan + parse + extract + cache)
  python profile_analyzer.py --target /path/to/repo --language python

  # Full profile including semantic + embedding
  python profile_analyzer.py --target /path/to/repo --language python --full

  # Compare sequential vs parallel parsing
  python profile_analyzer.py --target /path/to/repo --language python --parallel 8

  # Just embedding benchmark
  python profile_analyzer.py --target /path/to/repo --language python --embed
        """,
    )
    parser.add_argument("--target", "-t", required=False, default=".",
                        help="Directory of source code to analyze")
    parser.add_argument("--language", "-l", required=False,
                        choices=list(LANGUAGES.keys()),
                        help="Which language analyzer to profile (required unless --list-languages)")
    parser.add_argument("--parallel", "-p", type=int, default=0,
                        help="Also test parallel parsing with N workers (0=skip)")
    parser.add_argument("--pool-type", choices=["thread", "process"], default="thread",
                        help="Pool type for --parallel (default: thread)")
    parser.add_argument("--embed", action="store_true",
                        help="Benchmark embedding model inference")
    parser.add_argument("--embed-model", default="jinaai/jina-embeddings-v3",
                        help="Embedding model name (default: jinaai/jina-embeddings-v3)")
    parser.add_argument("--embed-batch", type=int, default=8,
                        help="Embedding batch size (default: 8)")
    parser.add_argument("--full", action="store_true",
                        help="Run ALL phases including semantic + embedding")
    parser.add_argument("--no-cache-write", action="store_true",
                        help="Skip cache write/read phases")
    parser.add_argument("--list-languages", action="store_true",
                        help="List supported languages and exit")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N files (for quick testing)")
    parser.add_argument("--top-files", type=int, default=20,
                        help="Number of slowest files to show in outlier analysis (default: 20)")
    parser.add_argument("--outliers-only", action="store_true",
                        help="Skip timing phases, load existing outliers JSON and re-display")

    args = parser.parse_args()

    if args.list_languages:
        print("Supported languages:")
        for lang, cfg in LANGUAGES.items():
            print(f"  {lang:12s}  exts={cfg.extensions}  module={cfg.module}")
        return 0

    if not args.language:
        parser.error("--language is required (unless using --list-languages)")

    if not os.path.isdir(args.target):
        print(f"ERROR: target directory not found: {args.target}", file=sys.stderr)
        return 1

    # Build profiler
    try:
        prof = Profiler(args.target, args.language)
    except Exception as e:
        print(f"ERROR: failed to import analyzer for '{args.language}': {e}", file=sys.stderr)
        print(f"  Make sure tree-sitter dependencies are installed.", file=sys.stderr)
        return 1

    print(f"\n🔧 Profiling {args.language} analyzer on: {args.target}")

    # ── Run phases ──
    # Phase 1: Scan
    prof.phase_scan()
    if not prof.scanned_files:
        print(f"\n⚠️  No {args.language} files found under {args.target}")
        print("    Try a different --target or --language.")
        return 0

    if args.limit > 0:
        prof.scanned_files = prof.scanned_files[:args.limit]
        print(f"   (limited to first {len(prof.scanned_files)} files)")

    print(f"   Found {len(prof.scanned_files)} files\n")

    # Phase 2: Parse (tree-sitter only, singleton)
    prof.phase_parse_ts_only(singleton=True)

    # Phase 2b: Full parse + extract
    prof.phase_parse_full()

    # Phase 3: Payload → dict
    prof.phase_to_dicts()

    # Phase 4-5: Cache
    if not args.no_cache_write:
        prof.phase_cache_write()
        prof.phase_cache_read()

    # Phase 6: Semantic enrichment
    if args.full or args.embed:
        prof.phase_semantic()

    # Phase 7: Embedding
    if args.embed or args.full:
        prof.phase_embedding(args.embed_model, args.embed_batch)

    # Phase 8: Parallel comparison
    if args.parallel > 0:
        prof.phase_parallel(args.parallel, args.pool_type)

    # ── Report ──
    prof.print_report()
    prof.save_json()

    # ── Outlier analysis ──
    prof.print_outliers(top_n=args.top_files)
    prof.save_outliers_json(top_n=max(args.top_files, 50))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
