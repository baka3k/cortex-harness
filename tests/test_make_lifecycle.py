import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"
SPEC = importlib.util.spec_from_file_location("mcp_lifecycle", LIFECYCLE_PATH)
assert SPEC and SPEC.loader
LIFECYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIFECYCLE)

# Ensure ``cortex_harness.storage.remote_probe`` is importable so test
# fixtures can compare against the production ``ProbeResult`` dataclass.
sys_path_entry = str(ROOT / "cortex_harness")
if sys_path_entry not in sys.path:
    sys.path.insert(0, sys_path_entry)
from cortex_harness.storage.remote_probe import ProbeResult  # noqa: E402


@unittest.skipIf(os.name == "nt", "POSIX lifecycle dispatch test")
class MakeLifecycleTests(unittest.TestCase):
    def test_lifecycle_adds_repository_root_to_source_import_path(self):
        isolated_path = [
            entry
            for entry in sys.path
            if not entry or Path(entry).resolve() != ROOT.resolve()
        ]
        spec = importlib.util.spec_from_file_location(
            "mcp_lifecycle_import_path_test",
            LIFECYCLE_PATH,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)

        with mock.patch.object(sys, "path", isolated_path):
            spec.loader.exec_module(module)
            self.assertIn(str(ROOT), sys.path)

    def test_lifecycle_defers_psutil_dependent_process_imports(self):
        source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        self.assertNotIn(
            "from cortex_harness.sync_processes import embedded_falkordb_pids, sync_processes",
            source,
        )
        self.assertIn("def sync_processes(*args, **kwargs):", source)

    def test_doctor_probes_the_falkordblite_backend_import(self):
        self.assertIn(
            "from redislite.falkordb_client import FalkorDB",
            LIFECYCLE.PYTHON_DEPENDENCY_PROBE,
        )
        self.assertNotIn("import falkordblite", LIFECYCLE.PYTHON_DEPENDENCY_PROBE)

    def test_doctor_reports_running_sync_and_embedded_processes(self):
        resolved = SimpleNamespace(
            falkordb_code_path=Path("/stores/code.rdb"),
            falkordb_doc_path=Path("/stores/doc.rdb"),
        )
        worker = SimpleNamespace(pid=4242)
        with mock.patch.object(
            LIFECYCLE,
            "sync_processes",
            side_effect=[[worker], []],
        ), mock.patch.object(
            LIFECYCLE,
            "embedded_falkordb_pids",
            side_effect=[[4343], []],
        ), mock.patch.object(LIFECYCLE, "doctor_check") as check:
            LIFECYCLE.doctor_process_checks(resolved)

        calls = {call.args[0]: call for call in check.call_args_list}
        self.assertIn("running pid(s): 4242", calls["code sync workers"].args[2])
        self.assertIn("running pid(s): 4343", calls["code embedded FalkorDB"].args[2])
        self.assertEqual(calls["doc sync workers"].args[2], "idle")

    def test_embedded_storage_dependencies_are_pinned_consistently(self):
        expected = {"qdrant-client==1.18.0", "falkordblite==0.10.0"}
        for relative in ("requirements.txt", "code-tiny/requirements.txt", "doc-tiny/requirements.txt"):
            with self.subTest(requirements=relative):
                lines = {
                    line.strip()
                    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                }
                self.assertTrue(expected.issubset(lines))
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for dependency in expected:
            self.assertIn(f'"{dependency}"', metadata)

    def test_make_help_does_not_require_powershell(self):
        result = subprocess.run(
            ["make", "help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("make build", result.stdout)

    def test_all_make_targets_dispatch_to_python(self):
        targets = (
            "help",
            "build",
            "install",
            "uninstall",
            "infra-up",
            "infra-down",
            "storage-layout",
            "storage-init",
            "storage-migrate-layout",
            "storage-backup",
            "doctor",
            "start",
            "stop",
        )
        result = subprocess.run(
            ["make", "-n", *targets],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("pwsh", result.stdout)
        expected_python = ".venv/bin/python" if (ROOT / ".venv/bin/python").exists() else "python3"
        for target in targets:
            self.assertIn(
                f"{expected_python} scripts/mcp-lifecycle.py {target}",
                result.stdout,
            )

    def test_make_sync_stop_aliases_dispatch_to_scoped_dev_commands(self):
        expected_python = ".venv/bin/python" if (ROOT / ".venv/bin/python").exists() else "python3"
        for goals, owner in ((["sync", "code", "stop"], "code"), (["sync", "doc", "stop"], "doc")):
            with self.subTest(owner=owner):
                result = subprocess.run(
                    ["make", "-n", *goals],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    f"{expected_python} cortex_harness/dev.py sync {owner} stop",
                    result.stdout,
                )

    def test_build_creates_and_populates_the_venv_with_uv(self):
        with tempfile.TemporaryDirectory() as directory:
            venv_dir = Path(directory) / ".venv"
            python = venv_dir / "bin" / "python"
            command_paths = {"uv": "/tools/uv", "python3": "/tools/python3"}

            with mock.patch.object(LIFECYCLE, "VENV_DIR", venv_dir), mock.patch.object(
                LIFECYCLE.shutil, "which", side_effect=command_paths.get
            ), mock.patch.object(
                LIFECYCLE, "venv_python", return_value=python
            ), mock.patch.object(LIFECYCLE, "run") as run:
                LIFECYCLE.invoke_build()

        self.assertEqual(
            run.call_args_list[0],
            mock.call(["/tools/uv", "venv", "--python", "/tools/python3", str(venv_dir)]),
        )
        install_command = run.call_args_list[1].args[0]
        self.assertEqual(
            install_command[:5],
            ["/tools/uv", "pip", "install", "--python", str(python)],
        )
        self.assertNotIn("pip", install_command[0])
        for requirements in LIFECYCLE.requirement_files():
            self.assertIn(str(requirements), install_command)
        self.assertEqual(install_command[-2:], ["--editable", str(ROOT)])

    def test_build_reports_a_clear_error_when_uv_is_missing(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "uv was not found"):
                LIFECYCLE.invoke_build()

    def test_uv_executable_honors_the_make_override(self):
        with mock.patch.dict(os.environ, {"UV": "/custom/uv"}), mock.patch.object(
            LIFECYCLE.shutil, "which", return_value="/custom/uv"
        ) as which:
            self.assertEqual(LIFECYCLE.uv_executable(), "/custom/uv")
        which.assert_called_once_with("/custom/uv")

    def test_windows_build_backend_uses_uv_instead_of_pip(self):
        lifecycle = (ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")
        self.assertIn("function Get-UvLauncher", lifecycle)
        self.assertIn(
            'Invoke-Uv -Uv $uv -Arguments @("venv", "--python", $launcher, $venvDir)',
            lifecycle,
        )
        self.assertIn('@("pip", "install", "--python", $python)', lifecycle)
        self.assertNotIn("-m pip", lifecycle)

    def test_windows_start_isolates_graph_provider_environment(self):
        lifecycle = (ROOT / "scripts" / "mcp-lifecycle.ps1").read_text(encoding="utf-8")
        self.assertIn("function Get-GraphProvider", lifecycle)
        self.assertIn("function Remove-InactiveGraphEnvironment", lifecycle)
        self.assertIn('if ($effectiveProvider -eq "falkordb")', lifecycle)
        self.assertIn('$overrides.FALKORDB_GRAPH = $databaseName', lifecycle)
        self.assertIn('$overrides.NEO4J_DB = $databaseName', lifecycle)
        self.assertIn("Unsupported graph provider", lifecycle)
        self.assertIn("Env:NEO4J_*", lifecycle)
        self.assertIn("Env:FALKORDB_*", lifecycle)

    def test_install_and_uninstall_use_user_local_bin(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(
                LIFECYCLE, "invoke_build"
            ):
                LIFECYCLE.invoke_install()
                target = Path(home) / ".local" / "bin" / "dev"
                self.assertTrue(target.is_file())
                self.assertTrue(os.access(target, os.X_OK))
                self.assertIn("cortex_harness/dev.py", target.read_text(encoding="utf-8"))
                result = subprocess.run(
                    [str(target), "--help"],
                    cwd=home,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Commands:", result.stdout)
                self.assertIn("stop", result.stdout)

                LIFECYCLE.invoke_uninstall()
                self.assertFalse(target.exists())

    def test_start_creates_launchers_and_pid_records(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            pid_file = state_dir / "pids.json"

            def fake_terminal_command(wrapper):
                name = wrapper.stem.removeprefix("start-")
                (state_dir / f"{name}.pid").write_text("12345", encoding="utf-8")
                return ["true"]

            with mock.patch.object(LIFECYCLE, "STATE_DIR", state_dir), mock.patch.object(
                LIFECYCLE, "PID_FILE", pid_file
            ), mock.patch.object(LIFECYCLE, "invoke_stop") as stop, mock.patch.object(
                LIFECYCLE, "tcp_port_open", return_value=False
            ), mock.patch.object(
                LIFECYCLE, "terminal_command", side_effect=fake_terminal_command
            ), mock.patch.object(LIFECYCLE, "run"):
                LIFECYCLE.invoke_start()

            stop.assert_not_called()

            records = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual([record["name"] for record in records], ["code-tiny", "doc-tiny"])
            for server in LIFECYCLE.SERVERS:
                launcher = state_dir / f"start-cortext-{server['name']}.command"
                runtime_env = state_dir / f"cortext-{server['name']}.active.env"
                self.assertTrue(launcher.is_file())
                self.assertTrue(runtime_env.is_file())
                content = launcher.read_text(encoding="utf-8")
                active_content = runtime_env.read_text(encoding="utf-8")
                self.assertIn(str(server["script"]), content)
                self.assertIn(f"export CORTEX_HARNESS_ENV_FILE={runtime_env}", content)
                self.assertIn('export GRAPH_PROVIDER="${GRAPH_PROVIDER:-falkordb}"', content)
                self.assertNotIn("FALKORDB_URI", content)
                self.assertIn("export CORTEX_STORAGE_INSTANCE=default", active_content)
                self.assertIn("export CORTEX_DATA_HOME=", active_content)
                self.assertTrue(
                    "export FALKORDB_PATH=" in active_content
                    or "export FALKORDB_URI=" in active_content
                )
                self.assertIn("export QDRANT_CODE_PATH=", active_content)
                scoped_provider = "DOC_GRAPH_PROVIDER" if server["name"] == "doc-tiny" else "CODE_GRAPH_PROVIDER"
                self.assertIn(
                    f'export {scoped_provider}="${{{scoped_provider}:-${{GRAPH_PROVIDER}}}}"',
                    content,
                )

    def test_start_uses_nearest_project_dev_config_and_increments_occupied_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "project"
            nested = project_root / "src" / "feature"
            config_path = project_root / ".cortext-harness" / "config" / "dev.json"
            config_path.parent.mkdir(parents=True)
            nested.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "active": False,
                        "project": {"code": "SHOP", "name": "Shop"},
                        "code": {"env": {"FALKORDB_GRAPH": "shop_graph"}},
                        "doc": {"env": {"FALKORDB_GRAPH": "shop_docs"}},
                    }
                ),
                encoding="utf-8",
            )
            (config_path.parent / "prod.json").write_text(
                json.dumps(
                    {
                        "active": True,
                        "project": {"code": "WRONG", "name": "Wrong config"},
                        "code": {"env": {"FALKORDB_GRAPH": "wrong_graph"}},
                        "doc": {"env": {"FALKORDB_GRAPH": "wrong_docs"}},
                    }
                ),
                encoding="utf-8",
            )
            state_dir = Path(directory) / "state"
            pid_file = state_dir / "pids.json"
            state_dir.mkdir()
            pid_file.write_text(
                json.dumps(
                    [
                        {
                            "name": "code-tiny",
                            "instance": "LEGACY",
                            "pid": 101,
                            "script": str(LIFECYCLE.SERVERS[0]["script"]),
                            "port": 8788,
                            "graph": "legacy_graph",
                        },
                        {
                            "name": "doc-tiny",
                            "instance": "LEGACY",
                            "pid": 202,
                            "script": str(LIFECYCLE.SERVERS[1]["script"]),
                            "port": 8789,
                            "graph": "legacy_docs",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            def fake_terminal_command(wrapper):
                state_name = wrapper.stem.removeprefix("start-")
                (state_dir / f"{state_name}.pid").write_text("12345", encoding="utf-8")
                return ["true"]

            occupied = {8788, 8789}
            with mock.patch.object(LIFECYCLE, "STATE_DIR", state_dir), mock.patch.object(
                LIFECYCLE, "PID_FILE", pid_file
            ), mock.patch.object(LIFECYCLE, "invoke_stop") as stop, mock.patch.object(
                LIFECYCLE.Path, "cwd", return_value=nested
            ), mock.patch.object(
                LIFECYCLE, "tcp_port_open", side_effect=lambda _host, port: port in occupied
            ), mock.patch.object(
                LIFECYCLE,
                "process_table",
                return_value={
                    101: (1, f"bash {LIFECYCLE.SERVERS[0]['script']} --port 8788"),
                    202: (1, f"bash {LIFECYCLE.SERVERS[1]['script']} --port 8789"),
                },
            ), mock.patch.object(
                LIFECYCLE, "terminal_command", side_effect=fake_terminal_command
            ), mock.patch.object(LIFECYCLE, "run"):
                LIFECYCLE.invoke_start()

            records = json.loads(pid_file.read_text(encoding="utf-8"))
            shop_records = [record for record in records if record["instance"] == "SHOP"]
            self.assertEqual([record["port"] for record in shop_records], [8790, 8791])
            self.assertEqual([record["graph"] for record in shop_records], ["shop_graph", "shop_docs"])
            self.assertTrue(all(record["config_path"] == str(config_path) for record in shop_records))
            self.assertEqual([record["instance"] for record in records[:2]], ["LEGACY", "LEGACY"])
            stop.assert_not_called()
            self.assertIn(
                "--port 8790",
                (state_dir / "start-SHOP-code-tiny.command").read_text(encoding="utf-8"),
            )

    def test_start_config_falls_back_to_installation_dev_json(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                LIFECYCLE.resolve_start_config(Path(directory)),
                (LIFECYCLE.ROOT, LIFECYCLE.ROOT / ".cortext-harness" / "config" / "dev.json"),
            )

    def test_running_mcp_instances_include_tracked_and_untracked_ports(self):
        processes = {
            101: (1, "/repo/code-tiny/mcp.sh --host 127.0.0.1 --port 8788 --path /mcp"),
            102: (101, "python /repo/code-tiny/mcp/unified_mcp.py --port 8788"),
            202: (1, "python /other/doc-tiny/mcp_graph_rag.py --port 8793"),
        }
        records = [
            {
                "name": "code-tiny",
                "instance": "SHOP",
                "pid": 101,
                "script": "/repo/code-tiny/mcp.sh",
                "port": 8788,
                "host": "127.0.0.1",
                "graph": "shop_graph",
            }
        ]

        instances = LIFECYCLE.running_mcp_instances(processes, records)

        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0]["graph"], "shop_graph")
        self.assertEqual(instances[0]["port"], 8788)
        self.assertEqual(instances[1]["port"], 8793)
        self.assertEqual(instances[1]["name"], "doc-tiny")

    def test_running_mcp_instances_hydrate_legacy_records_from_active_env(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            (state_dir / "code-tiny.active.env").write_text(
                "\n".join(
                    (
                        "export FALKORDB_GRAPH=legacy_graph",
                        "export NEO4J_DB=legacy_graph",
                        "export FALKORDB_PATH=/stores/code/data.rdb",
                        "export CORTEX_HARNESS_CONFIG_PATH=/projects/shop/.cortext-harness/config/dev.json",
                    )
                ),
                encoding="utf-8",
            )
            processes = {101: (1, "/repo/code-tiny/mcp.sh")}
            records = [{"name": "code-tiny", "pid": 101, "port": 8788}]

            with mock.patch.object(LIFECYCLE, "STATE_DIR", state_dir):
                instances = LIFECYCLE.running_mcp_instances(processes, records)

        self.assertEqual(instances[0]["graph"], "legacy_graph")
        self.assertEqual(instances[0]["database_path"], "/stores/code/data.rdb")
        self.assertEqual(
            instances[0]["config_path"],
            "/projects/shop/.cortext-harness/config/dev.json",
        )

    def test_doctor_formats_each_mcp_as_readable_multiline_block_with_db_size(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "data.rdb"
            database.write_bytes(b"x" * 1536)
            instance = {
                "name": "code-tiny",
                "pid": 101,
                "host": "127.0.0.1",
                "port": 8788,
                "path": "/mcp",
                "graph": "shop_graph",
                "database_path": str(database),
                "config_path": "/projects/shop/.cortext-harness/config/dev.json",
            }

            with mock.patch.object(LIFECYCLE, "doctor_check") as check, mock.patch(
                "builtins.print"
            ) as output:
                LIFECYCLE.doctor_mcp_checks([instance])

        check.assert_called_once_with("mcp instances", True, "1 running", required=False)
        rendered = "\n".join(call.args[0] for call in output.call_args_list)
        self.assertIn("code-tiny (pid=101)", rendered)
        self.assertIn("Endpoint : http://127.0.0.1:8788/mcp", rendered)
        self.assertIn("Graph    : shop_graph", rendered)
        self.assertIn(f"DB file  : {database}", rendered)
        self.assertIn("DB size  : 1.5 KiB", rendered)
        self.assertIn("Config   : /projects/shop/.cortext-harness/config/dev.json", rendered)

    def test_custom_start_creates_one_named_project_instance(self):
        options = LIFECYCLE.start_options(
            ["--server", "code", "--name", "shop-code", "--project", "SHOP", "--port", "8790"]
        )
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            pid_file = state_dir / "pids.json"

            def fake_terminal_command(wrapper):
                state_name = wrapper.stem.removeprefix("start-")
                (state_dir / f"{state_name}.pid").write_text("12345", encoding="utf-8")
                return ["true"]

            with mock.patch.object(LIFECYCLE, "STATE_DIR", state_dir), mock.patch.object(
                LIFECYCLE, "PID_FILE", pid_file
            ), mock.patch.object(LIFECYCLE, "invoke_stop") as stop, mock.patch.object(
                LIFECYCLE, "tcp_port_open", return_value=False
            ), mock.patch.object(
                LIFECYCLE, "runtime_environment", return_value={}
            ), mock.patch.object(
                LIFECYCLE, "terminal_command", side_effect=fake_terminal_command
            ), mock.patch.object(LIFECYCLE, "run"):
                LIFECYCLE.invoke_start(options)

            stop.assert_called_once_with("shop-code")
            records = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["name"], "code-tiny")
            self.assertEqual(records[0]["instance"], "shop-code")
            self.assertEqual(records[0]["port"], 8790)
            self.assertEqual(records[0]["endpoint"], "http://127.0.0.1:8790/mcp")

            launcher = state_dir / "start-shop-code-code-tiny.command"
            active_env = state_dir / "shop-code-code-tiny.active.env"
            self.assertIn("--port 8790", launcher.read_text(encoding="utf-8"))
            env_content = active_env.read_text(encoding="utf-8")
            self.assertIn("export MCP_SERVER_NAME=shop-code", env_content)
            self.assertIn("export PROJECT_ID=SHOP", env_content)
            self.assertIn("export CORTEX_STORAGE_PROJECT_ID=SHOP", env_content)
            self.assertIn("export FALKORDB_GRAPH=SHOP", env_content)
            self.assertNotIn("export NEO4J_", env_content)
            self.assertIn("export QDRANT_COLLECTION=SHOP", env_content)

    def test_custom_start_supports_independent_code_and_doc_settings(self):
        options = LIFECYCLE.start_options(
            [
                "--name",
                "mixed",
                "--code-database",
                "CODE_DB",
                "--doc-database",
                "DOC_DB",
                "--code-collection",
                "code_vectors",
                "--doc-collection",
                "doc_vectors",
                "--code-port",
                "8810",
                "--doc-port",
                "8811",
            ]
        )
        servers = LIFECYCLE.selected_servers(options)
        self.assertEqual([server["port"] for server in servers], [8810, 8811])
        code_env = LIFECYCLE.runtime_overrides(options, "code-tiny", "mixed", True)
        doc_env = LIFECYCLE.runtime_overrides(options, "doc-tiny", "mixed", True)
        self.assertEqual(code_env["MCP_SERVER_NAME"], "mixed-code")
        self.assertEqual(code_env["FALKORDB_GRAPH"], "CODE_DB")
        self.assertNotIn("NEO4J_DB", code_env)
        self.assertEqual(code_env["QDRANT_COLLECTION"], "code_vectors")
        self.assertEqual(doc_env["MCP_SERVER_NAME"], "mixed-doc")
        self.assertEqual(doc_env["FALKORDB_GRAPH"], "DOC_DB")
        self.assertNotIn("NEO4J_DB", doc_env)
        self.assertEqual(doc_env["QDRANT_COLLECTION_DOC"], "doc_vectors")

    def test_custom_start_database_override_is_scoped_to_explicit_neo4j(self):
        options = LIFECYCLE.start_options(
            [
                "--server",
                "code",
                "--name",
                "neo-code",
                "--database",
                "NEO_DB",
                "--provider",
                "neo4j",
            ]
        )

        env = LIFECYCLE.runtime_overrides(
            options,
            "code-tiny",
            "neo-code",
            False,
            {
                "GRAPH_PROVIDER": "falkordb",
                "CODE_GRAPH_PROVIDER": "falkordb",
                "FALKORDB_GRAPH": "stale",
            },
        )

        self.assertEqual(env["GRAPH_PROVIDER"], "neo4j")
        self.assertEqual(env["CODE_GRAPH_PROVIDER"], "neo4j")
        self.assertEqual(env["NEO4J_DB"], "NEO_DB")
        self.assertNotIn("FALKORDB_GRAPH", env)

    def test_custom_start_invalid_configured_provider_fails_closed(self):
        options = LIFECYCLE.start_options(
            ["--server", "code", "--name", "invalid", "--database", "graph"]
        )
        with self.assertRaisesRegex(ValueError, "GRAPH_PROVIDER.*falkordb.*neo4j"):
            LIFECYCLE.runtime_overrides(
                options,
                "code-tiny",
                "invalid",
                False,
                {"GRAPH_PROVIDER": "neo"},
            )

    def test_named_stop_preserves_other_instance_records(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            pid_file = state_dir / "pids.json"
            records = [
                {"name": "code-tiny", "instance": "shop", "pid": 101, "script": "/repo/code-tiny/mcp.sh"},
                {"name": "code-tiny", "instance": "crm", "pid": 202, "script": "/repo/code-tiny/mcp.sh"},
            ]
            pid_file.write_text(json.dumps(records), encoding="utf-8")
            processes = {
                101: (1, "bash /repo/code-tiny/mcp.sh"),
                202: (1, "bash /repo/code-tiny/mcp.sh"),
            }
            with mock.patch.object(LIFECYCLE, "PID_FILE", pid_file), mock.patch.object(
                LIFECYCLE, "process_table", return_value=processes
            ), mock.patch.object(LIFECYCLE, "stop_process_tree") as stop_tree:
                LIFECYCLE.invoke_stop("shop")

            stop_tree.assert_called_once_with(101, processes)
            remaining = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual([record["instance"] for record in remaining], ["crm"])

    def test_macos_terminal_command_uses_osascript(self):
        wrapper = Path("/tmp/cortex launcher.command")
        with mock.patch.object(LIFECYCLE.sys, "platform", "darwin"), mock.patch.object(
            LIFECYCLE.shutil, "which", return_value="/usr/bin/osascript"
        ):
            command = LIFECYCLE.terminal_command(wrapper)

        self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
        self.assertIn("Terminal", command[2])
        self.assertIn(str(wrapper), command[2])

    def test_infra_up_no_longer_emits_deprecation_warning(self):
        """infra-up is a first-class command now, not a deprecated alias."""
        from cortex_harness.storage import layout as layout_mod

        with mock.patch.object(
            LIFECYCLE, "_scan_project_backends", return_value=[]
        ), mock.patch.object(
            LIFECYCLE, "_resolved_storage"
        ), mock.patch.object(
            layout_mod, "ensure_layout"
        ) as ensure_layout, mock.patch.object(
            LIFECYCLE, "_ensure_docker_services"
        ) as ensure_docker, mock.patch(
            "builtins.print"
        ) as output:
            LIFECYCLE.invoke_infra_up()
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        self.assertNotIn("deprecated", rendered.lower())
        self.assertNotIn("storage-init", rendered.lower())
        ensure_layout.assert_called_once()
        ensure_docker.assert_called_once_with()

    def test_infra_up_routes_local_projects_to_storage_init(self):
        projects = [
            {
                "project_id": "local_a",
                "backend_mode": "local",
                "remote_config": None,
                "config_path": "/tmp/local_a.json",
            }
        ]
        from cortex_harness.storage import layout as layout_mod

        with mock.patch.object(LIFECYCLE, "_scan_project_backends", return_value=projects), mock.patch.object(
            LIFECYCLE, "_resolved_storage"
        ) as resolved, mock.patch.object(layout_mod, "ensure_layout") as ensure_layout, mock.patch.object(
            LIFECYCLE, "_ensure_docker_services"
        ):
            LIFECYCLE.invoke_infra_up()
        ensure_layout.assert_called_once_with(resolved())

    def test_infra_up_invokes_docker_ensure_after_layout(self):
        from cortex_harness.storage import layout as layout_mod

        with mock.patch.object(
            LIFECYCLE, "_scan_project_backends", return_value=[]
        ), mock.patch.object(LIFECYCLE, "_resolved_storage"), mock.patch.object(
            layout_mod, "ensure_layout"
        ), mock.patch.object(LIFECYCLE, "_ensure_docker_services") as ensure_docker:
            LIFECYCLE.invoke_infra_up()
        ensure_docker.assert_called_once_with()

    def test_infra_up_probes_remote_projects(self):
        projects = [
            {
                "project_id": "remote_a",
                "backend_mode": "remote",
                "remote_config": {
                    "qdrant_url": "http://qdrant.example:6333",
                    "falkordb_uri": "redis://falkor.example:6379",
                },
                "config_path": "/tmp/remote_a.json",
            }
        ]
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.example:6333", True, "reachable"),
            ProbeResult("falkordb", "redis://falkor.example:6379", True, "reachable"),
        ]
        from cortex_harness.storage import layout as layout_mod, remote_probe as rp

        with mock.patch.object(LIFECYCLE, "_scan_project_backends", return_value=projects), mock.patch.object(
            LIFECYCLE, "_resolved_storage"
        ), mock.patch.object(layout_mod, "ensure_layout"), mock.patch.object(
            LIFECYCLE, "_ensure_docker_services"
        ), mock.patch.object(
            rp, "probe_all", return_value=probe_results
        ), mock.patch("builtins.print") as output:
            LIFECYCLE.invoke_infra_up()
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        self.assertIn("remote_a (remote)", rendered)
        self.assertIn("[ok] qdrant", rendered)
        self.assertIn("[ok] falkordb", rendered)

    def test_infra_up_exits_nonzero_on_remote_probe_failure(self):
        projects = [
            {
                "project_id": "remote_a",
                "backend_mode": "remote",
                "remote_config": {"qdrant_url": "http://qdrant.example:6333"},
                "config_path": "/tmp/remote_a.json",
            }
        ]
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.example:6333", False, "Connection refused"),
        ]
        from cortex_harness.storage import layout as layout_mod, remote_probe as rp

        with mock.patch.object(LIFECYCLE, "_scan_project_backends", return_value=projects), mock.patch.object(
            LIFECYCLE, "_resolved_storage"
        ), mock.patch.object(layout_mod, "ensure_layout"), mock.patch.object(
            LIFECYCLE, "_ensure_docker_services"
        ), mock.patch.object(
            rp, "probe_all", return_value=probe_results
        ), self.assertRaises(SystemExit) as exit_ctx:
            LIFECYCLE.invoke_infra_up()
        self.assertEqual(exit_ctx.exception.code, 1)

    def test_infra_up_provision_flag_invokes_provision_helper(self):
        projects = [
            {
                "project_id": "remote_a",
                "backend_mode": "remote",
                "remote_config": {"qdrant_url": "http://qdrant.example:6333"},
                "config_path": "/tmp/remote_a.json",
            }
        ]
        probe_results = [
            ProbeResult("qdrant", "http://qdrant.example:6333", True, "reachable"),
            ProbeResult("falkordb", "(not configured)", True, "skipped — no falkordb_uri"),
        ]
        from cortex_harness.storage import layout as layout_mod, remote_probe as rp

        with mock.patch.object(LIFECYCLE, "_scan_project_backends", return_value=projects), mock.patch.object(
            LIFECYCLE, "_resolved_storage"
        ), mock.patch.object(layout_mod, "ensure_layout"), mock.patch.object(
            LIFECYCLE, "_ensure_docker_services"
        ), mock.patch.object(
            rp, "probe_all", return_value=probe_results
        ), mock.patch.object(
            LIFECYCLE, "_provision_remote_project"
        ) as provision:
            LIFECYCLE.invoke_infra_up(provision=True)
        provision.assert_called_once()

    def test_infra_up_options_parses_provision_flag(self):
        options = LIFECYCLE.infra_up_options(["--provision"])
        self.assertTrue(options.provision)
        options = LIFECYCLE.infra_up_options([])
        self.assertFalse(options.provision)

    def test_infra_down_closes_remote_clients(self):
        with mock.patch(
            "cortex_harness.storage.qdrant_remote.reset_remote_clients"
        ) as reset, mock.patch.object(LIFECYCLE, "_docker_available", return_value=False):
            LIFECYCLE.invoke_infra_down()
        reset.assert_called_once_with()

    def test_infra_down_stops_managed_containers_when_docker_is_available(self):
        with mock.patch.object(LIFECYCLE, "_docker_available", return_value=True), mock.patch.object(
            LIFECYCLE, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")
        ) as run_mock, mock.patch(
            "cortex_harness.storage.qdrant_remote.reset_remote_clients"
        ) as reset:
            LIFECYCLE.invoke_infra_down()
        stop_calls = [
            call for call in run_mock.call_args_list
            if call.args and len(call.args[0]) >= 2 and call.args[0][1] == "stop"
        ]
        self.assertEqual(
            [call.args[0][2] for call in stop_calls],
            ["cortex-qdrant", "cortex-falkordb"],
        )
        reset.assert_called_once_with()

    def test_infra_down_silently_skips_missing_containers(self):
        # ``docker stop`` returns nonzero when the container is missing — that
        # path must be swallowed so ``infra-down`` stays idempotent.
        with mock.patch.object(LIFECYCLE, "_docker_available", return_value=True), mock.patch.object(
            LIFECYCLE,
            "run",
            return_value=mock.Mock(returncode=1, stdout="", stderr="No such container"),
        ) as run_mock, mock.patch(
            "cortex_harness.storage.qdrant_remote.reset_remote_clients"
        ):
            LIFECYCLE.invoke_infra_down()
        stop_calls = [
            call for call in run_mock.call_args_list
            if call.args and len(call.args[0]) >= 2 and call.args[0][1] == "stop"
        ]
        self.assertGreaterEqual(len(stop_calls), 2)

    def test_active_infra_aliases_do_not_execute_container_commands(self):
        import inspect

        source = inspect.getsource(LIFECYCLE.invoke_infra_up) + inspect.getsource(LIFECYCLE.invoke_infra_down)
        for command in ("docker_command", "start_infra_service", "container_exists", "container_running"):
            self.assertNotIn(command, source)

    def test_scan_merges_root_and_caller_configs(self):
        """_scan_project_backends merges ROOT + caller configs."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_config = tmp_path / "repo" / ".cortext-harness" / "config"
            root_config.mkdir(parents=True)
            _write_config(
                root_config,
                "repo_proj",
                {
                    "project": {"code": "repo_proj"},
                },
            )

            caller_config = tmp_path / "caller" / ".cortext-harness" / "config"
            caller_config.mkdir(parents=True)
            _write_config(
                caller_config,
                "caller_proj",
                {
                    "project": {"code": "caller_proj"},
                    "storage_backend": "remote",
                    "remote": {"qdrant_url": "http://localhost:6333"},
                },
            )

            with mock.patch.object(LIFECYCLE, "ROOT", tmp_path / "repo"), mock.patch(
                "pathlib.Path.cwd", return_value=tmp_path / "caller"
            ):
                projects = LIFECYCLE._scan_project_backends()

            ids = [p["project_id"] for p in projects]
            self.assertIn("repo_proj", ids)
            self.assertIn("caller_proj", ids)
            self.assertEqual(len(projects), 2)


def _write_config(config_dir: Path, name: str, payload: dict) -> Path:
    path = config_dir / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
