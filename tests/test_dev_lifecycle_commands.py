import subprocess
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cortex_harness.dev import (
    REPO_ROOT,
    _embedded_falkordb_pids,
    _mcp_pids,
    _mcp_stop_pattern,
    _pause_code_mcp_for_sync,
    _sync_process_scope,
    cli,
)
from cortex_harness.sync_processes import ProcessRecord


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

    def test_doctor_dispatches_to_python_from_caller_directory_on_non_windows(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "linux"), mock.patch(
            "cortex_harness.dev.subprocess.run", return_value=completed
        ) as run:
            with self.runner.isolated_filesystem() as caller_directory:
                result = self.runner.invoke(cli, ["doctor"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            [
                mock.ANY,
                str(REPO_ROOT / "scripts" / "mcp-lifecycle.py"),
                "doctor",
            ],
            cwd=str(Path(caller_directory).resolve()),
        )

    def test_start_matches_make_start_from_any_directory(self):
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch("cortex_harness.dev.sys.platform", "darwin"), mock.patch(
            "cortex_harness.dev.subprocess.run", return_value=completed
        ) as run:
            with self.runner.isolated_filesystem() as caller_directory:
                result = self.runner.invoke(cli, ["start"])

        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with(
            [
                mock.ANY,
                str(REPO_ROOT / "scripts" / "mcp-lifecycle.py"),
                "start",
            ],
            cwd=str(Path(caller_directory).resolve()),
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
        make_only_sync_aliases = {"code", "doc", "sync-code-stop", "sync-doc-stop"}
        self.assertEqual(
            make_targets - set(cli.commands) - make_only_sync_aliases,
            set(),
        )

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
                if action == "infra-up":
                    run.assert_called_once_with(action, [])
                else:
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

    def test_windows_start_uses_caller_config_and_keeps_project_ids_equal(self):
        lifecycle = (REPO_ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")

        self.assertIn("function Resolve-StartConfig", lifecycle)
        self.assertIn("--config $startConfig.Path", lifecycle)
        self.assertIn("$overrides.CORTEX_STORAGE_PROJECT_ID = $Project", lifecycle)

    def test_windows_lifecycle_has_no_container_runtime_behavior(self):
        lifecycle = (REPO_ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")
        executable_lines = "\n".join(
            line for line in lifecycle.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("docker", executable_lines.casefold())
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
        self.assertIn('os.getenv("MCP_SERVER_NAME", "graph_mcp")', code_server)
        self.assertIn('os.getenv("MCP_SERVER_NAME", "mind_mcp")', doc_server)

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

    def test_posix_mcp_pid_discovery_ignores_its_own_process_match(self):
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "  101 /usr/bin/python3 mcp/unified_mcp.py --port 8788\n"
                "  202 /usr/bin/pgrep -f unified_mcp.py\n"
                "  303 /usr/bin/node unified_mcp.py\n"
            ),
        )
        with mock.patch("cortex_harness.dev.sys.platform", "darwin"), mock.patch(
            "cortex_harness.dev.subprocess.run", return_value=completed
        ) as run:
            result = _mcp_pids("unified_mcp.py")

        self.assertEqual(result, [101])
        self.assertEqual(run.call_args.args[0], ["ps", "-ax", "-o", "pid=,command="])

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

    def test_posix_mcp_stop_force_kills_process_that_ignores_term(self):
        with mock.patch("cortex_harness.dev.sys.platform", "darwin"), mock.patch(
            "cortex_harness.dev._mcp_pids", side_effect=[[101], [101]]
        ), mock.patch("cortex_harness.dev.time.monotonic", side_effect=[0.0, 6.0]), mock.patch(
            "cortex_harness.dev.subprocess.run"
        ) as run:
            stopped = _mcp_stop_pattern("unified_mcp.py")

        self.assertEqual(stopped, 1)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [["kill", "-TERM", "101"], ["kill", "-KILL", "101"]],
        )

    def test_embedded_falkordb_pid_discovery_matches_exact_database(self):
        with self.runner.isolated_filesystem():
            root = Path.cwd()
            target = root / "code" / "data.rdb"
            other = root / "doc" / "data.rdb"
            target.parent.mkdir(parents=True)
            other.parent.mkdir(parents=True)
            target_config = root / "target.config"
            other_config = root / "other.config"
            target_config.write_text(
                f"dir '{target.parent}'\ndbfilename '{target.name}'\n",
                encoding="utf-8",
            )
            other_config.write_text(
                f"dir '{other.parent}'\ndbfilename '{other.name}'\n",
                encoding="utf-8",
            )
            processes = {
                101: ProcessRecord(
                    pid=101,
                    ppid=1,
                    argv=("/opt/redislite/bin/redis-server", str(target_config)),
                ),
                202: ProcessRecord(
                    pid=202,
                    ppid=1,
                    argv=("/opt/redislite/bin/redis-server", str(other_config)),
                ),
                303: ProcessRecord(pid=303, ppid=1, argv=("python", "worker.py")),
            }
            result = _embedded_falkordb_pids(target, processes=processes)

        self.assertEqual(result, [101])

    def test_code_sync_pauses_and_restarts_running_local_mcp(self):
        with mock.patch(
            "cortex_harness.dev._mcp_pids", side_effect=[[9245], []]
        ) as pids, mock.patch(
            "cortex_harness.dev._mcp_stop_pattern", return_value=1
        ) as stop, mock.patch(
            "cortex_harness.dev._stop_embedded_falkordb", return_value=(8123,)
        ) as stop_falkor, mock.patch(
            "cortex_harness.dev._mcp_start_one",
            return_value={"status": "started", "pid": 9300},
        ) as start:
            with _pause_code_mcp_for_sync(
                {
                    "CODE_GRAPH_PROVIDER": "falkordb",
                    "FALKORDB_PATH": "/tmp/code.rdb",
                },
                project_path=Path("/tmp/project"),
                enabled=True,
            ):
                pass

        stop.assert_called_once_with("unified_mcp.py", instance_id="default")
        stop_falkor.assert_called_once_with(Path("/tmp/code.rdb"))
        start.assert_called_once()
        self.assertEqual(pids.call_count, 2)

    def test_code_sync_stops_orphan_falkordb_without_restarting_mcp(self):
        with mock.patch(
            "cortex_harness.dev._mcp_pids", return_value=[]
        ), mock.patch(
            "cortex_harness.dev._embedded_falkordb_pids", side_effect=[[8123], []]
        ), mock.patch(
            "cortex_harness.dev._stop_embedded_falkordb", return_value=(8123,)
        ) as stop_falkor, mock.patch(
            "cortex_harness.dev._mcp_start_one"
        ) as start:
            with _pause_code_mcp_for_sync(
                {
                    "CODE_GRAPH_PROVIDER": "falkordb",
                    "FALKORDB_PATH": "/tmp/code.rdb",
                },
                project_path=Path("/tmp/project"),
                enabled=True,
            ):
                pass

        stop_falkor.assert_called_once_with(Path("/tmp/code.rdb"))
        start.assert_not_called()

    def test_sync_stop_subcommands_dispatch_to_scoped_owner_cleanup(self):
        config = {"code": {"env": {}}, "doc": {"env": {}}}
        with mock.patch(
            "cortex_harness.dev._load_active_config", return_value=(config, Path("dev.json"))
        ), mock.patch(
            "cortex_harness.dev._code_env_for_process", return_value={"owner": "code"}
        ), mock.patch(
            "cortex_harness.dev._doc_env_for_process", return_value={"owner": "doc"}
        ), mock.patch("cortex_harness.dev._stop_sync_command") as stop:
            code_result = self.runner.invoke(cli, ["sync", "code", "stop"])
            doc_result = self.runner.invoke(cli, ["sync", "doc", "stop"])

        self.assertEqual(code_result.exit_code, 0, code_result.output)
        self.assertEqual(doc_result.exit_code, 0, doc_result.output)
        self.assertEqual([call.args[0] for call in stop.call_args_list], ["code", "doc"])

    def test_sync_scope_cleans_previous_and_interrupted_workers(self):
        with mock.patch("cortex_harness.dev._stop_sync_workers") as stop:
            with self.assertRaises(KeyboardInterrupt):
                with _sync_process_scope("code", {"FALKORDB_PATH": "/tmp/code.rdb"}, enabled=True):
                    raise KeyboardInterrupt

        self.assertEqual(stop.call_count, 2)
        self.assertEqual(
            [call.kwargs["prefix"] for call in stop.call_args_list],
            ["cleared previous run", "cleanup"],
        )

    def test_code_sync_does_not_pause_mcp_for_dry_run_or_remote_graph(self):
        with mock.patch("cortex_harness.dev._mcp_pids") as pids, mock.patch(
            "cortex_harness.dev._mcp_stop_pattern"
        ) as stop, mock.patch("cortex_harness.dev._mcp_start_one") as start:
            with _pause_code_mcp_for_sync(
                {"CODE_GRAPH_PROVIDER": "falkordb"},
                project_path=Path("/tmp/project"),
                enabled=False,
            ):
                pass
            with _pause_code_mcp_for_sync(
                {"CODE_GRAPH_PROVIDER": "neo4j"},
                project_path=Path("/tmp/project"),
                enabled=True,
            ):
                pass

        pids.assert_not_called()
        stop.assert_not_called()
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
