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
STATE_DIR = ROOT / ".cache" / "mcp"
PID_FILE = STATE_DIR / "pids.json"
VENV_DIR = ROOT / ".venv"
RUST_CORE_DIR = ROOT / "rust-analyzer-core"

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
        "run_args": ("--maxmemory", "4gb", "--maxmemory-policy", "noeviction"),
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

Parameterized MCP instances:
  dev start --server code --name shop --project SHOP --port 8790
  dev start --name shop --project SHOP --code-port 8790 --doc-port 8791
  dev stop --name shop
  make start START_ARGS="--server code --name shop --project SHOP --port 8790"
  make stop STOP_ARGS="--name shop"

Default MCP servers:
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local infrastructure:
  qdrant    http://127.0.0.1:6333
  falkordb  redis://127.0.0.1:6379
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


def verify_rust_extension(python: Path) -> None:
    run([str(python), "-c", "import cortex_extract"])


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
    manifest = RUST_CORE_DIR / "Cargo.toml"
    if not manifest.is_file():
        raise RuntimeError(f"Rust extension manifest not found: {manifest}")
    if not shutil.which("cargo"):
        raise RuntimeError("Cargo was not found on PATH. Install Rust before running make build.")
    print("[build] Building and installing Rust extension")
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(RUST_CORE_DIR),
        ]
    )
    verify_rust_extension(python)
    print("[build] Dependency and Rust extension sync complete.")


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


def wait_for_http(url: str, timeout: int = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if http_ready(url):
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
        for arg in service.get("run_args", ()):
            arguments.append(str(arg))
        print(f"[infra] Creating container: {name}")
        run(arguments)

    host, port = str(service["host"]), int(service["port"])
    if not wait_for_port(host, port):
        raise RuntimeError(f"{service['name']} did not open {host}:{port} within 30 seconds.")
    ready_url = str(service["ready_url"])
    if not wait_for_http(ready_url):
        raise RuntimeError(f"{service['name']} is listening, but {ready_url} was not healthy within 30 seconds.")
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
        dependencies = "neo4j, falkordb, qdrant_client, requests, cortex_extract"
        result = run(
            [str(python), "-c", "import neo4j, falkordb, qdrant_client, requests, cortex_extract"],
            capture=True,
            check=False,
        )
        failures += doctor_check("python + Rust deps", result.returncode == 0, dependencies)

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
    verify_rust_extension(venv_python())
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
        write_pid_records(records)
        host = options.host if custom else "127.0.0.1"
        if not wait_for_port(host, int(server["port"])):
            invoke_stop(instance if custom else None)
            raise RuntimeError(f"{server['name']} did not become ready on {host}:{server['port']}")
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
