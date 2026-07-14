import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project  # noqa: E402


class CobolPerformanceTest(unittest.TestCase):
    def test_representative_fixture_stays_below_regression_threshold(self):
        started = time.perf_counter()
        facts, _ = analyze_project(FIXTURE, project_id="performance")
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(facts.summary.processed_files, 8)
        self.assertLess(elapsed, 5.0, f"representative COBOL analysis took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
