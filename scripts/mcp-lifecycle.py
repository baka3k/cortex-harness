#!/usr/bin/env python3
"""POSIX lifecycle commands used by the root Makefile and global dev CLI."""

from __future__ import annotations

import json
import os
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


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".cache" / "mcp"
PID_FILE = STATE_DIR / "pids.json"
VENV_DIR = ROOT / ".venv"

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

INFRA_SERVICES = (
    {
        "name": "qdrant",
        "container": "cortex-qdrant",
        "image": "qdrant/qdrant",
        "ports": ("6333:6333",),
        "host": "127.0.0.1",
        "port": 6333,
        "ready_url": "http://127.0.0.1:6333",
    },
    {
        "name": "falkordb",
        "container": "cortex-falkordb",
        "image": "falkordb/falkordb",
        "ports": ("6379:6379",),
        "host": "127.0.0.1",
        "port": 6379,
        "ready_url": "",
    },
)

USAGE = """Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Pull/start local Qdrant and FalkorDB containers.
  make infra-down  | dev infra-down  Stop the containers started by infra-up.
  make doctor      | dev doctor      Check Python deps, Docker, databases, and MCP ports.
  make start       | dev start       Open each MCP server in a separate terminal window.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Default MCP servers:
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local infrastructure:
  qdrant    http://127.0.0.1:6333
  falkordb  redis://127.0.0.1:6379
"""


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


def start_infra_service(docker: str, service: dict[str, object]) -> None:
    name = str(service["container"])
    if container_exists(docker, name):
        if container_running(docker, name):
            print(f"[infra] {name} is already running.")
        else:
            print(f"[infra] Starting existing container: {name}")
            run([docker, "start", name])
    else:
        image = str(service["image"])
        ensure_docker_image(docker, image)
        arguments = [docker, "run", "-d", "--name", name, "--restart", "unless-stopped"]
        for port in service["ports"]:
            arguments.extend(("-p", str(port)))
        arguments.append(image)
        print(f"[infra] Creating container: {name}")
        run(arguments)

    host, port = str(service["host"]), int(service["port"])
    if not wait_for_port(host, port):
        raise RuntimeError(f"{service['name']} did not open {host}:{port} within 30 seconds.")
    ready_url = str(service["ready_url"])
    if not http_ready(ready_url):
        raise RuntimeError(f"{service['name']} is listening, but {ready_url} did not return a healthy response.")
    print(f"[infra] {service['name']} ready on {host}:{port}")


def invoke_infra_up() -> None:
    docker = docker_command()
    for service in INFRA_SERVICES:
        start_infra_service(docker, service)
    print("[infra] Local infrastructure is ready.")


def invoke_infra_down() -> None:
    docker = docker_command()
    for service in INFRA_SERVICES:
        name = str(service["container"])
        if not container_exists(docker, name):
            print(f"[infra] Container not found, skipping: {name}")
        elif not container_running(docker, name):
            print(f"[infra] Container already stopped: {name}")
        else:
            print(f"[infra] Stopping container: {name}")
            run([docker, "stop", name])
    print("[infra] Local infrastructure stopped.")


def doctor_check(name: str, ok: bool, message: str, *, required: bool = True) -> int:
    if ok:
        print(f"[doctor][ok]   {name} - {message}")
        return 0
    level = "fail" if required else "warn"
    print(f"[doctor][{level}] {name} - {message}")
    return int(required)


def invoke_doctor() -> None:
    failures = 0
    try:
        python = venv_python()
        failures += doctor_check("python venv", True, str(python))
    except RuntimeError as error:
        python = None
        failures += doctor_check("python venv", False, str(error))

    if python:
        dependencies = "neo4j, falkordb, qdrant_client, requests"
        result = run([str(python), "-c", "import neo4j, falkordb, qdrant_client, requests"], capture=True, check=False)
        failures += doctor_check("python deps", result.returncode == 0, dependencies)

    docker = shutil.which("docker")
    docker_ready = False
    if docker:
        failures += doctor_check("docker cli", True, docker)
        docker_ready = run([docker, "info"], capture=True, check=False).returncode == 0
        message = "Docker daemon reachable" if docker_ready else "Docker daemon not reachable"
        failures += doctor_check("docker daemon", docker_ready, message)
    else:
        failures += doctor_check("docker cli", False, "Docker not found on PATH")

    for service in INFRA_SERVICES:
        host, port = str(service["host"]), int(service["port"])
        failures += doctor_check(f"{service['name']} port", tcp_port_open(host, port), f"{host}:{port}")
        if service["ready_url"]:
            url = str(service["ready_url"])
            failures += doctor_check(f"{service['name']} http", http_ready(url), url)
        if docker_ready and docker:
            running = container_exists(docker, str(service["container"])) and container_running(
                docker, str(service["container"])
            )
            doctor_check(f"{service['name']} container", running, str(service["container"]), required=False)

    for server in SERVERS:
        doctor_check(
            f"{server['name']} mcp",
            tcp_port_open("127.0.0.1", int(server["port"])),
            f"127.0.0.1:{server['port']}",
            required=False,
        )

    if failures:
        raise RuntimeError(f"Doctor found {failures} required check(s) failing.")
    print("[doctor] Required checks passed.")


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


def invoke_stop() -> None:
    processes = process_table()
    stopped: set[int] = set()
    if PID_FILE.is_file():
        try:
            records = json.loads(PID_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            records = []
        for record in records:
            pid = int(record.get("pid", 0))
            command = processes.get(pid, (0, ""))[1]
            script = str(record.get("script", ""))
            if pid > 1 and script and script in command:
                print(f"[stop] Stopping saved process {pid} ({record.get('name', 'unknown')})")
                stop_process_tree(pid, processes)
                stopped.add(pid)
            elif pid in processes:
                print(f"[stop] Skipping stale PID record {pid} ({record.get('name', 'unknown')})")

    markers = ("code-tiny/mcp.sh", "doc-tiny/mcp.sh", "mcp/unified_mcp.py", "mcp_graph_rag.py")
    for pid, (_, command) in processes.items():
        if pid != os.getpid() and pid not in stopped and str(ROOT) in command and any(marker in command for marker in markers):
            print(f"[stop] Stopping MCP process {pid}")
            stop_process_tree(pid, processes)

    PID_FILE.unlink(missing_ok=True)
    print("[stop] MCP stop complete.")


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
        'export FALKORDB_GRAPH="${FALKORDB_GRAPH:-neo4j}"\n'
        'export FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-}"\n'
    )


def invoke_start() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    invoke_stop()
    records = []
    for server in SERVERS:
        script = Path(server["script"])
        if not script.is_file():
            raise RuntimeError(f"MCP script not found: {script}")
        wrapper = STATE_DIR / f"start-{server['name']}.command"
        pid_path = STATE_DIR / f"{server['name']}.pid"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"printf '%s' \"$$\" > {shlex.quote(str(pid_path))}\n"
            f"if [ -f {shlex.quote(str(VENV_DIR / 'bin' / 'activate'))} ]; then\n"
            f"  source {shlex.quote(str(VENV_DIR / 'bin' / 'activate'))}\n"
            "fi\n"
            f"{default_graph_env_exports(str(server['name']))}"
            f"cd {shlex.quote(str(server['work_dir']))}\n"
            f"exec bash {shlex.quote(str(script))}\n",
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
        records.append({"name": server["name"], "pid": pid, "script": str(script), "port": server["port"]})
        print(f"[start] Started {server['name']} in terminal PID {pid}")

    PID_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print("[start] MCP terminals opened. Logs are visible in their own windows.")


ACTIONS = {
    "build": invoke_build,
    "install": invoke_install,
    "uninstall": invoke_uninstall,
    "infra-up": invoke_infra_up,
    "infra-down": invoke_infra_down,
    "doctor": invoke_doctor,
    "start": invoke_start,
    "stop": invoke_stop,
    "help": lambda: print(USAGE, end=""),
}


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    action = arguments[0] if arguments else "help"
    if action not in ACTIONS or len(arguments) > 1:
        print(USAGE, end="")
        return 2
    try:
        ACTIONS[action]()
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[error] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
