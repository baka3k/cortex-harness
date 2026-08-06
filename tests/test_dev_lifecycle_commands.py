import subprocess
import unittest
from unittest import mock

from click.testing import CliRunner

from cortex_harness.dev import REPO_ROOT, _mcp_pids, _mcp_stop_pattern, cli


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

    def test_custom_start_forwards_named_project_options(self):
        with mock.patch("cortex_harness.dev._run_lifecycle") as run:
            result = self.runner.invoke(
                cli,
                [
                    "start",
                    "--server",
                    "code",
                    "--name",
                    "shop-code",
                    "--project",
                    "SHOP",
                    "--port",
                    "8790",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            "start",
            ["--server", "code", "--name", "shop-code", "--project", "SHOP", "--port", "8790"],
        )

    def test_windows_custom_start_translates_options_for_powershell(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "win32"), mock.patch(
            "cortex_harness.dev.shutil.which", return_value="powershell.exe"
        ), mock.patch("cortex_harness.dev.subprocess.run", return_value=completed) as run:
            result = self.runner.invoke(
                cli,
                ["start", "--server", "doc", "--name", "shop-doc", "--project", "SHOP", "--port", "8791"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        command = run.call_args.args[0]
        self.assertEqual(command[-8:], ["-Server", "doc", "-Name", "shop-doc", "-Project", "SHOP", "-Port", "8791"])

    def test_named_stop_forwards_only_the_instance_name(self):
        with mock.patch("cortex_harness.dev._run_lifecycle") as run:
            result = self.runner.invoke(cli, ["stop", "--name", "shop-code"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with("stop", ["--name", "shop-code"])

    def test_every_make_lifecycle_target_is_exposed_by_dev(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))
        make_targets = set(phony.removeprefix(".PHONY:").split())
        self.assertEqual(make_targets - set(cli.commands), set())

    def test_all_lifecycle_commands_dispatch_the_matching_action(self):
        actions = (
            "help",
            "build",
            "install",
            "uninstall",
            "infra-up",
            "infra-down",
            "storage-layout",
            "storage-init",
            "doctor",
            "start",
            "stop",
        )
        for action in actions:
            with self.subTest(action=action), mock.patch("cortex_harness.dev._run_lifecycle") as run:
                result = self.runner.invoke(cli, [action])
                self.assertEqual(result.exit_code, 0, result.output)
                run.assert_called_once_with(action)

    def test_storage_migration_forwards_dry_run_and_apply_options(self):
        with mock.patch("cortex_harness.dev._run_lifecycle") as run:
            result = self.runner.invoke(
                cli,
                ["storage-migrate-layout", "--legacy-root", "/tmp/legacy", "--apply"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            "storage-migrate-layout",
            ["--legacy-root", "/tmp/legacy", "--apply"],
        )

    def test_storage_backup_forwards_owner(self):
        with mock.patch("cortex_harness.dev._run_lifecycle") as run:
            result = self.runner.invoke(cli, ["storage-backup", "--owner", "doc"])
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with("storage-backup", ["--owner", "doc"])

    def test_lifecycle_failure_exit_code_is_preserved(self):
        completed = subprocess.CompletedProcess([], 7)
        with mock.patch("cortex_harness.dev.subprocess.run", return_value=completed):
            result = self.runner.invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 7)

    def test_windows_start_applies_active_config_after_service_env(self):
        lifecycle = (REPO_ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")

        helper_offset = lifecycle.index("mcp_runtime_config.py")
        service_env_offset = lifecycle.index("$envFile = Join-Path")
        active_env_offset = lifecycle.index("$runtimeEnvironment = Get-Content")
        self.assertLess(helper_offset, service_env_offset)
        self.assertLess(service_env_offset, active_env_offset)
        self.assertIn("export CORTEX_HARNESS_ENV_FILE=$runtimeEnvFile", lifecycle)
        self.assertIn("[Environment]::SetEnvironmentVariable(`$_.Name", lifecycle)
        self.assertIn("RuntimeConfig = $runtimeJsonPath", lifecycle)

    def test_windows_lifecycle_has_no_container_runtime_behavior(self):
        lifecycle = (REPO_ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")
        self.assertNotIn("docker", lifecycle.casefold())
        for action in ("storage-layout", "storage-init", "storage-migrate-layout", "storage-backup"):
            self.assertIn(f'"{action}"', lifecycle)

    def test_git_bash_does_not_rewrite_the_mcp_route(self):
        for relative_path in ("code-tiny/mcp.sh", "doc-tiny/mcp.sh"):
            with self.subTest(script=relative_path):
                script = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("MSYS_NO_PATHCONV=1 python", script)
                self.assertIn("--path /mcp", script)
                self.assertIn('"$@"', script)

    def test_mcp_server_names_can_be_overridden_per_instance(self):
        code_server = (REPO_ROOT / "code-tiny" / "mcp" / "unified_mcp.py").read_text(encoding="utf-8")
        doc_server = (REPO_ROOT / "doc-tiny" / "mcp_graph_rag.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("MCP_SERVER_NAME", "Project Call Graph Unified")', code_server)
        self.assertIn('os.getenv("MCP_SERVER_NAME", "graph_rag")', doc_server)

    def test_windows_mcp_pid_discovery_uses_python_command_lines(self):
        completed = subprocess.CompletedProcess([], 0, stdout="101\n202\n")
        with mock.patch("cortex_harness.dev.sys.platform", "win32"), mock.patch(
            "cortex_harness.dev.shutil.which", return_value="powershell.exe"
        ), mock.patch("cortex_harness.dev.subprocess.run", return_value=completed) as run:
            result = _mcp_pids("unified_mcp.py")

        self.assertEqual(result, [101, 202])
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["powershell.exe", "-NoProfile", "-Command"])
        self.assertIn("python", command[3])
        self.assertIn("unified_mcp.py", command[3])

    def test_windows_mcp_stop_kills_process_trees(self):
        with mock.patch("cortex_harness.dev.sys.platform", "win32"), mock.patch(
            "cortex_harness.dev._mcp_pids", side_effect=[[101, 202], []]
        ), mock.patch("cortex_harness.dev.subprocess.run") as run:
            stopped = _mcp_stop_pattern("unified_mcp.py")

        self.assertEqual(stopped, 2)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["taskkill", "/PID", "101", "/T", "/F"],
                ["taskkill", "/PID", "202", "/T", "/F"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
