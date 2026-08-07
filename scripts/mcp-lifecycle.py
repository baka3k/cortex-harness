#!/usr/bin/env python3
"""POSIX lifecycle commands used by the root Makefile and global dev CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # ``python scripts/mcp-lifecycle.py`` makes ``scripts/`` the first import
    # root. Add the repository root explicitly so the source package works
    # before (and independently of) an editable ``pip install -e .``.
    sys.path.insert(0, str(ROOT))

from cortex_harness.sync_processes import embedded_falkordb_pids, sync_processes

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
    centralized per-account persistent storage in Phase 04 of the docker-free
    cutover. ``invoke_storage_init`` creates the canonical instance tree;
    there are no database services to enumerate.
    """
    return ()

USAGE = """Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Deprecated alias for local storage initialization.
  make infra-down  | dev infra-down  Deprecated no-op; local storage has no service lifecycle.
  make storage-layout               Show instance paths, manifest, and current leases.
  make storage-init                 Create the canonical instance tree and manifest.
  make storage-migrate-layout       Dry-run legacy repository-local migration.
  make storage-backup               Create a verified owner backup (OWNER=code|doc).
  make doctor      | dev doctor      Check local Python storage runtime, paths, and MCP ports.
                                      Also reports active code/doc sync workers.
  make sync code stop                Stop code sync workers and descendants.
  make sync doc stop                 Stop document sync workers and descendants.
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
  data root     ~/.cortext-harness/v1/instances/default
  qdrant code  <data-root>/qdrant/code
  qdrant doc   <data-root>/qdrant/doc
  falkordb     <data-root>/falkordb/{code,doc}/data.rdb
"""

INSTANCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def runtime_environment(root: Path, server_name: str) -> dict[str, str]:
    """Load runtime configuration only for start operations.

    Keeping this import lazy lets `make help` and other bootstrap commands run
    before the virtual environment dependencies have been installed.
    """
    from mcp_runtime_config import runtime_environment as resolve_runtime_environment

    return resolve_runtime_environment(root, server_name)


def format_bash_exports(environment: dict[str, str]) -> str:
    from mcp_runtime_config import format_bash_exports as render_bash_exports

    return render_bash_exports(environment)


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
            raise RuntimeError("Python was not found on PATH. Install Python 3.12+ before running make build.")
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


def tcp_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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


def _resolved_storage(root: Path | None = None):
    try:
        from mcp_runtime_config import resolve_active_storage
    except ImportError as error:
        raise RuntimeError(
            "Local storage configuration could not be imported. "
            "Run 'make build' first to install the package in editable mode."
        ) from error
    return resolve_active_storage(Path(root) if root is not None else ROOT)


def _storage_summary(resolved) -> dict[str, object]:
    from cortex_harness.storage.layout import load_manifest

    leases: dict[str, object] = {}
    for owner, backend, target in (
        (resolved.code_owner_id, "qdrant", resolved.qdrant_code_path),
        (resolved.doc_owner_id, "qdrant", resolved.qdrant_doc_path),
        (resolved.code_owner_id, "falkordb", resolved.falkordb_code_path),
        (resolved.doc_owner_id, "falkordb", resolved.falkordb_doc_path),
    ):
        target = Path(target)
        lock_path = target.parent / f".{target.name}.cortex-owner.lock"
        holder: object = None
        if lock_path.is_file():
            try:
                raw = lock_path.read_text(encoding="utf-8").strip()
                holder = json.loads(raw) if raw else None
            except (OSError, json.JSONDecodeError):
                holder = "unreadable"
        leases[f"{owner}:{backend}"] = holder
    return {
        "schema_version": resolved.schema_version,
        "instance_id": resolved.instance_id,
        "data_root": str(resolved.data_root),
        "instance_root": str(resolved.instance_root),
        "qdrant": {
            resolved.code_owner_id: str(resolved.qdrant_code_path),
            resolved.doc_owner_id: str(resolved.qdrant_doc_path),
        },
        "falkordb": {
            resolved.code_owner_id: str(resolved.falkordb_code_path),
            resolved.doc_owner_id: str(resolved.falkordb_doc_path),
        },
        "manifest": load_manifest(resolved),
        "leases": leases,
    }


def invoke_storage_layout() -> None:
    print(json.dumps(_storage_summary(_resolved_storage()), indent=2, sort_keys=True))


def invoke_storage_init() -> None:
    """Create the canonical instance tree and immutable manifest."""
    from cortex_harness.storage.layout import ensure_layout

    resolved = _resolved_storage()
    ensure_layout(resolved)
    print(f"[storage-init] data root     : {resolved.data_root}")
    print(f"[storage-init] instance      : {resolved.instance_id}")
    print(f"[storage-init] Qdrant code   : {resolved.qdrant_code_path}")
    print(f"[storage-init] Qdrant doc    : {resolved.qdrant_doc_path}")
    print(f"[storage-init] FalkorDB code : {resolved.falkordb_code_path}")
    print(f"[storage-init] FalkorDB doc  : {resolved.falkordb_doc_path}")
    print(f"[storage-init] manifest      : {resolved.manifest_path}")


def invoke_storage_migrate_layout(legacy_root: Path, *, apply: bool) -> None:
    from cortex_harness.storage.migration import migrate_legacy_layout

    resolved = _resolved_storage()
    report = migrate_legacy_layout(resolved, legacy_root, dry_run=not apply)
    mode = "apply" if apply else "dry-run"
    print(f"[storage-migrate-layout] mode: {mode}")
    if not report:
        print(f"[storage-migrate-layout] no legacy stores found under {Path(legacy_root).resolve()}")
    for item in report:
        print(f"[storage-migrate-layout] {item.action}: {item.source} -> {item.target} sha256={item.digest}")


def _path_digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        digest.update((item.name if path.is_file() else item.relative_to(path).as_posix()).encode("utf-8"))
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def invoke_storage_backup(owner: str) -> None:
    from cortex_harness.storage.layout import ensure_layout
    from cortex_harness.storage.lease import StorageLease

    resolved = _resolved_storage()
    ensure_layout(resolved)
    owner = owner.casefold()
    if owner not in {resolved.code_owner_id, resolved.doc_owner_id}:
        raise RuntimeError(
            f"Unknown storage owner {owner!r}; choose {resolved.code_owner_id!r} or {resolved.doc_owner_id!r}."
        )
    qdrant_source = resolved.qdrant_code_path if owner == resolved.code_owner_id else resolved.qdrant_doc_path
    falkor_source = Path(resolved.falkordb_code_path if owner == resolved.code_owner_id else resolved.falkordb_doc_path)
    with ExitStack() as leases:
        leases.enter_context(
            StorageLease(qdrant_source, instance_id=resolved.instance_id, owner_id=owner, backend="qdrant")
        )
        leases.enter_context(
            StorageLease(falkor_source, instance_id=resolved.instance_id, owner_id=owner, backend="falkordb")
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = Path(resolved.backups_path) / timestamp
        records: list[dict[str, str]] = []
        for backend, source, target in (
            ("qdrant", Path(qdrant_source), destination / "qdrant" / owner),
            ("falkordb", falkor_source, destination / "falkordb" / owner / "data.rdb"),
        ):
            if not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            source_digest = _path_digest(source)
            if _path_digest(target) != source_digest:
                raise RuntimeError(f"Backup verification failed for {source}")
            records.append({"backend": backend, "source": str(source), "target": str(target), "sha256": source_digest})
        manifest = {
            "schema_version": resolved.schema_version,
            "instance_id": resolved.instance_id,
            "owner_id": owner,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "items": records,
        }
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"[storage-backup] verified backup: {destination}")


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


def doctor_process_checks(resolved: object | None) -> None:
    """Report active sync workers and embedded graph processes without mutation."""

    for owner in ("code", "doc"):
        workers = sync_processes(owner, root=ROOT)
        doctor_check(
            f"{owner} sync workers",
            not workers,
            (
                "idle"
                if not workers
                else "running pid(s): " + ", ".join(str(item.pid) for item in workers)
            ),
            required=False,
        )

    if resolved is None:
        return
    for owner, path in (
        ("code", Path(resolved.falkordb_code_path)),
        ("doc", Path(resolved.falkordb_doc_path)),
    ):
        pids = embedded_falkordb_pids(path)
        doctor_check(
            f"{owner} embedded FalkorDB",
            not pids,
            "idle" if not pids else "running pid(s): " + ", ".join(map(str, pids)),
            required=False,
        )


def invoke_doctor() -> None:
    failures = 0
    resolved = None
    failures += doctor_check(
        "python version",
        sys.version_info >= (3, 12),
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (requires 3.12+)",
    )
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

    # Production paths are inspected, but write probes always use an isolated
    # temporary data root so doctor cannot change registered project data.
    try:
        resolved = _resolved_storage()
        doctor_check("qdrant base path", True, str(resolved.qdrant_base))
        doctor_check("qdrant code path", True, str(resolved.qdrant_code_path))
        doctor_check("qdrant doc path",  True, str(resolved.qdrant_doc_path))
        doctor_check("falkordb code path", True, str(resolved.falkordb_code_path))
        doctor_check("falkordb doc path", True, str(resolved.falkordb_doc_path))
        writable_parent = next((path for path in (Path(resolved.data_root), *Path(resolved.data_root).parents) if path.exists()), None)
        failures += doctor_check(
            "data root parent writable",
            writable_parent is not None and os.access(writable_parent, os.W_OK),
            str(writable_parent or resolved.data_root),
        )
    except Exception as error:
        failures += doctor_check("local storage", False, str(error))

    doctor_process_checks(resolved)

    with tempfile.TemporaryDirectory(prefix="cortex-doctor-") as temporary:
        try:
            from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, reset_clients, resolve_storage
            from qdrant_client.http import models as qmodels

            probe = resolve_storage(ROOT, data_home=temporary, instance_id="doctor")
            for role in (QdrantStorageRole.CODE, QdrantStorageRole.DOCUMENT):
                store = LocalQdrantStore(probe, role)
                collection = f"doctor_{role.value}"
                try:
                    store.create_collection(
                        collection,
                        vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
                    )
                    store.upsert(
                        collection,
                        points=[qmodels.PointStruct(id=1, vector=[0.0, 1.0], payload={"__doctor__": True})],
                    )
                    hits = store.retrieve(collection, ids=[1])
                    failures += doctor_check(
                        f"qdrant {role.value} round-trip",
                        bool(hits) and bool(hits[0].payload.get("__doctor__")),
                        str(store.path),
                    )
                finally:
                    store.close()
            reset_clients()
        except ImportError as error:
            failures += doctor_check("qdrant round-trip", False, f"dependency missing: {error}")
        except Exception as error:
            failures += doctor_check("qdrant round-trip", False, str(error))

        try:
            from redislite.falkordb_client import FalkorDB

            graph_path = Path(temporary) / "falkordb" / "doctor.rdb"
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            client = FalkorDB(str(graph_path))
            try:
                result = client.select_graph("doctor").query("RETURN 1 AS ok")
                failures += doctor_check(
                    "falkordblite round-trip",
                    bool(result.result_set) and result.result_set[0][0] == 1,
                    str(graph_path),
                )
            finally:
                client.close()
        except ImportError as error:
            failures += doctor_check("falkordblite round-trip", False, f"dependency missing: {error}")
        except Exception as error:
            failures += doctor_check("falkordblite round-trip", False, str(error))

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
        'export FALKORDB_GRAPH="${FALKORDB_GRAPH:-hyper_graph}"\n'
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
    overrides = {
        "MCP_SERVER_NAME": mcp_name,
        "CORTEX_STORAGE_INSTANCE": instance.casefold().replace(".", "-"),
        "CORTEX_STORAGE_OWNER": "code" if is_code else "doc",
    }
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
        from cortex_harness.storage import resolve_storage, storage_overlay

        owner = "code" if server["name"] == "code-tiny" else "doc"
        storage_config = resolve_storage(
            ROOT,
            config=runtime_env,
            instance_id=runtime_env.get("CORTEX_STORAGE_INSTANCE", "default"),
            code_graph=runtime_env.get("FALKORDB_GRAPH") if owner == "code" else None,
            doc_graph=runtime_env.get("FALKORDB_GRAPH") if owner == "doc" else None,
            code_collection=runtime_env.get("QDRANT_COLLECTION"),
            doc_collection=runtime_env.get("QDRANT_COLLECTION_DOC"),
        )
        runtime_env.update(storage_overlay(storage_config, owner=owner))
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
    "storage-layout": invoke_storage_layout,
    "storage-init": invoke_storage_init,
    "storage-migrate-layout": invoke_storage_migrate_layout,
    "storage-backup": invoke_storage_backup,
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


def storage_migrate_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp-lifecycle.py storage-migrate-layout")
    parser.add_argument("--legacy-root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true", help="Copy and verify; default is dry-run.")
    return parser.parse_args(arguments)


def storage_backup_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp-lifecycle.py storage-backup")
    parser.add_argument("--owner", choices=("code", "doc"), default="code")
    return parser.parse_args(arguments)


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
        elif action == "storage-migrate-layout":
            options = storage_migrate_options(arguments[1:])
            invoke_storage_migrate_layout(options.legacy_root, apply=options.apply)
        elif action == "storage-backup":
            options = storage_backup_options(arguments[1:])
            invoke_storage_backup(options.owner)
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
