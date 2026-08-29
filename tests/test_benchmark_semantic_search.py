"""Tests for scripts/benchmark_semantic_search.py (synthetic mode, stub embedder)."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for entry in (str(SCRIPTS), str(ROOT / "code-tiny")):
    if str(entry) not in sys.path:
        sys.path.insert(0, entry)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_semantic_search", SCRIPTS / "benchmark_semantic_search.py"
)
bench = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bench)


class BenchmarkSemanticSearchTests(unittest.TestCase):
    """Run the synthetic benchmark end-to-end with a stub embedder."""

    @classmethod
    def setUpClass(cls) -> None:
        bench.RUNS = 4
        bench.WARMUP = 1
        # Import unified_mcp (loads all backends) without a store first so the
        # graph driver can be stubbed before any scenario opens a real graph.
        cls.unified = bench.import_unified()
        driver_error = RuntimeError("no graph driver in benchmark test")
        cls._driver_patches = [
            patch.object(cls.unified.cplus_backend, "_get_graph_driver", AsyncMock(side_effect=driver_error)),
            patch.object(cls.unified.android_backend, "_get_graph_driver", AsyncMock(side_effect=driver_error)),
            patch.object(cls.unified.fast_backend, "_get_graph_driver", AsyncMock(side_effect=driver_error)),
        ]
        for item in cls._driver_patches:
            item.start()

    @classmethod
    def tearDownClass(cls) -> None:
        for item in cls._driver_patches:
            item.stop()

    def test_synthetic_run_produces_four_scenarios(self):
        args = argparse.Namespace(live=False, real_embed=False)
        report = bench.run_benchmark(args)

        self.assertEqual(set(report["scenarios"]), set(bench.SCENARIOS))
        self.assertIn("synthetic", report["mode"])
        for name, data in report["scenarios"].items():
            with self.subTest(scenario=name):
                self.assertTrue(data["result_shape_ok"])
                self.assertIn("cplus", data["backends"])
                self.assertGreaterEqual(data["hits_total"], 0)
                for stage in bench.STAGES:
                    stats = data["stages"][stage]
                    self.assertGreaterEqual(stats["p50"], 0.0, stage)
                    self.assertGreaterEqual(stats["p95"], stats["p50"], stage)
                    self.assertEqual(int(stats["n"]), bench.RUNS, stage)
                self.assertGreater(data["wall"]["p50"], 0.0)
        # Stage numbers must come from the cplus backend (production surface).
        self.assertEqual(report["backend_resolved"], ["cplus"])
        self.assertEqual(report["query_engine_resolved"], "graph_generic")

    def test_markdown_writer_emits_all_sections(self):
        args = argparse.Namespace(live=False, real_embed=False)
        report = bench.run_benchmark(args)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.md"
            bench.write_markdown(report, out)
            text = out.read_text(encoding="utf-8")
        for scenario in bench.SCENARIOS:
            self.assertIn(f"## {scenario}", text)
        for stage in ("dispatch-wall", *bench.STAGES):
            self.assertIn(f"| {stage} ", text)


if __name__ == "__main__":
    unittest.main()
