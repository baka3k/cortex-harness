#!/usr/bin/env python3
"""
Profile Results Analyzer for cortex-harness.

Reads one or more `profile_results_*.json` files (output of profile_analyzer.py)
and produces a deep bottleneck analysis:

  - Phase breakdown with derived metrics (% of total, throughput)
  - Bottleneck ranking (which phase dominates)
  - Tail-latency detection (p99 >> p50 → outlier files)
  - Parallel speedup analysis (sequential vs parallel)
  - Rust-vs-Python ROI estimate per phase
  - Cross-run comparison (e.g. different languages / configs)
  - Actionable recommendations with priority

Usage:
  python analyze_profile.py profile_results_python_*.json
  python analyze_profile.py profile_results_python_*.json --compare
  python analyze_profile.py profile_results_java_*.json --rust-roi
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════

# Phases known to be CPU-bound parsing (candidates for Rust)
PARSE_PHASES = {"TREE-SITTER PARSE", "FULL PARSE + EXTRACT", "PARALLEL FULL PARSE"}

# Phases that are I/O bound (disk / network) — Rust won't help
IO_PHASES = {"CACHE WRITE", "CACHE READ", "SCAN"}

# Phases that are ML / model inference — must stay Python
ML_PHASES = {"EMBEDDING INFERENCE"}

# Phases that are regex/heuristic — Rust could help but marginal
HEURISTIC_PHASES = {"SEMANTIC ENRICHMENT", "PAYLOAD"}


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


def pct(numerator: float, denominator: float) -> str:
    if denominator == 0:
        return "  -  "
    return f"{numerator / denominator * 100:5.1f}%"


def bar(percent: float, width: int = 30) -> str:
    """ASCII bar chart for percentage."""
    filled = int(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def tail_ratio(p50: Optional[float], p99: Optional[float]) -> Optional[float]:
    """How much worse is p99 vs p50? High ratio = outlier files."""
    if p50 is None or p99 is None or p50 == 0:
        return None
    return p99 / p50


# ═══════════════════════════════════════════════════════════
#  Phase classification
# ═══════════════════════════════════════════════════════════

@dataclass
class PhaseInfo:
    name: str
    wall: float
    peak_mem_mb: float
    item_count: int
    p50: Optional[float]
    p90: Optional[float]
    p99: Optional[float]
    p_max: Optional[float]
    notes: str

    # Derived (computed in analyze())
    pct_of_total: float = 0.0
    category: str = ""           # "PARSE" | "IO" | "ML" | "HEURISTIC" | "SERIAL"
    rust_candidate: bool = False
    _seq_wall: Optional[float] = None  # for parallel comparison

    @property
    def throughput(self) -> float:
        return self.item_count / self.wall if self.wall > 0 else 0

    @property
    def is_parallel(self) -> bool:
        return "PARALLEL" in self.name

    @property
    def has_tail(self) -> bool:
        ratio = tail_ratio(self.p50, self.p99)
        return ratio is not None and ratio > 5  # p99 is 5x p50

    def classify(self):
        upper = self.name.upper()
        if "PARALLEL" in upper:
            self.category = "PARALLEL"
            self.rust_candidate = True
        elif "TREE-SITTER PARSE" in upper and "FULL" not in upper:
            self.category = "PARSE_C"      # pure C parse
            self.rust_candidate = False     # already C — won't help
        elif "FULL PARSE" in upper or "EXTRACT" in upper:
            self.category = "PARSE_PY"     # Python AST walk
            self.rust_candidate = True
        elif any(k in upper for k in ("CACHE WRITE", "CACHE READ")):
            self.category = "IO"
            self.rust_candidate = False
        elif "SCAN" in upper:
            self.category = "IO"
            self.rust_candidate = False
        elif "EMBED" in upper:
            self.category = "ML"
            self.rust_candidate = False
        elif "SEMANTIC" in upper:
            self.category = "HEURISTIC"
            self.rust_candidate = True   # marginal
        elif "PAYLOAD" in upper or "DICT" in upper:
            self.category = "SERIALIZE"
            self.rust_candidate = True   # marginal
        else:
            self.category = "OTHER"
            self.rust_candidate = False


# ═══════════════════════════════════════════════════════════
#  Analysis
# ═══════════════════════════════════════════════════════════

@dataclass
class AnalysisResult:
    filepath: str
    language: str
    target: str
    file_count: int
    phases: List[PhaseInfo] = field(default_factory=list)
    total_measured: float = 0.0
    parse_total: float = 0.0      # parse + extract
    io_total: float = 0.0
    ml_total: float = 0.0
    heuristic_total: float = 0.0
    serialize_total: float = 0.0
    bottleneck_phase: Optional[PhaseInfo] = None
    findings: List[str] = field(default_factory=list)
    recommendations: List[Tuple[str, str, str]] = field(default_factory=list)  # (priority, title, detail)

    @property
    def has_embedding(self) -> bool:
        return self.ml_total > 0

    @property
    def has_parallel(self) -> bool:
        return any(p.is_parallel for p in self.phases)

    @property
    def parse_pct(self) -> float:
        return self.parse_total / self.total_measured * 100 if self.total_measured > 0 else 0

    @property
    def embed_pct(self) -> float:
        return self.ml_total / self.total_measured * 100 if self.total_measured > 0 else 0


def load_json(filepath: str) -> AnalysisResult:
    with open(filepath) as f:
        data = json.load(f)

    result = AnalysisResult(
        filepath=filepath,
        language=data.get("language", "?"),
        target=data.get("target", "?"),
        file_count=data.get("file_count", 0),
    )

    for pd in data.get("phases", []):
        pi = PhaseInfo(
            name=pd["name"],
            wall=pd["wall_seconds"],
            peak_mem_mb=pd.get("peak_mem_mb", 0),
            item_count=pd.get("item_count", 0),
            p50=pd.get("per_item_ms_p50"),
            p90=pd.get("per_item_ms_p90"),
            p99=pd.get("per_item_ms_p99"),
            p_max=pd.get("per_item_ms_max"),
            notes=pd.get("notes", ""),
        )
        pi.classify()
        result.phases.append(pi)

    return result


def analyze(result: AnalysisResult) -> AnalysisResult:
    """Compute derived metrics, find bottlenecks, generate findings & recommendations."""
    phases = result.phases

    # Split parallel from sequential for fair total
    seq_phases = [p for p in phases if not p.is_parallel]
    parallel_phases = [p for p in phases if p.is_parallel]

    result.total_measured = sum(p.wall for p in seq_phases)
    result.parse_total = sum(p.wall for p in seq_phases if p.category in ("PARSE_C", "PARSE_PY"))
    result.io_total = sum(p.wall for p in seq_phases if p.category == "IO")
    result.ml_total = sum(p.wall for p in seq_phases if p.category == "ML")
    result.heuristic_total = sum(p.wall for p in seq_phases if p.category == "HEURISTIC")
    result.serialize_total = sum(p.wall for p in seq_phases if p.category == "SERIALIZE")

    # Pct of total per phase
    for p in phases:
        p.pct_of_total = p.wall / result.total_measured * 100 if result.total_measured > 0 else 0

    # Find bottleneck
    if seq_phases:
        result.bottleneck_phase = max(seq_phases, key=lambda p: p.wall)

    # ── Findings ──

    # 1. Bottleneck identification
    if result.bottleneck_phase:
        bp = result.bottleneck_phase
        cat_name = {
            "PARSE_C": "tree-sitter C parse",
            "PARSE_PY": "Python AST walk + extraction",
            "IO": "disk I/O",
            "ML": "model inference",
            "HEURISTIC": "regex heuristics",
            "SERIALIZE": "dataclass serialization",
        }.get(bp.category, bp.category)
        result.findings.append(
            f"BOTTLENECK: '{bp.name}' dominates at {fmt_ms(bp.wall)} ({bp.pct_of_total:.1f}% of total) — classified as {cat_name}"
        )

    # 2. Parse vs extract breakdown
    parse_c = sum(p.wall for p in seq_phases if p.category == "PARSE_C")
    parse_py = sum(p.wall for p in seq_phases if p.category == "PARSE_PY")
    if parse_c > 0 and parse_py > 0:
        ratio = parse_py / parse_c
        result.findings.append(
            f"EXTRACT vs PARSE: Python AST walk is {ratio:.1f}x slower than tree-sitter C parse "
            f"({fmt_ms(parse_py)} vs {fmt_ms(parse_c)}). The C parse is already fast; the Python extraction layer is the cost."
        )

    # 3. Tail latency detection
    tail_outliers = [p for p in phases if p.has_tail and not p.is_parallel]
    for p in tail_outliers:
        ratio = p.p99 / p.p50 if p.p50 and p.p99 else 0
        result.findings.append(
            f"TAIL LATENCY: '{p.name}' has p99={p.p99:.1f}ms vs p50={p.p50:.1f}ms "
            f"(ratio {ratio:.1f}x) — some files are extreme outliers"
        )

    # 4. Parallel speedup analysis
    if parallel_phases:
        for pp in parallel_phases:
            # Find the matching sequential phase
            seq_match = None
            for sp in seq_phases:
                if sp.category == "PARSE_PY":
                    seq_match = sp
                    break
            if seq_match and seq_match.wall > 0:
                speedup = seq_match.wall / pp.wall
                workers = ""
                if "workers" in pp.name:
                    workers = pp.name.split("workers")[0].split(",")[-1].strip()
                pool_type = "thread" if "thread" in pp.name.lower() else "process" if "process" in pp.name.lower() else "?"
                if speedup < 1.2:
                    result.findings.append(
                        f"PARALLEL FAIL: {pool_type}pool gave only {speedup:.2f}x speedup "
                        f"({fmt_ms(seq_match.wall)} → {fmt_ms(pp.wall)}) — GIL blocks Python AST walking. "
                        f"Use ProcessPool instead."
                    )
                elif speedup < 2.0:
                    result.findings.append(
                        f"PARALLEL WEAK: {pool_type}pool gave {speedup:.2f}x speedup — suboptimal due to GIL or overhead"
                    )
                else:
                    result.findings.append(
                        f"PARALLEL OK: {pool_type}pool gave {speedup:.2f}x speedup ({fmt_ms(seq_match.wall)} → {fmt_ms(pp.wall)})"
                    )

    # 5. Embedding analysis
    if result.ml_total > 0:
        embed_phase = next((p for p in phases if p.category == "ML"), None)
        if embed_phase and result.parse_total > 0:
            ratio = result.ml_total / result.parse_total
            result.findings.append(
                f"EMBED vs PARSE: Embedding is {ratio:.1f}x slower than parsing — "
                f"rewriting parser in Rust saves at most {result.parse_pct:.0f}% of total time"
            )

    # 6. Cache effectiveness
    cache_read = next((p for p in phases if "CACHE READ" in p.name), None)
    if cache_read and "hits=" in cache_read.notes:
        result.findings.append(f"CACHE: {cache_read.notes}")

    # 7. Memory usage outliers
    mem_hogs = sorted(phases, key=lambda p: p.peak_mem_mb, reverse=True)[:3]
    for p in mem_hogs:
        if p.peak_mem_mb > 10:
            result.findings.append(
                f"MEMORY: '{p.name}' allocated {fmt_mb(p.peak_mem_mb)} peak"
            )

    # ── Recommendations ──

    recs = result.recommendations

    # Priority 1: If parsing dominates
    if result.parse_pct > 50:
        if parse_py > parse_c * 3:
            recs.append((
                "🔴 HIGH",
                "Port extraction layer to Rust (not the C parse)",
                f"The Python AST walk/extraction is {parse_py/parse_c:.1f}x slower than tree-sitter's C parse. "
                f"This is the part Rust would accelerate. But try ProcessPool first — it's free."
            ))
        # Check if parallel was tested
        if not result.has_parallel:
            recs.append((
                "🔴 HIGH",
                "Test ProcessPool parallel parsing FIRST",
                f"Parsing is {result.parse_pct:.0f}% of total but no parallel test was run. "
                f"Run: python profile_analyzer.py --target ... --parallel 8 --pool-type process. "
                f"This alone could give 4-8x speedup with zero Rust."
            ))

    # Priority 2: If embedding dominates
    if result.embed_pct > 40:
        recs.append((
            "🔴 HIGH",
            "Optimize embedding BEFORE parser rewrite",
            f"Embedding is {result.embed_pct:.0f}% of total. A Rust parser rewrite won't touch this. "
            f"Fix: (1) increase batch_size from 4 to 16-32, (2) share model across analyzers via daemon, "
            f"(3) use async Qdrant upserts instead of ?wait=true."
        ))

    # Priority 3: Tail latency
    if tail_outliers:
        recs.append((
            "🟡 MEDIUM",
            "Investigate outlier files",
            f"{len(tail_outliers)} phase(s) have p99 >> p50, indicating some files are extremely expensive. "
            f"Find the largest/most complex files and check if they cause disproportionate slowdowns. "
            f"Consider file-size-based batching."
        ))

    # Priority 4: Serialization overhead
    if result.serialize_total / result.total_measured * 100 > 10:
        recs.append((
            "🟡 MEDIUM",
            "Reduce asdict() serialization overhead",
            f"Payload→dict serialization is {result.serialize_total/result.total_measured*100:.0f}% of total. "
            f"Consider using __slots__ on dataclasses, or msgpack instead of asdict+json."
        ))

    # Priority 5: Memory
    total_mem = sum(p.peak_mem_mb for p in phases if not p.is_parallel)
    if total_mem > 100:
        recs.append((
            "🟡 MEDIUM",
            "Reduce peak memory usage",
            f"Total peak allocations: {fmt_mb(total_mem)}. The 'load all payloads into memory' pattern "
            f"(see python_analyzer.py:1390) may cause OOM on large repos. Consider streaming."
        ))

    # Priority 6: Rust ROI estimate
    if result.parse_pct > 0:
        rust_saveable = parse_py / result.total_measured * 100  # only Python extraction
        rust_c_parse = parse_c / result.total_measured * 100    # already C, no gain
        if rust_saveable > 0:
            recs.append((
                "📊 INFO",
                "Rust rewrite ROI estimate",
                f"If Rust makes extraction 3x faster (typical), you save "
                f"~{parse_py * 2/3 / result.total_measured * 100:.0f}% of total time end-to-end. "
                f"The C parse ({rust_c_parse:.0f}%) is already native — no Rust gain there. "
                f"Embedding/IO ({100 - result.parse_pct:.0f}%) is untouched."
            ))

    # Sort recommendations by priority
    priority_order = {"🔴 HIGH": 0, "🟡 MEDIUM": 1, "🟢 LOW": 2, "📊 INFO": 3}
    recs.sort(key=lambda r: priority_order.get(r[0], 99))

    return result


# ═══════════════════════════════════════════════════════════
#  Output rendering
# ═══════════════════════════════════════════════════════════

def render_report(result: AnalysisResult):
    """Print full analysis report."""
    r = result

    print("\n" + "=" * 110)
    print(f"  📊 BOTTLENECK ANALYSIS: {r.language}  |  files: {r.file_count}  |  source: {os.path.basename(r.filepath)}")
    print("=" * 110)

    # ── Section 1: Phase breakdown ──
    print("\n┌─ PHASE BREAKDOWN ──────────────────────────────────────────────────────────────────────────────")
    print(f"│ {'PHASE':<58} {'WALL':>8} {'%':>6}  {'BAR':<32} {'THROUGHPUT':>12}  {'TAIL (p50→p99)':>16}")
    print("├──────────────────────────────────────────────────────────────────────────────────────────────────")

    for p in r.phases:
        thr = f"{p.throughput:.0f}/s" if p.throughput > 0 else ""
        tail = ""
        if p.p50 is not None and p.p99 is not None:
            tail = f"{p.p50:.1f}ms → {p.p99:.1f}ms"
            tr = tail_ratio(p.p50, p.p99)
            if tr and tr > 5:
                tail += f" ⚠️{tr:.0f}x"
        rust = "🦀" if p.rust_candidate else "  "
        print(f"│ {rust}{p.name:<56} {fmt_ms(p.wall):>8} {p.pct_of_total:5.1f}%  {bar(p.pct_of_total)} {thr:>12}  {tail:>16}")

    print("└──────────────────────────────────────────────────────────────────────────────────────────────────")
    print(f"  🦀 = Rust candidate    ⚠️ = tail latency outlier")

    # ── Section 2: Category summary ──
    print("\n┌─ TIME BY CATEGORY ──────────────────────────────────────────────────────────────────────────────")
    print(f"│ {'CATEGORY':<25} {'WALL':>8}  {'%':>6}  {'BAR':<40}  RUST IMPACT")
    print("├──────────────────────────────────────────────────────────────────────────────────────────────────")

    cats = [
        ("Parsing (C tree-sitter)", sum(p.wall for p in r.phases if p.category == "PARSE_C" and not p.is_parallel), "Already native — no gain"),
        ("Parsing (Python extract)", sum(p.wall for p in r.phases if p.category == "PARSE_PY" and not p.is_parallel), "🦀 Primary Rust target"),
        ("I/O (cache + scan)", sum(p.wall for p in r.phases if p.category == "IO" and not p.is_parallel), "No gain — I/O bound"),
        ("Serialization (asdict)", sum(p.wall for p in r.phases if p.category == "SERIALIZE" and not p.is_parallel), "🦀 Marginal gain"),
        ("Semantic (regex)", sum(p.wall for p in r.phases if p.category == "HEURISTIC" and not p.is_parallel), "🦀 Marginal gain"),
        ("Embedding (ML)", sum(p.wall for p in r.phases if p.category == "ML" and not p.is_parallel), "No gain — must stay Python"),
    ]
    for name, wall, impact in cats:
        if wall < 0.0001:
            continue
        pct_val = wall / r.total_measured * 100 if r.total_measured > 0 else 0
        print(f"│ {name:<25} {fmt_ms(wall):>8}  {pct_val:5.1f}%  {bar(pct_val, 40)}  {impact}")

    print("├──────────────────────────────────────────────────────────────────────────────────────────────────")
    print(f"│ {'TOTAL':<25} {fmt_ms(r.total_measured):>8}")
    print("└──────────────────────────────────────────────────────────────────────────────────────────────────")

    # ── Section 3: Findings ──
    if r.findings:
        print("\n┌─ FINDINGS ───────────────────────────────────────────────────────────────────────────────────────")
        for i, f in enumerate(r.findings, 1):
            print(f"│ {i}. {f}")
        print("└──────────────────────────────────────────────────────────────────────────────────────────────────")

    # ── Section 4: Recommendations ──
    if r.recommendations:
        print("\n┌─ RECOMMENDATIONS (sorted by priority) ────────────────────────────────────────────────────────────")
        for i, (priority, title, detail) in enumerate(r.recommendations, 1):
            print(f"│")
            print(f"│  {priority}  {i}. {title}")
            # Wrap detail at 95 chars
            words = detail.split()
            line = "│           "
            for word in words:
                if len(line) + len(word) + 1 > 105:
                    print(line.rstrip())
                    line = "│           "
                line += word + " "
            if line.strip():
                print(line.rstrip())
        print("│")
        print("└──────────────────────────────────────────────────────────────────────────────────────────────────")

    # ── Section 5: Verdict ──
    print("\n┌─ VERDICT ────────────────────────────────────────────────────────────────────────────────────────")

    if r.embed_pct > 40:
        print("│  🟡 Embedding dominates — rewriting parser in Rust will NOT solve the main bottleneck.")
        print("│     Fix embedding first (batch_size, shared model, async upserts), then re-evaluate.")
    elif r.parse_pct > 60 and result.parse_total > 0:
        parse_py_pct = sum(p.wall for p in r.phases if p.category == "PARSE_PY") / r.total_measured * 100
        if parse_py_pct > 40:
            print("│  🟡 Parsing dominates AND the Python extraction layer is the main cost.")
            print("│     → ProcessPool parallelization is the cheapest fix (zero Rust).")
            print(f"│     → Rust rewrite of the extraction layer could save ~{parse_py_pct * 0.6:.0f}% end-to-end.")
            print("│     → Recommend: parallelize first, benchmark, then decide on Rust.")
        else:
            print("│  🟢 Parsing dominates but the C parse is already fast.")
            print("│     Rust won't help much — focus on parallelization and I/O optimization.")
    else:
        print("│  🟢 No single dominant bottleneck — pipeline is reasonably balanced.")
        print("│     Rust rewrite would give marginal gains. Focus on parallelization + dedup.")

    print("└──────────────────────────────────────────────────────────────────────────────────────────────────\n")


def render_comparison(results: List[AnalysisResult]):
    """Side-by-side comparison of multiple runs."""
    print("\n" + "=" * 120)
    print("  📊 CROSS-RUN COMPARISON")
    print("=" * 120)

    # Header
    run_names = []
    for r in results:
        name = f"{r.language}/{r.file_count}f"
        run_names.append(name)

    print(f"\n  {'METRIC':<35}", end="")
    for name in run_names:
        print(f"  {name:>18}", end="")
    print()
    print("  " + "─" * (35 + 20 * len(results)))

    # Rows
    def row(label: str, values: List[str]):
        print(f"  {label:<35}", end="")
        for v in values:
            print(f"  {v:>18}", end="")
        print()

    row("Total wall time", [fmt_ms(r.total_measured) for r in results])
    row("Files analyzed", [str(r.file_count) for r in results])
    row("Throughput (files/sec)", [f"{r.file_count / r.total_measured:.0f}" if r.total_measured > 0 else "-" for r in results])
    row("Parse % of total", [f"{r.parse_pct:.1f}%" for r in results])
    row("Embed % of total", [f"{r.embed_pct:.0f}%" for r in results])
    row("Bottleneck", [r.bottleneck_phase.name[:18] if r.bottleneck_phase else "-" for r in results])

    print()
    for r in results:
        print(f"  📄 {os.path.basename(r.filepath)}: {r.language}, {r.file_count} files, {fmt_ms(r.total_measured)} total")


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Analyze profile_results_*.json files to identify bottlenecks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze a single result
  python analyze_profile.py profile_results_python_20260731_093854.json

  # Analyze and compare multiple runs
  python analyze_profile.py profile_results_python_*.json --compare

  # Analyze all results in current dir
  python analyze_profile.py *.json
        """,
    )
    parser.add_argument("files", nargs="+",
                        help="One or more profile_results_*.json files")
    parser.add_argument("--compare", action="store_true",
                        help="Side-by-side comparison of multiple runs")

    args = parser.parse_args()

    # Expand globs
    expanded = []
    for f in args.files:
        matched = glob.glob(f) if "*" in f else [f] if os.path.exists(f) else []
        expanded.extend(matched)

    if not expanded:
        print("ERROR: no matching files found", file=sys.stderr)
        return 1

    # Deduplicate preserving order
    seen = set()
    expanded = [f for f in expanded if not (f in seen or seen.add(f))]

    # Load + analyze all
    results = []
    for filepath in expanded:
        try:
            result = analyze(load_json(filepath))
            results.append(result)
        except Exception as e:
            print(f"⚠️  Skipping {filepath}: {e}", file=sys.stderr)

    if not results:
        print("ERROR: no valid profile files could be loaded", file=sys.stderr)
        return 1

    # Render individual reports
    if not args.compare or len(results) == 1:
        for result in results:
            render_report(result)
    else:
        # Comparison mode: show individual then comparison
        for result in results:
            render_report(result)
        if len(results) > 1:
            render_comparison(results)

    print("\n✅ Analysis complete.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
