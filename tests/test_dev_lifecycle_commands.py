import subprocess
import unittest
from unittest import mock

from click.testing import CliRunner

from cortex_harness.dev import REPO_ROOT, cli


class DevLifecycleCommandTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_infra_up_dispatches_from_repository_root(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "win32"), mock.patch(
            "cortex_harness.dev.shutil.which", return_value="powershell.exe"
        ), mock.patch("cortex_harness.dev.subprocess.run", return_value=completed) as run:
            result = self.runner.invoke(cli, ["infra-up"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(REPO_ROOT / "scripts" / "mcp-lifecycle.ps1"),
                "infra-up",
            ],
            cwd=str(REPO_ROOT),
        )

    def test_doctor_dispatches_to_python_on_non_windows(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "linux"), mock.patch(
            "cortex_harness.dev.subprocess.run", return_value=completed
        ) as run:
            result = self.runner.invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            [
                mock.ANY,
                str(REPO_ROOT / "scripts" / "mcp-lifecycle.py"),
                "doctor",
            ],
            cwd=str(REPO_ROOT),
        )

    def test_start_matches_make_start_from_any_directory(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "darwin"), mock.patch(
            "cortex_harness.dev.subprocess.run", return_value=completed
        ) as run:
            with self.runner.isolated_filesystem():
                result = self.runner.invoke(cli, ["start"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            [
                mock.ANY,
                str(REPO_ROOT / "scripts" / "mcp-lifecycle.py"),
                "start",
            ],
            cwd=str(REPO_ROOT),
        )

    def test_lifecycle_failure_exit_code_is_preserved(self):
        completed = subprocess.CompletedProcess([], 7)
        with mock.patch("cortex_harness.dev.subprocess.run", return_value=completed):
            result = self.runner.invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 7)


if __name__ == "__main__":
    unittest.main()
