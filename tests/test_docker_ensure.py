"""Tests for the idempotent Docker ensure helpers in ``mcp-lifecycle.py``.

The lifecycle script now owns Qdrant + FalkorDB container lifecycle so
``make infra-up`` can spin up the local services a remote project points
at. These tests cover every branch of the state machine without ever
invoking a real docker daemon.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"

sys_path_entry = str(ROOT)
if sys_path_entry not in sys.path:
    sys.path.insert(0, sys_path_entry)


def _load_lifecycle():
    spec = importlib.util.spec_from_file_location(
        "mcp_lifecycle_docker_test", LIFECYCLE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load_lifecycle()


@pytest.fixture
def fake_run():
    """Patch ``LIFECYCLE.run`` with a side-effect router keyed on the
    docker subcommand (``argv[1]``) — the test harness resolves
    ``shutil.which`` to ``/usr/bin/docker`` so the full path differs across
    hosts but the subcommand is stable.
    """

    def factory(handlers: dict[str, mock.Mock]):
        default = mock.Mock(returncode=1, stdout="", stderr="unexpected call")
        router = mock.Mock(side_effect=lambda argv, **_kwargs: handlers.get(
            argv[1] if len(argv) > 1 else "",
            default,
        ))

        def fake_run(arguments, *, capture=False, check=True):
            return router(arguments, capture=capture, check=check)

        return fake_run, router

    return factory


def _subcommand_sequence(router):
    """Return the sequence of docker subcommands invoked via ``router``."""
    return [call.args[0][1] for call in router.call_args_list if len(call.args[0]) > 1]


def _find_run_call(router):
    for call in router.call_args_list:
        if len(call.args[0]) > 1 and call.args[0][1] == "run":
            return call
    raise AssertionError("docker run was not invoked")


def _qdrant_spec():
    return next(spec for spec in LIFECYCLE.DOCKER_SERVICES if spec["name"] == "cortex-qdrant")


def _falkordb_spec():
    return next(spec for spec in LIFECYCLE.DOCKER_SERVICES if spec["name"] == "cortex-falkordb")


# ── _docker_available ─────────────────────────────────────────────────────


class TestDockerAvailable:
    def test_returns_false_when_cli_missing(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value=None):
            assert LIFECYCLE._docker_available() is False

    def test_returns_true_when_docker_info_succeeds(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            assert LIFECYCLE._docker_available() is True
        run.assert_called_once_with(["/usr/bin/docker", "info"], capture=True, check=False)

    def test_returns_false_when_docker_info_fails(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", return_value=mock.Mock(returncode=1, stderr="boom")
        ):
            assert LIFECYCLE._docker_available() is False


# ── _container_state ──────────────────────────────────────────────────────


class TestContainerState:
    def test_returns_status_when_container_exists(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE,
            "run",
            return_value=mock.Mock(returncode=0, stdout="running\n", stderr=""),
        ):
            assert LIFECYCLE._container_state("cortex-qdrant") == "running"

    def test_returns_none_when_container_missing(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", return_value=mock.Mock(returncode=1, stderr="No such container")
        ):
            assert LIFECYCLE._container_state("cortex-qdrant") is None

    def test_returns_none_when_docker_cli_missing(self):
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value=None):
            assert LIFECYCLE._container_state("cortex-qdrant") is None


# ── _ensure_service: running branch ───────────────────────────────────────


class TestEnsureServiceRunning:
    def test_running_container_is_left_alone(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=0, stdout="running\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "cortex-qdrant running" in rendered
        assert "http://127.0.0.1:6333" in rendered
        # No start, run, pull, or image-inspect calls were issued.
        invoked = _subcommand_sequence(router)
        assert "start" not in invoked
        assert "run" not in invoked
        assert "pull" not in invoked
        assert "image" not in invoked


# ── _ensure_service: stopped branch ───────────────────────────────────────


class TestEnsureServiceStopped:
    def test_stopped_container_is_started_without_pull(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=0, stdout="exited\n", stderr=""),
            "start": mock.Mock(returncode=0, stdout="cortex-qdrant\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        invoked = _subcommand_sequence(router)
        assert "start" in invoked
        assert "pull" not in invoked
        assert "run" not in invoked
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "started existing container" in rendered


# ── _ensure_service: missing + image cached ───────────────────────────────


class TestEnsureServiceMissingImageCached:
    def test_runs_without_pulling_when_image_is_present(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(returncode=0, stdout="abc123\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {}, clear=False), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        invoked = _subcommand_sequence(router)
        assert "pull" not in invoked
        assert "run" in invoked
        argv = _find_run_call(router).args[0]
        # Pin both ports to 127.0.0.1.
        assert "127.0.0.1:6333:6333" in argv
        assert "127.0.0.1:6334:6334" in argv
        # --restart unless-stopped so dev restarts don't drop the container.
        assert "--restart" in argv and "unless-stopped" in argv
        # Named volume so storage survives container recreation.
        assert "cortex-qdrant-storage:/qdrant/storage" in argv
        # Image tag is the latest default.
        assert argv[-1] == "qdrant/qdrant:latest"
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "created + started cortex-qdrant" in rendered


# ── _ensure_service: missing + image absent ───────────────────────────────


class TestEnsureServiceMissingImageAbsent:
    def test_pulls_then_runs(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=1, stderr="Error: No such image", stdout=""),
            "pull": mock.Mock(returncode=0, stdout="", stderr=""),
            "run": mock.Mock(returncode=0, stdout="def456\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {}, clear=False), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_falkordb_spec())
        invoked = _subcommand_sequence(router)
        pull_index = invoked.index("pull")
        run_index = invoked.index("run")
        assert pull_index < run_index, "pull must precede run"
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "pulling falkordb/falkordb:latest" in rendered
        assert "created + started cortex-falkordb" in rendered
        # FalkorDB's primary port is the Browser UI (index 1 → 3000).
        assert "http://127.0.0.1:3000" in rendered


# ── _ensure_service: image + env overrides ────────────────────────────────


class TestEnsureServiceEnvOverrides:
    def test_image_override_honored(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(returncode=0, stdout="x\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict(
            "os.environ", {"QDRANT_IMAGE": "qdrant/qdrant:v1.13.0"}, clear=False
        ):
            LIFECYCLE._ensure_service(_qdrant_spec())
        assert _find_run_call(router).args[0][-1] == "qdrant/qdrant:v1.13.0"

    def test_port_overrides_honored(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(returncode=0, stdout="x\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict(
            "os.environ", {"QDRANT_HTTP_PORT": "16333", "QDRANT_GRPC_PORT": "16334"}, clear=False
        ):
            LIFECYCLE._ensure_service(_qdrant_spec())
        argv = _find_run_call(router).args[0]
        assert "127.0.0.1:16333:6333" in argv
        assert "127.0.0.1:16334:6334" in argv

    def test_invalid_port_falls_back_to_default(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(returncode=0, stdout="x\n", stderr=""),
        }
        run_fn, router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {"QDRANT_HTTP_PORT": "not-a-number"}, clear=False):
            LIFECYCLE._ensure_service(_qdrant_spec())
        argv = _find_run_call(router).args[0]
        assert "127.0.0.1:6333:6333" in argv


# ── _ensure_service: error paths ──────────────────────────────────────────


class TestEnsureServiceErrors:
    def test_pull_failure_is_reported_without_run(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=1, stderr="Error: No such image", stdout=""),
            "pull": mock.Mock(returncode=1, stderr="network unreachable", stdout=""),
        }
        run_fn, _router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {}, clear=False), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "docker pull failed" in rendered
        assert "network unreachable" in rendered

    def test_run_bind_failure_reports_host_port_in_use(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(
                returncode=125,
                stdout="",
                stderr="Error response from daemon: bind: address already in use",
            ),
        }
        run_fn, _router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {}, clear=False), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "host port already in use" in rendered
        assert "remote probe will report reachability" in rendered

    def test_run_unexpected_failure_reports_stderr(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=1, stderr="No such container", stdout=""),
            "image": mock.Mock(returncode=0, stdout="[]", stderr=""),
            "run": mock.Mock(returncode=1, stdout="", stderr="image manifest unknown"),
        }
        run_fn, _router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch.dict("os.environ", {}, clear=False), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "docker run failed" in rendered
        assert "image manifest unknown" in rendered

    def test_start_failure_is_reported(self, fake_run):
        handlers = {
            "inspect": mock.Mock(returncode=0, stdout="stopped\n", stderr=""),
            "start": mock.Mock(returncode=1, stderr="OCI runtime error", stdout=""),
        }
        run_fn, _router = fake_run(handlers)
        with mock.patch.object(LIFECYCLE.shutil, "which", return_value="/usr/bin/docker"), mock.patch.object(
            LIFECYCLE, "run", side_effect=run_fn
        ), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_service(_qdrant_spec())
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "docker start failed" in rendered


# ── _ensure_docker_services ───────────────────────────────────────────────


class TestEnsureDockerServices:
    def test_warns_and_returns_when_daemon_unreachable(self):
        with mock.patch.object(LIFECYCLE, "_docker_available", return_value=False), mock.patch(
            "builtins.print"
        ) as output, mock.patch.object(LIFECYCLE, "_ensure_service") as ensure_one:
            LIFECYCLE._ensure_docker_services()
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "docker not available" in rendered
        assert "skipping container ensure" in rendered
        ensure_one.assert_not_called()

    def test_loops_over_every_service_when_daemon_reachable(self):
        with mock.patch.object(LIFECYCLE, "_docker_available", return_value=True), mock.patch.object(
            LIFECYCLE, "_ensure_service"
        ) as ensure_one:
            LIFECYCLE._ensure_docker_services()
        called_names = [call.args[0]["name"] for call in ensure_one.call_args_list]
        assert called_names == ["cortex-qdrant", "cortex-falkordb"]

    def test_per_service_exception_is_swallowed(self):
        with mock.patch.object(LIFECYCLE, "_docker_available", return_value=True), mock.patch.object(
            LIFECYCLE,
            "_ensure_service",
            side_effect=[None, RuntimeError("boom")],
        ), mock.patch("builtins.print") as output:
            LIFECYCLE._ensure_docker_services()
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        assert "cortex-falkordb" in rendered
        assert "boom" in rendered


# ── invoke_infra_up / infra-down integration with docker ensure ──────────


class TestInfraUpIntegration:
    def test_invoke_infra_up_continues_when_docker_unavailable(self):
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
        ensure_layout.assert_called_once()
        ensure_docker.assert_called_once_with()
        rendered = "\n".join(call.args[0] for call in output.call_args_list if call.args)
        # No crash, layout message still printed before docker warn.
        assert "data root" in rendered
