#!/usr/bin/env python3
"""POSIX lifecycle commands used by the root Makefile and global dev CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mcp_runtime_config import format_bash_exports, runtime_environment  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # ``python scripts/mcp-lifecycle.py`` makes ``scripts/`` the first import
    # root. Add the repository root explicitly so the source package works
    # before (and independently of) an editable ``pip install -e .``.
    sys.path.insert(0, str(ROOT))

STATE_DIR = ROOT / ".cache" / "mcp"
PID_FILE = STATE_DIR / "pids.json"
VENV_DIR = ROOT / ".venv"

PYTHON_DEPENDENCY_PROBE = (
    "import qdrant_client, requests; "
    "from redislite.falkordb_client import FalkorDB; "
    "import cortex_harness.storage"
)
PYTHON_DEPENDENCY_LABEL = (
    "qdrant_client, FalkorDBLite backend, requests, cortex_harness.storage"
)

SERVERS = (
    {
        "name": "code-tiny",
        "work_dir": ROOT / "code-tiny",
        "script": ROOT / "code-tiny" / "mcp.sh",
        "port": 8788,
    },
    {
        "name": "doc-tiny",
        "work_dir": ROOT / "doc-tiny",
        "script": ROOT / "doc-tiny" / "mcp.sh",
        "port": 8789,
    },
)

def infra_services() -> tuple[dict[str, object], ...]:
    """Deprecated: retained for one release for compatibility only.

    The Docker-managed Qdrant + FalkorDB containers were replaced by
    project-local persistent storage in Phase 04 of the docker-free
    cutover. ``invoke_storage_init`` creates the same on-disk locations
    without invoking Docker. The shape returned here still matches the
    legacy ``{name, container, image, ports, host, port, ready_url}`` keys
    so any caller that introspects the dict keeps compiling, but the data
    is informational only.
    """
    return ()

USAGE = """Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Deprecated alias for local storage initialization.
  make infra-down  | dev infra-down  Deprecated no-op; local storage has no service lifecycle.
  make doctor      | dev doctor      Check local Python storage runtime, paths, and MCP ports.
  make start       | dev start       Open each MCP server in a separate terminal window.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Parameterized MCP instances:
  dev start --server code --name shop --project SHOP --port 8790
  dev start --name shop --project SHOP --code-port 8790 --doc-port 8791
  dev stop --name shop
  make start START_ARGS="--server code --name shop --project SHOP --port 8790"
  make stop STOP_ARGS="--name shop"

Default MCP servers:
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local storage:
  qdrant code  ./local_qdrant_db/code
  qdrant doc   ./local_qdrant_db/doc
  falkordb     ./local_falkordb_db/cortex.rdb
"""

INSTANCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def run(arguments: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=check,
        text=True,
        capture_output=capture,
    )


def venv_python() -> Path:
    python = VENV_DIR / "bin" / "python"
    if not python.is_file():
        raise RuntimeError(f"Virtualenv Python not found under {VENV_DIR}.")
    return python


def install_requirements(python: Path, requirements: Path) -> None:
    if requirements.is_file():
        print(f"[build] Installing requirements: {requirements}")
        run([str(python), "-m", "pip", "install", "-r", str(requirements)])


def invoke_build() -> None:
    if not VENV_DIR.exists():
        launcher = shutil.which("python3") or shutil.which("python")
        if not launcher:
            raise RuntimeError("Python was not found on PATH. Install Python 3.10+ before running make build.")
        print(f"[build] Creating venv: {VENV_DIR}")
        run([launcher, "-m", "venv", str(VENV_DIR)])

    python = venv_python()
    print(f"[build] Upgrading pip in {VENV_DIR}")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    install_requirements(python, ROOT / "requirements.txt")
    install_requirements(python, ROOT / "code-tiny" / "requirements.txt")
    install_requirements(python, ROOT / "doc-tiny" / "requirements.txt")
    print("[build] Installing editable root package")
    run([str(python), "-m", "pip", "install", "-e", str(ROOT)])
    print("[build] Dependency sync complete.")


def user_bin_dir() -> Path:
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME is not set; cannot choose a user-local install directory.")
    return Path(home) / ".local" / "bin"


def invoke_install() -> None:
    invoke_build()
    bin_dir = user_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "dev"
    root = shlex.quote(str(ROOT))
    target.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"CORTEX_HARNESS_DIR={root}\n"
        'PYTHON_EXE="${CORTEX_HARNESS_DIR}/.venv/bin/python"\n'
        'if [ ! -x "$PYTHON_EXE" ]; then\n'
        '  PYTHON_EXE="$(command -v python3 || command -v python)"\n'
        "fi\n"
        "export PYTHONUTF8=1\n"
        "export PYTHONIOENCODING=utf-8\n"
        'exec "$PYTHON_EXE" "${CORTEX_HARNESS_DIR}/cortex_harness/dev.py" "$@"\n',
        encoding="utf-8",
    )
    target.chmod(0o755)
    print(f"[install] Installed dev command: {target}")
    path_entries = [Path(item).expanduser() for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    if bin_dir not in path_entries:
        print(f'[install] Add this to your shell profile if needed: export PATH="{bin_dir}:$PATH"')


def invoke_uninstall() -> None:
    target = user_bin_dir() / "dev"
    if target.exists():
        target.unlink()
        print(f"[uninstall] Removed dev command: {target}")
    else:
        print(f"[uninstall] dev command was not installed at: {target}")
    print("[uninstall] User PATH was left unchanged.")


def docker_command() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker was not found on PATH. Install Docker Desktop before running make infra-up.")
    if run([docker, "info"], capture=True, check=False).returncode != 0:
        raise RuntimeError("Docker was found, but the Docker daemon is not running. Start Docker Desktop and retry.")
    return docker


def tcp_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(host: str, port: int, timeout: int = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tcp_port_open(host, port):
            return True
        time.sleep(1)
    return False


def http_ready(url: str) -> bool:
    if not url:
        return True
    try:
        with urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except (OSError, URLError):
        return False


def redis_ping_ready(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True when a Redis-protocol server (e.g. FalkorDB) answers PING with PONG.

    Port-open only proves the socket is listening; PING proves the server and its
    modules (FalkorDB graph) finished loading and can actually serve commands.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            # RESP inline command: "*1\r\n$4\r\nPING\r\n"
            connection.sendall(b"*1\r\n$4\r\nPING\r\n")
            return connection.recv(64).startswith(b"+PONG")
    except OSError:
        return False


def wait_for_redis_ping(host: str, port: int, timeout: int = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if redis_ping_ready(host, port):
            return True
        time.sleep(1)
    return False


def container_exists(docker: str, name: str) -> bool:
    result = run(
        [docker, "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture=True,
        check=False,
    )
    return name in result.stdout.splitlines()


def container_running(docker: str, name: str) -> bool:
    result = run([docker, "inspect", "-f", "{{.State.Running}}", name], capture=True, check=False)
    return result.returncode == 0 and result.stdout.strip().splitlines()[:1] == ["true"]


def ensure_docker_image(docker: str, image: str) -> None:
    if run([docker, "image", "inspect", image], capture=True, check=False).returncode == 0:
        return
    print(f"[infra] Pulling image: {image}")
    run([docker, "pull", image])


def ensure_docker_volume(docker: str, volume: str) -> None:
    if run([docker, "volume", "inspect", volume], capture=True, check=False).returncode == 0:
        return
    print(f"[infra] Creating volume: {volume}")
    run([docker, "volume", "create", volume])


def container_ports(docker: str, name: str) -> set[str]:
    result = run([docker, "port", name], capture=True, check=False)
    if result.returncode != 0:
        return set()
    bindings: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split("->")
        if len(parts) == 2:
            binding = parts[1].strip()
            if ":" in binding:
                bindings.add(binding.rsplit(":", 1)[1])
    return bindings


def start_infra_service(docker: str, service: dict[str, object]) -> None:
    name = str(service["container"])
    image = str(service["image"])
    expected_ports = {str(port).split(":", 1)[0] for port in service["ports"]}
    if container_exists(docker, name):
        actual_ports = container_ports(docker, name)
        # Recreate when the current host-side port bindings do not match the desired set
        # (e.g. a new Web UI port was added or FALKORDB_BROWSER_PORT changed). A fresh
        # container has empty actual_ports, so only recreate on an actual mismatch.
        port_mismatch = bool(actual_ports) and expected_ports != actual_ports
        if port_mismatch:
            missing = sorted(expected_ports - actual_ports)
            extra = sorted(actual_ports - expected_ports)
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if extra:
                detail.append(f"unexpected {extra}")
            print(
                f"[infra] {name} port mapping changed ({'; '.join(detail)}); "
                f"recreating to apply the current mapping."
            )
            if container_running(docker, name):
                run([docker, "stop", name])
            run([docker, "rm", name])
            create_docker_container(docker, name, image, service)
        elif container_running(docker, name):
            print(f"[infra] {name} is already running.")
        else:
            print(f"[infra] Starting existing container: {name}")
            run([docker, "start", name])
    else:
        create_docker_container(docker, name, image, service)

    host, port = str(service["host"]), int(service["port"])
    if not wait_for_port(host, port):
        raise RuntimeError(f"{service['name']} did not open {host}:{port} within 30 seconds.")
    ready_url = str(service["ready_url"])
    if not http_ready(ready_url):
        raise RuntimeError(f"{service['name']} is listening, but {ready_url} did not return a healthy response.")
    # For Redis-protocol backends (FalkorDB), PING proves the DB + graph module
    # finished loading, not just that the socket is open.
    if str(service.get("protocol", "")) == "redis":
        if not wait_for_redis_ping(host, port):
            raise RuntimeError(
                f"{service['name']} port {host}:{port} is open, but PING did not return PONG within 30 seconds "
                f"(DB/module may still be loading)."
            )
    print(f"[infra] {service['name']} ready on {host}:{port}")

    # Optional FalkorDB Browser Web UI (bundled in falkordb/falkordb on port 3000).
    browser_port = service.get("browser_port")
    if browser_port:
        browser_url = str(service.get("browser_ready_url") or f"http://127.0.0.1:{browser_port}")
        if not wait_for_port(host, int(browser_port), timeout=45):
            print(
                f"[infra] WARNING: {service['name']} Browser Web UI did not open "
                f"{host}:{browser_port} within 45 seconds."
            )
        elif not http_ready(browser_url):
            print(
                f"[infra] WARNING: {service['name']} Browser Web UI is listening, "
                f"but {browser_url} did not return a healthy response."
            )
        else:
            print(f"[infra] {service['name']} Browser Web UI ready at {browser_url}")


def create_docker_container(docker: str, name: str, image: str, service: dict[str, object]) -> None:
    ensure_docker_image(docker, image)
    volumes = service.get("volumes") or ()
    for volume_mapping in volumes:
        volume_name = str(volume_mapping).split(":", 1)[0]
        if volume_name:
            ensure_docker_volume(docker, volume_name)
    arguments = [docker, "run", "-d", "--name", name, "--restart", "unless-stopped"]
    for port in service["ports"]:
        arguments.extend(("-p", str(port)))
    for volume_mapping in volumes:
        arguments.extend(("-v", str(volume_mapping)))
    arguments.append(image)
    print(f"[infra] Creating container: {name}")
    run(arguments)


def invoke_infra_up() -> None:
    """Deprecated alias for ``storage-init``.

    Kept for one release so existing scripts that still call
    ``infra-up`` keep working. No Docker interaction occurs; the local
    on-disk storage is created in the project's root.
    """
    print(
        "[warn] 'infra-up' is deprecated. Use 'storage-init' instead. "
        "Docker is no longer required."
    )
    invoke_storage_init()


def invoke_infra_down() -> None:
    """Deprecated alias for ``storage-stop``.

    No-op: local storage has no lifecycle to stop. Kept so existing
    scripts keep returning exit code 0.
    """
    print(
        "[warn] 'infra-down' is deprecated. Use 'storage-stop' instead. "
        "Docker is no longer required."
    )


def invoke_storage_init() -> None:
    """Resolve paths, create parent directories, open + close both stores.

    Idempotent: re-running on an already-initialized project is a no-op
    apart from a confirmation message. Reports the logical targets
    (graph, code/doc Qdrant paths) for the doctor / acceptance sequence.
    """
    try:
        from cortex_harness.storage import resolve_storage
    except ImportError as error:
        raise RuntimeError(
            "cortex_harness.storage could not be imported. "
            "Run 'make build' first to install the package in editable mode."
        ) from error

    resolved = resolve_storage(ROOT)
    resolved.ensure_directories()
    print(f"[storage-init] project root : {resolved.project_root}")
    print(f"[storage-init] Qdrant base  : {resolved.qdrant_base}")
    print(f"[storage-init] Qdrant code  : {resolved.qdrant_code_path}")
    print(f"[storage-init] Qdrant doc   : {resolved.qdrant_doc_path}")
    print(f"[storage-init] FalkorDBLite : {resolved.falkordb_path}")

    # Round-trip both stores with a temporary collection / graph so doctor
    # can validate write permission without polluting project data.
    try:
        from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, reset_clients
        from cortex_harness.storage.qdrant import _client_lock

        # Code + doc smoke test only when qdrant_client is importable.
        try:
            from qdrant_client.http import models as qmodels  # noqa: F401
        except ImportError:
            print("[storage-init] qdrant_client not installed; skipping vector round-trip.")
            return

        for role in (QdrantStorageRole.CODE, QdrantStorageRole.DOCUMENT):
            store = LocalQdrantStore(resolved, role)
            try:
                collections = store.list_collection_names()
                print(f"[storage-init] {role.value} collections: {len(collections)}")
            finally:
                store.close()
                with _client_lock:
                    from cortex_harness.storage import qdrant as _qdrant_module
                    _qdrant_module._clients.pop(str(store.path), None)
        reset_clients()
    except ImportError:
        pass


def _supports_color() -> bool:
    """Return True only when writing to a real TTY and NO_COLOR is not set.

    Honors the de-facto standard `NO_COLOR` env var
    (https://no-color.org) and disables colors when output is redirected/piped,
    so logs stay clean and grep-friendly.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


_COLOR = {
    "reset": "\033[0m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "bold": "\033[1m",
}


def _color(text: str, name: str) -> str:
    code = _COLOR.get(name)
    if not code or not _supports_color():
        return text
    return f"{code}{text}{_COLOR['reset']}"


def doctor_check(name: str, ok: bool, message: str, *, required: bool = True) -> int:
    if ok:
        tag = _color("[ok]", "green")
        print(f"[doctor]{tag}   {name} - {message}")
        return 0
    if required:
        tag = _color("[fail]", "red")
        failures = 1
    else:
        tag = _color("[warn]", "yellow")
        failures = 0
    print(f"[doctor]{tag} {name} - {message}")
    return failures


def invoke_doctor() -> None:
    failures = 0
    try:
        python = venv_python()
        failures += doctor_check("python venv", True, str(python))
    except RuntimeError as error:
        python = None
        failures += doctor_check("python venv", False, str(error))

    if python:
        result = run(
            [
                str(python),
                "-c",
                PYTHON_DEPENDENCY_PROBE,
            ],
            capture=True,
            check=False,
        )
        failures += doctor_check(
            "python deps",
            result.returncode == 0,
            PYTHON_DEPENDENCY_LABEL,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                print(f"[doctor] {stderr.splitlines()[-1]}")

    # Local storage paths: report and validate writability without invoking Docker.
    try:
        from cortex_harness.storage import resolve_storage
        resolved = resolve_storage(ROOT)
        doctor_check("qdrant base path", True, str(resolved.qdrant_base))
        doctor_check("qdrant code path", True, str(resolved.qdrant_code_path))
        doctor_check("qdrant doc path",  True, str(resolved.qdrant_doc_path))
        doctor_check("falkordb path",    True, str(resolved.falkordb_path))
        resolved.ensure_directories()
        for label, path in (
            ("qdrant code writable", resolved.qdrant_code_path),
            ("qdrant doc writable",  resolved.qdrant_doc_path),
            ("falkordb writable",    resolved.falkordb_path.parent),
        ):
            failures += doctor_check(label, os.access(path, os.W_OK), str(path))
    except Exception as error:
        failures += doctor_check("local storage", False, str(error))

    # Round-trip the local Qdrant client and FalkorDBLite store in temporary
    # collections / graphs to prove the on-disk backends are usable. We only
    # do this when the optional dependency modules are importable.
    try:
        from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole
        from cortex_harness.storage.qdrant import _client_lock
        from cortex_harness.storage import qdrant as _qdrant_module
        from qdrant_client.http import models as qmodels

        import uuid as _uuid

        tmp_name = f"doctor_{_uuid.uuid4().hex[:8]}"
        for role in (QdrantStorageRole.CODE, QdrantStorageRole.DOCUMENT):
            store = LocalQdrantStore(resolved, role)
            try:
                store.create_collection(
                    tmp_name,
                    vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
                )
                store.upsert(
                    tmp_name,
                    points=[qmodels.PointStruct(id=1, vector=[0.0, 1.0], payload={"__doctor__": True})],
                )
                hits = store.retrieve(tmp_name, ids=[1])
                failures += doctor_check(
                    f"qdrant {role.value} round-trip",
                    bool(hits) and bool(hits[0].payload.get("__doctor__")),
                    str(store.path),
                )
            finally:
                try:
                    store.delete_collection(tmp_name)
                except Exception:
                    pass
                store.close()
                with _client_lock:
                    _qdrant_module._clients.pop(str(store.path), None)
    except ImportError as error:
        doctor_check(
            "qdrant round-trip",
            False,
            f"dependency missing: {error}",
            required=False,
        )
    except Exception as error:
        doctor_check("qdrant round-trip", False, str(error), required=False)

    for server in SERVERS:
        doctor_check(
            f"{server['name']} mcp",
            tcp_port_open("127.0.0.1", int(server["port"])),
            f"127.0.0.1:{server['port']}",
            required=False,
        )

    if failures:
        raise RuntimeError(_color(f"Doctor found {failures} required check(s) failing.", "red"))
    print(_color("[doctor] Required checks passed.", "green"))


def process_table() -> dict[int, tuple[int, str]]:
    result = run(["ps", "-axo", "pid=,ppid=,command="], capture=True)
    processes: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            processes[int(parts[0])] = (int(parts[1]), parts[2])
    return processes


def stop_process_tree(pid: int, processes: dict[int, tuple[int, str]]) -> None:
    children = [child for child, (parent, _) in processes.items() if parent == pid]
    for child in children:
        stop_process_tree(child, processes)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def read_pid_records() -> list[dict[str, object]]:
    if not PID_FILE.is_file():
        return []
    try:
        payload = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [record for record in payload if isinstance(record, dict)] if isinstance(payload, list) else []


def write_pid_records(records: list[dict[str, object]]) -> None:
    if records:
        PID_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
    else:
        PID_FILE.unlink(missing_ok=True)


def invoke_stop(instance: str | None = None) -> None:
    processes = process_table()
    stopped: set[int] = set()
    remaining: list[dict[str, object]] = []
    for record in read_pid_records():
        if instance is not None and record.get("instance") != instance:
            remaining.append(record)
            continue
        pid = int(record.get("pid", 0))
        command = processes.get(pid, (0, ""))[1]
        script = str(record.get("script", ""))
        if pid > 1 and script and script in command:
            print(f"[stop] Stopping saved process {pid} ({record.get('name', 'unknown')})")
            stop_process_tree(pid, processes)
            stopped.add(pid)
        elif pid in processes:
            print(f"[stop] Skipping stale PID record {pid} ({record.get('name', 'unknown')})")

    if instance is None:
        markers = ("code-tiny/mcp.sh", "doc-tiny/mcp.sh", "mcp/unified_mcp.py", "mcp_graph_rag.py")
        for pid, (_, command) in processes.items():
            if pid != os.getpid() and pid not in stopped and str(ROOT) in command and any(marker in command for marker in markers):
                print(f"[stop] Stopping MCP process {pid}")
                stop_process_tree(pid, processes)
        remaining = []

    write_pid_records(remaining)
    scope = f" instance '{instance}'" if instance else ""
    print(f"[stop] MCP{scope} stop complete.")


def terminal_command(wrapper: Path) -> list[str]:
    if sys.platform == "darwin":
        osascript = shutil.which("osascript")
        if not osascript:
            raise RuntimeError("osascript was not found; cannot open macOS Terminal windows.")
        command = shlex.quote(str(wrapper))
        apple_script = f'tell application "Terminal" to do script {json.dumps(command)}'
        return [osascript, "-e", apple_script]

    candidates = (
        ("gnome-terminal", ["--", "bash", str(wrapper)]),
        ("x-terminal-emulator", ["-e", "bash", str(wrapper)]),
        ("xterm", ["-e", "bash", str(wrapper)]),
    )
    for name, arguments in candidates:
        executable = shutil.which(name)
        if executable:
            return [executable, *arguments]
    raise RuntimeError("No supported terminal emulator found (gnome-terminal, x-terminal-emulator, or xterm).")


def default_graph_env_exports(server_name: str) -> str:
    scoped_provider = "DOC_GRAPH_PROVIDER" if server_name == "doc-tiny" else "CODE_GRAPH_PROVIDER"
    return (
        "# Default local graph backend for make start.\n"
        'export GRAPH_PROVIDER="${GRAPH_PROVIDER:-falkordb}"\n'
        f'export {scoped_provider}="${{{scoped_provider}:-${{GRAPH_PROVIDER}}}}"\n'
        'export FALKORDB_HOST="${FALKORDB_HOST:-localhost}"\n'
        'export FALKORDB_PORT="${FALKORDB_PORT:-6379}"\n'
        'export FALKORDB_URI="${FALKORDB_URI:-redis://${FALKORDB_HOST}:${FALKORDB_PORT}}"\n'
        'export FALKORDB_GRAPH="${FALKORDB_GRAPH:-hyper_graph}"\n'
        'export FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-}"\n'
    )


def validate_instance_name(value: str) -> str:
    if not INSTANCE_NAME.fullmatch(value):
        raise RuntimeError("Instance name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}.")
    return value


def selected_servers(options: argparse.Namespace) -> list[dict[str, object]]:
    selected = [dict(server) for server in SERVERS if options.server == "all" or server["name"].startswith(options.server)]
    if options.port is not None and options.server == "all":
        raise RuntimeError("--port requires --server code or --server doc; use --code-port/--doc-port for both.")
    if options.port is not None:
        selected[0]["port"] = options.port
    for server in selected:
        if server["name"] == "code-tiny" and options.code_port is not None:
            if options.port is not None:
                raise RuntimeError("Use either --port or --code-port, not both.")
            server["port"] = options.code_port
        if server["name"] == "doc-tiny" and options.doc_port is not None:
            if options.port is not None:
                raise RuntimeError("Use either --port or --doc-port, not both.")
            server["port"] = options.doc_port
    ports = [int(server["port"]) for server in selected]
    if len(ports) != len(set(ports)):
        raise RuntimeError("Each selected MCP server must use a different port.")
    return selected


def runtime_overrides(
    options: argparse.Namespace,
    server_name: str,
    instance: str,
    multiple_servers: bool,
) -> dict[str, str]:
    is_code = server_name == "code-tiny"
    database = options.code_database if is_code else options.doc_database
    database = database or options.database or options.project
    collection = options.code_collection if is_code else options.doc_collection
    collection = collection or options.collection or options.project
    mcp_name = f"{instance}-{'code' if is_code else 'doc'}" if multiple_servers else instance
    overrides = {"MCP_SERVER_NAME": mcp_name}
    if options.project:
        overrides.update({"PROJECT_ID": options.project, "PROJECT_NAME": options.project})
    if database:
        overrides.update({"FALKORDB_GRAPH": database, "NEO4J_DB": database})
    if collection:
        overrides["QDRANT_COLLECTION" if is_code else "QDRANT_COLLECTION_DOC"] = collection
    if options.provider:
        overrides["GRAPH_PROVIDER"] = options.provider
        overrides["CODE_GRAPH_PROVIDER" if is_code else "DOC_GRAPH_PROVIDER"] = options.provider
    return overrides


def invoke_start(options: argparse.Namespace | None = None) -> None:
    custom = options is not None
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if custom:
        instance = validate_instance_name(options.name or options.project or options.database or options.server)
        servers = selected_servers(options)
        invoke_stop(instance)
        records = read_pid_records()
        for server in servers:
            if tcp_port_open(options.host, int(server["port"])):
                raise RuntimeError(f"Port already in use: {options.host}:{server['port']}")
    else:
        instance = "default"
        servers = [dict(server) for server in SERVERS]
        invoke_stop()
        records = []

    for server in servers:
        script = Path(server["script"])
        if not script.is_file():
            raise RuntimeError(f"MCP script not found: {script}")
        state_name = f"{instance}-{server['name']}" if custom else str(server["name"])
        wrapper = STATE_DIR / f"start-{state_name}.command"
        pid_path = STATE_DIR / f"{state_name}.pid"
        runtime_env_path = STATE_DIR / f"{state_name}.active.env"
        runtime_env = runtime_environment(ROOT, str(server["name"]))
        if custom:
            runtime_env.update(runtime_overrides(options, str(server["name"]), instance, len(servers) > 1))
        runtime_env_path.write_text(
            format_bash_exports(runtime_env) + ("\n" if runtime_env else ""),
            encoding="utf-8",
        )
        runtime_env_path.chmod(0o600)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s' \"$$\" > {shlex.quote(str(pid_path))}\n"
            f"if [ -f {shlex.quote(str(VENV_DIR / 'bin' / 'activate'))} ]; then\n"
            f"  source {shlex.quote(str(VENV_DIR / 'bin' / 'activate'))}\n"
            "fi\n"
            f"{default_graph_env_exports(str(server['name']))}"
            f"export CORTEX_HARNESS_ENV_FILE={shlex.quote(str(runtime_env_path))}\n"
            f"cd {shlex.quote(str(server['work_dir']))}\n"
            f"exec bash {shlex.quote(str(script))}"
            + (
                " "
                + " ".join(
                    shlex.quote(value)
                    for value in (
                        "--host",
                        options.host,
                        "--port",
                        str(server["port"]),
                        "--path",
                        options.path,
                    )
                )
                if custom
                else ""
            )
            + "\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        pid_path.unlink(missing_ok=True)
        run(terminal_command(wrapper))
        deadline = time.monotonic() + 5
        while not pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not pid_path.is_file():
            raise RuntimeError(f"Terminal opened, but {server['name']} did not report its process ID.")
        pid = int(pid_path.read_text(encoding="utf-8"))
        record = {"name": server["name"], "pid": pid, "script": str(script), "port": server["port"]}
        if custom:
            record.update(
                {
                    "instance": instance,
                    "host": options.host,
                    "path": options.path,
                    "endpoint": f"http://{options.host}:{server['port']}{options.path}",
                }
            )
        records.append(record)
        label = f"{instance}/{server['name']}" if custom else str(server["name"])
        if custom:
            print(f"[start] Started {label} in terminal PID {pid} on {server['port']}")
        else:
            print(f"[start] Started {label} in terminal PID {pid}")

    write_pid_records(records)
    print("[start] MCP terminals opened. Logs are visible in their own windows.")


ACTIONS = {
    "build": invoke_build,
    "install": invoke_install,
    "uninstall": invoke_uninstall,
    "infra-up": invoke_infra_up,
    "infra-down": invoke_infra_down,
    "storage-init": invoke_storage_init,
    "storage-stop": lambda: print("[storage-stop] Local storage has no lifecycle to stop."),
    "doctor": invoke_doctor,
    "start": invoke_start,
    "stop": invoke_stop,
    "help": lambda: print(USAGE, end=""),
}


def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def start_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp-lifecycle.py start")
    parser.add_argument("--server", choices=("all", "code", "doc"), default="all")
    parser.add_argument("--name")
    parser.add_argument("--project")
    parser.add_argument("--database", "--db")
    parser.add_argument("--code-database")
    parser.add_argument("--doc-database")
    parser.add_argument("--port", type=port_number)
    parser.add_argument("--code-port", type=port_number)
    parser.add_argument("--doc-port", type=port_number)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--path", default="/mcp")
    parser.add_argument("--provider", choices=("falkordb", "neo4j"))
    parser.add_argument("--collection")
    parser.add_argument("--code-collection")
    parser.add_argument("--doc-collection")
    options = parser.parse_args(arguments)
    if not options.path.startswith("/"):
        options.path = "/" + options.path
    if options.server == "code" and options.doc_port is not None:
        parser.error("--doc-port cannot be used with --server code")
    if options.server == "doc" and options.code_port is not None:
        parser.error("--code-port cannot be used with --server doc")
    return options


def stop_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp-lifecycle.py stop")
    parser.add_argument("--name")
    options = parser.parse_args(arguments)
    if options.name:
        validate_instance_name(options.name)
    return options


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    action = arguments[0] if arguments else "help"
    if action not in ACTIONS:
        print(USAGE, end="")
        return 2
    try:
        if action == "start":
            invoke_start(start_options(arguments[1:]) if len(arguments) > 1 else None)
        elif action == "stop":
            options = stop_options(arguments[1:]) if len(arguments) > 1 else None
            invoke_stop(options.name if options else None)
        elif len(arguments) > 1:
            print(USAGE, end="")
            return 2
        else:
            ACTIONS[action]()
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[error] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
