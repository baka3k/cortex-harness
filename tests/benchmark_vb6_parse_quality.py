"""VB6 parse-quality / throughput benchmark (plan 260917-1200, M4).

Materializes the reviewed fixture corpus into a >=100-file synthetic project
(unique ``Attribute VB_Name`` per replica) and measures per-file amortized
parse latency, throughput, and — for the antlr engine — JVM startup cost
separately so fixed cost never dominates the per-file number.

Usage:
    python tests/benchmark_vb6_parse_quality.py --engine regex [--replicas 12]
    python tests/benchmark_vb6_parse_quality.py --engine antlr [--replicas 12]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
SOURCE_EXTS = {".bas", ".cls", ".frm"}
# malformed.bas is included on purpose (error-recovery cost is part of the
# benchmark); every other file must parse cleanly.
SKIP_FILES = {"expected.json", "Sample.vbp"}


def _percentile(values: List[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def materialize_corpus(replicas: int, target: Path) -> List[Path]:
    """Replicate the fixture corpus with unique module names per replica."""

    sources = sorted(
        path
        for path in FIXTURE_DIR.iterdir()
        if path.suffix.lower() in SOURCE_EXTS and path.name not in SKIP_FILES
    )
    materialized: List[Path] = []
    vbp_lines = ["Type=Exe"]
    for index in range(replicas):
        suffix = f"{index:03d}"
        for source in sources:
            text = source.read_text(encoding="utf-8", errors="ignore")
            text = re.sub(
                r'(Attribute\s+VB_Name\s*=\s*")([^"]+)(")',
                lambda match: f"{match.group(1)}{match.group(2)}_{suffix}{match.group(3)}",
                text,
                count=1,
            )
            stem = source.stem
            out = target / f"{stem}_{suffix}{source.suffix.lower()}"
            out.write_text(text, encoding="utf-8")
            materialized.append(out)
            rel = out.name
            if source.suffix.lower() == ".frm":
                vbp_lines.append(f"Form={rel}")
            elif source.suffix.lower() == ".cls":
                vbp_lines.append(f"Class={stem}_{suffix}; {rel}")
            else:
                vbp_lines.append(f"Module={stem}_{suffix}; {rel}")
    (target / "Benchmark.vbp").write_text("\n".join(vbp_lines) + "\n", encoding="utf-8")
    return materialized


def run_regex(files: List[Path], root: Path) -> Dict[str, Any]:
    from tools.vb.vb_common import get_vb6_parser, parse_vb_file

    latencies: List[float] = []
    ok = 0
    functions = 0
    calls = 0
    for path in files:
        started = time.perf_counter()
        try:
            payload = parse_vb_file(str(path), str(root), get_vb6_parser, "vb6")
            ok += 1
            functions += len(payload[0])
            calls += len(payload[1])
        except Exception:
            pass
        latencies.append((time.perf_counter() - started) * 1000.0)
    return {
        "engine": "regex",
        "files": len(files),
        "ok": ok,
        "functions": functions,
        "calls": calls,
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
        },
        "files_per_sec": round(len(files) / (sum(latencies) / 1000.0), 3) if latencies else 0.0,
        "jvm_startup_ms": 0.0,
    }


def run_antlr(files: List[Path], root: Path) -> Dict[str, Any]:
    """Whole-program batch through the real adapter (includes .frm
    materialization). JVM startup is measured separately via a 1-file run so
    the per-file number is not dominated by fixed cost (M4/F9)."""

    from tools.vb.vb6_antlr_adapter import parse_vb6_files_with_antlr

    started = time.perf_counter()
    payloads, errors, meta = parse_vb6_files_with_antlr(
        root=str(root),
        files=[str(path) for path in files],
        timeout_sec=600.0,
        verbose=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    import subprocess
    import json as _json

    tiny = root / "_jvm_probe.bas"
    tiny.write_text('Attribute VB_Name = "tiny"\nSub A()\nEnd Sub\n', encoding="utf-8")
    try:
        import tempfile as _tempfile

        manifest = {
            "root": str(root),
            "project": "",
            "files": [{"file_path": "_jvm_probe.bas"}],
        }
        jar = root.parent / "code-tiny" / "tools" / "vb" / "antlr_worker" / "worker" / "target" / "vb6-antlr-worker.jar"
        with _tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            _json.dump(manifest, handle)
            manifest_path = handle.name
        t0 = time.perf_counter()
        subprocess.run(
            ["java", "-jar", str(ROOT / "code-tiny/tools/vb/antlr_worker/worker/target/vb6-antlr-worker.jar"),
             "--manifest", manifest_path],
            capture_output=True, timeout=120,
        )
        jvm_startup_ms = (time.perf_counter() - t0) * 1000.0
        os.remove(manifest_path)
    finally:
        tiny.unlink(missing_ok=True)

    return {
        "engine": "antlr",
        "files": len(files),
        "ok": len(payloads),
        "errors": len(errors),
        "functions": sum(len(payload.get("functions", [])) for payload in payloads.values()),
        "calls": sum(len(payload.get("calls", [])) for payload in payloads.values()),
        "latency_ms": {
            "p50": round(elapsed_ms / max(1, len(files)), 3),
            "p95": round(float(meta.get("elapsed_ms") or elapsed_ms) / max(1, len(files)), 3),
        },
        "files_per_sec": round(len(files) / (elapsed_ms / 1000.0), 3) if elapsed_ms else 0.0,
        "jvm_startup_ms": round(jvm_startup_ms, 1),
        "total_elapsed_ms": round(elapsed_ms, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VB6 parse-quality benchmark")
    parser.add_argument("--engine", choices=("regex", "antlr"), default="regex")
    parser.add_argument("--replicas", type=int, default=12, help="fixture replicas (>= 9 for >=100 files)")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    if args.replicas * 9 < 100:
        print(
            f"note: {args.replicas} replicas -> {args.replicas * 9} files (< 100); "
            "M4 requires >= 100 files",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "corpus"
        target.mkdir()
        files = materialize_corpus(args.replicas, target)
        if args.engine == "regex":
            report = run_regex(files, target)
        else:
            report = run_antlr(files, target)
        report["runtime"] = {"python": sys.version.split()[0], "platform": sys.platform}

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
