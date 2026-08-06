import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"
SPEC = importlib.util.spec_from_file_location("mcp_lifecycle", LIFECYCLE_PATH)
assert SPEC and SPEC.loader
LIFECYCLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIFECYCLE)


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

    def test_doctor_probes_the_falkordblite_backend_import(self):
        self.assertIn(
            "from redislite.falkordb_client import FalkorDB",
            LIFECYCLE.PYTHON_DEPENDENCY_PROBE,
        )
        self.assertNotIn("import falkordblite", LIFECYCLE.PYTHON_DEPENDENCY_PROBE)

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
        for target in targets:
            self.assertIn(f"python3 scripts/mcp-lifecycle.py {target}", result.stdout)

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
            ), mock.patch.object(LIFECYCLE, "invoke_stop"), mock.patch.object(
                LIFECYCLE, "terminal_command", side_effect=fake_terminal_command
            ), mock.patch.object(LIFECYCLE, "run"):
                LIFECYCLE.invoke_start()

            records = json.loads(pid_file.read_text(encoding="utf-8"))
            self.assertEqual([record["name"] for record in records], ["code-tiny", "doc-tiny"])
            for server in LIFECYCLE.SERVERS:
                launcher = state_dir / f"start-{server['name']}.command"
                runtime_env = state_dir / f"{server['name']}.active.env"
                self.assertTrue(launcher.is_file())
                self.assertTrue(runtime_env.is_file())
                content = launcher.read_text(encoding="utf-8")
                active_content = runtime_env.read_text(encoding="utf-8")
                self.assertIn(str(server["script"]), content)
                self.assertIn(f"export CORTEX_HARNESS_ENV_FILE={runtime_env}", content)
                self.assertIn('export GRAPH_PROVIDER="${GRAPH_PROVIDER:-falkordb}"', content)
                self.assertIn('export FALKORDB_URI="${FALKORDB_URI:-redis://${FALKORDB_HOST}:${FALKORDB_PORT}}"', content)
                self.assertIn("export FALKORDB_GRAPH=cortext", active_content)
                self.assertIn("export NEO4J_DB=cortext", active_content)
                self.assertIn("export QDRANT_URL=http://localhost:6333", active_content)
                scoped_provider = "DOC_GRAPH_PROVIDER" if server["name"] == "doc-tiny" else "CODE_GRAPH_PROVIDER"
                self.assertIn(
                    f'export {scoped_provider}="${{{scoped_provider}:-${{GRAPH_PROVIDER}}}}"',
                    content,
                )

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
            self.assertIn("export FALKORDB_GRAPH=SHOP", env_content)
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
        self.assertEqual(code_env["QDRANT_COLLECTION"], "code_vectors")
        self.assertEqual(doc_env["MCP_SERVER_NAME"], "mixed-doc")
        self.assertEqual(doc_env["FALKORDB_GRAPH"], "DOC_DB")
        self.assertEqual(doc_env["QDRANT_COLLECTION_DOC"], "doc_vectors")

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

    def test_infra_up_visits_all_configured_services(self):
        with mock.patch.object(LIFECYCLE, "docker_command", return_value="docker"), mock.patch.object(
            LIFECYCLE, "start_infra_service"
        ) as start_service:
            LIFECYCLE.invoke_infra_up()

        self.assertEqual(start_service.call_count, len(LIFECYCLE.infra_services()))

    def test_infra_down_handles_missing_containers(self):
        with mock.patch.object(LIFECYCLE, "docker_command", return_value="docker"), mock.patch.object(
            LIFECYCLE, "container_exists", return_value=False
        ) as container_exists, mock.patch.object(LIFECYCLE, "run") as run_command:
            LIFECYCLE.invoke_infra_down()

        self.assertEqual(container_exists.call_count, len(LIFECYCLE.infra_services()))
        run_command.assert_not_called()

    def test_falkordb_service_exposes_browser_web_ui_port(self):
        falkordb = next(s for s in LIFECYCLE.infra_services() if s["name"] == "falkordb")
        # Container-side port 3000 must always be mapped so the bundled Browser Web UI is reachable.
        self.assertIn("3000:3000", falkordb["ports"])
        self.assertEqual(falkordb["browser_port"], 3000)
        self.assertIn("3000", falkordb["browser_ready_url"])

    def test_falkordb_browser_port_env_override(self):
        with mock.patch.dict(os.environ, {"FALKORDB_BROWSER_PORT": "3001"}, clear=False):
            falkordb = next(s for s in LIFECYCLE.infra_services() if s["name"] == "falkordb")
        self.assertIn("3001:3000", falkordb["ports"])
        self.assertEqual(falkordb["browser_port"], 3001)

    def test_falkordb_data_volume_persisted(self):
        falkordb = next(s for s in LIFECYCLE.infra_services() if s["name"] == "falkordb")
        self.assertTrue(any(str(v).startswith("cortex-falkordb-data:") for v in falkordb["volumes"]))

    def test_falkordb_marked_redis_protocol_for_db_readiness(self):
        falkordb = next(s for s in LIFECYCLE.infra_services() if s["name"] == "falkordb")
        self.assertEqual(falkordb["protocol"], "redis")

    def test_redis_ping_ready_returns_false_on_closed_port(self):
        # Port 1 is reserved/unassigned -> connection must fail -> not ready.
        self.assertFalse(LIFECYCLE.redis_ping_ready("127.0.0.1", 1, timeout=0.5))

    def test_redis_ping_ready_returns_true_on_pong(self):
        class FakeSocket:
            def __init__(self, *args, **kwargs):
                self.sent = b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def settimeout(self, value):
                pass

            def sendall(self, data):
                self.sent = data

            def recv(self, size):
                return b"+PONG\r\n"

        with mock.patch.object(LIFECYCLE.socket, "create_connection", return_value=FakeSocket()):
            self.assertTrue(LIFECYCLE.redis_ping_ready("127.0.0.1", 6379, timeout=1.0))


if __name__ == "__main__":
    unittest.main()
