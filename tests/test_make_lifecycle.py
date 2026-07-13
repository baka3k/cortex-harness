import importlib.util
import json
import os
import subprocess
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
                self.assertTrue(launcher.is_file())
                self.assertIn(str(server["script"]), launcher.read_text(encoding="utf-8"))

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

        self.assertEqual(start_service.call_count, len(LIFECYCLE.INFRA_SERVICES))

    def test_infra_down_handles_missing_containers(self):
        with mock.patch.object(LIFECYCLE, "docker_command", return_value="docker"), mock.patch.object(
            LIFECYCLE, "container_exists", return_value=False
        ) as container_exists, mock.patch.object(LIFECYCLE, "run") as run_command:
            LIFECYCLE.invoke_infra_down()

        self.assertEqual(container_exists.call_count, len(LIFECYCLE.INFRA_SERVICES))
        run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
