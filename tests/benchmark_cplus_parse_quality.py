"""Materialize and benchmark the reviewed 100-file parser-quality canary."""

from __future__ import annotations

import json
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer  # noqa: E402


def _percentile(values, quantile):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def main() -> int:
    manifest_path = ROOT / "tests" / "fixtures" / "cplus_parse_quality" / "corpus.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    latencies = []
    tier_counts = {}
    yield_totals = {"functions": 0, "types": 0, "calls": 0, "includes": 0}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp_root:
        temp_path = Path(temp_root)
        compile_files = set()
        materialized = []
        for cohort in manifest["cohorts"]:
            for index in range(int(cohort["count"])):
                rel_path = f"{cohort['id']}/{index:02d}{cohort['extension']}"
                path = temp_path / rel_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(cohort["source"].encode(cohort["encoding"]))
                materialized.append(path)
                if cohort["compile_context"]:
                    compile_files.add(rel_path)

        compile_index = {
            "path": "",
            "entries": len(compile_files),
            "cpp_files": {path for path in compile_files if path.endswith((".cpp", ".h"))},
            "c_files": {path for path in compile_files if not path.endswith((".cpp", ".h"))},
            "fingerprint": "reviewed-synthetic-v1",
        }
        for path in materialized:
            item_started = time.perf_counter()
            payload = cplus_analyzer._load_or_parse_payload(
                str(path),
                temp_root,
                str(temp_path / ".cache"),
                False,
                compile_index,
                "parse-quality-canary",
            )
            latencies.append((time.perf_counter() - item_started) * 1000.0)
            quality = (payload.get("parse_meta") or {}).get("quality") or {}
            if not quality:
                raise RuntimeError(f"fixture produced no quality record: {path}")
            tier = quality.get("tier") or "unknown"
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            semantic = quality.get("semantic_yield") or {}
            yield_totals["functions"] += int(semantic.get("function_count") or 0)
            yield_totals["types"] += int(semantic.get("type_count") or 0)
            yield_totals["calls"] += int(semantic.get("call_count") or 0)
            yield_totals["includes"] += int(semantic.get("include_count") or 0)

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_bytes = int(peak_rss if sys.platform == "darwin" else peak_rss * 1024)
    report = {
        "schema_version": "1",
        "corpus_file_count": len(latencies),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
        },
        "peak_rss_bytes": peak_rss_bytes,
        "quality_tiers": dict(sorted(tier_counts.items())),
        "semantic_yield": yield_totals,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
