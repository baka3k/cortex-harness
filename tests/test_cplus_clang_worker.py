import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.parse_recovery import run_clang_worker  # noqa: E402


class CPlusClangWorkerTests(unittest.TestCase):
    def test_real_worker_returns_schema_checked_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "sample.c")
            path.write_text("int sample(void) { return 0; }\n", encoding="utf-8")
            result = run_clang_worker(
                worker_path=str(CODE_TINY / "tools" / "cplus" / "clang_worker.py"),
                request={
                    "protocol_version": "1",
                    "root": root,
                    "path": str(path),
                    "compile_arguments": ["-std=c11"],
                    "compile_context_fingerprint": "test",
                    "memory_mb": 1024,
                    "cpu_seconds": 10,
                    "max_source_bytes": 1024 * 1024,
                },
                timeout_seconds=15,
            )
            self.assertEqual(result["status"], "ok", result.get("error"))
            self.assertEqual(result["protocol_version"], "1")
            self.assertEqual(result["payload"]["file_def"]["file_path"], "sample.c")

    def test_worker_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside, "outside.c")
            outside_file.write_text("int outside(void);\n", encoding="utf-8")
            link = Path(root, "escape.c")
            try:
                link.symlink_to(outside_file)
            except OSError:
                self.skipTest("symlinks unavailable")
            result = run_clang_worker(
                worker_path=str(CODE_TINY / "tools" / "cplus" / "clang_worker.py"),
                request={
                    "protocol_version": "1",
                    "root": root,
                    "path": str(link),
                    "compile_arguments": [],
                    "memory_mb": 1024,
                    "cpu_seconds": 10,
                },
                timeout_seconds=15,
            )
            self.assertNotEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
