import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.sync_scope import (  # noqa: E402
    LockBusyError,
    ProjectRunLock,
    resolve_sync_cache_dir,
    scan_scope_id,
)


class IncrementalSyncLockTests(unittest.TestCase):
    def test_stale_metadata_file_does_not_block_acquisition(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir, "scan.lock")
            lock_path.write_text("pid=999999 started_at=0\n", encoding="utf-8")

            lock = ProjectRunLock(
                str(lock_path),
                description="fixture",
                scope_id="fixture-scope",
                root=temp_dir,
                timeout_seconds=0,
            )
            lock.acquire()
            self.assertTrue(lock.acquired)
            lock.release()

    def test_live_owner_excludes_second_contender(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir, "scan.lock")
            first = ProjectRunLock(
                str(lock_path), "first", "scope", temp_dir, timeout_seconds=0
            )
            second = ProjectRunLock(
                str(lock_path), "second", "scope", temp_dir, timeout_seconds=0
            )
            first.acquire()
            try:
                with self.assertRaises(LockBusyError):
                    second.acquire()
            finally:
                first.release()

    def test_forced_process_termination_releases_os_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir, "scan.lock")
            script = (
                "import sys,time;"
                f"sys.path.insert(0,{str(CODE_TINY)!r});"
                "from tools.common.sync_scope import ProjectRunLock;"
                f"lock=ProjectRunLock({str(lock_path)!r},'child','scope',{temp_dir!r},timeout_seconds=0);"
                "lock.acquire();print('locked',flush=True);time.sleep(60)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                process.terminate()
                process.wait(timeout=10)
                lock = ProjectRunLock(
                    str(lock_path), "parent", "scope", temp_dir, timeout_seconds=1
                )
                lock.acquire()
                lock.release()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_scope_and_default_cache_are_independent_of_cwd(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            expected = str(Path(root, ".cache").resolve())
            original = os.getcwd()
            try:
                os.chdir(other)
                self.assertEqual(resolve_sync_cache_dir(None, root), expected)
                first = scan_scope_id("project", root)
                os.chdir(original)
                self.assertEqual(scan_scope_id("project", root), first)
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
