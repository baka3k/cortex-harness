import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cortex_harness.dev import _code_sync_summary_path, _dedupe_scan_roots, _run_with_retry


class DevSyncReliabilityTests(unittest.TestCase):
    def test_child_summary_paths_are_scoped_and_unique(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = _code_sync_summary_path(root, "project")
            second = _code_sync_summary_path(root, "project")
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, root / ".cache" / "incremental_sync_summaries")

    def test_parent_scan_root_covers_configured_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "module").mkdir()
            self.assertEqual(
                _dedupe_scan_roots(["module", "."], root),
                ["."],
            )

    def test_lock_busy_exit_is_not_retried(self):
        completed = subprocess.CompletedProcess(["worker"], 2)
        with patch("cortex_harness.dev.subprocess.run", return_value=completed) as run:
            self.assertEqual(
                _run_with_retry(
                    ["worker"],
                    max_retries=3,
                    non_retryable_exit_codes={2},
                ),
                2,
            )
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
