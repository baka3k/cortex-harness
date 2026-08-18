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
    # before (and independently of) an editable ``uv pip install -e .``.
    sys.path.insert(0, str(ROOT))

STATE_DIR = ROOT / ".cache" / "mcp"
PID_FILE = STATE_DIR / "pids.json"
VENV_DIR = ROOT / ".venv"
DEV_CONFIG = Path(".cortext-harness") / "config" / "dev.json"

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


def sync_processes(*args, **kwargs):
    """Load the optional process runtime only for commands that need it."""
    from cortex_harness.sync_processes import sync_processes as discover_sync_processes

    return discover_sync_processes(*args, **kwargs)


def embedded_falkordb_pids(*args, **kwargs):
    """Load psutil-backed discovery lazily so build/help can bootstrap."""
    from cortex_harness.sync_processes import embedded_falkordb_pids as discover_pids

    return discover_pids(*args, **kwargs)


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
  make infra-up    | dev infra-up    Initialize local storage; probe remote projects
                                      (pass INFRA_ARGS="--provision" to provision).
  make infra-down  | dev infra-down  Close cached remote clients (local: no-op).
  make storage-layout               Show instance paths, manifest, and current leases.
  make storage-init                 Create the canonical instance tree and manifest.
  make storage-migrate-layout       Dry-run legacy repository-local migration.
  make storage-backup               Create a verified owner backup (OWNER=code|doc).
  make doctor      | dev doctor      Check local storage and list every running Cortex MCP.
                                      Also reports active code/doc sync workers and
                                      remote-backend reachability.
  make sync code stop                Stop code sync workers and descendants.
  make sync doc stop                 Stop document sync workers and descendants.
  make start       | dev start       Load the nearest project dev.json and open both MCPs.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Parameterized MCP instances:
  dev start --server code --name shop --project SHOP --port 8790
  dev start --name shop --project SHOP --code-port 8790 --doc-port 8791
  dev stop --name shop
  make start START_ARGS="--server code --name shop --project SHOP --port 8790"
  make stop STOP_ARGS="--name shop"

Default MCP ports (occupied ports are advanced automatically):
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local storage:
  data root     ~/.cortext-harness/v1/instances/default
  qdrant code  <data-root>/qdrant/code
  qdrant doc   <data-root>/qdrant/doc
  falkordb     <data-root>/falkordb/{code,doc}/data.rdb
"""

INSTANCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MCP_PROCESS_MARKERS = (
    "code-tiny/mcp.sh",
    "doc-tiny/mcp.sh",
    "mcp/unified_mcp.py",
    "mcp_graph_rag.py",
)
RUNTIME_METADATA_KEYS = frozenset(
    {
        "FALKORDB_GRAPH",
        "NEO4J_DB",
        "FALKORDB_PATH",
        "FALKORDB_CODE_PATH",
        "FALKORDB_DOC_PATH",
        "CORTEX_HARNESS_CONFIG_PATH",
    }
)


def runtime_environment(
    root: Path,
    server_name: str,
    config_path: Path | None = None,
) -> dict[str, str]:
    """Load runtime configuration only for start operations.

    Keeping this import lazy lets `make help` and other bootstrap commands run
    before the virtual environment dependencies have been installed.
    """
    from mcp_runtime_config import runtime_environment as resolve_runtime_environment

    return resolve_runtime_environment(root, server_name, config_path)


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


def uv_executable() -> str:
    configured = os.environ.get("UV", "uv")
    executable = shutil.which(configured)
    if not executable:
        raise RuntimeError(
            f"uv was not found on PATH (configured as {configured!r}). "
            "Install uv before running make build."
        )
    return executable


def requirement_files() -> list[Path]:
    candidates = (
        ROOT / "requirements.txt",
        ROOT / "code-tiny" / "requirements.txt",
        ROOT / "doc-tiny" / "requirements.txt",
    )
    return [requirements for requirements in candidates if requirements.is_file()]


def invoke_build() -> None:
    uv = uv_executable()
    if not VENV_DIR.exists():
        launcher = shutil.which("python3") or shutil.which("python")
        if not launcher:
            raise RuntimeError("Python was not found on PATH. Install Python 3.12+ before running make build.")
        print(f"[build] Creating venv: {VENV_DIR}")
        run([uv, "venv", "--python", launcher, str(VENV_DIR)])

    python = venv_python()
    requirements = requirement_files()
    for requirements_file in requirements:
        print(f"[build] Including requirements: {requirements_file}")
    print("[build] Syncing dependencies and editable root package with uv")
    arguments = [uv, "pip", "install", "--python", str(python)]
    for requirements_file in requirements:
        arguments.extend(("--requirements", str(requirements_file)))
    arguments.extend(("--editable", str(ROOT)))
    run(arguments)
    print("[build] Dependency sync complete (uv).")


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


def _scan_project_backends(config_dir: Path | None = None) -> list[dict[str, object]]:
    """Scan ``.cortext-harness/config/*.json`` and classify by backend mode.

    Returns a list of dicts with keys ``project_id``, ``backend_mode``,
    ``remote_config`` and ``config_path``. Malformed JSON files are silently
    skipped so a single broken config never blocks ``infra-up``.
    """
    base = config_dir if config_dir is not None else ROOT / ".cortext-harness" / "config"
    if not base.is_dir():
        return []
    projects: list[dict[str, object]] = []
    for config_path in sorted(base.glob("*.json")):
        try:
            document = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(document, dict):
            continue
        project_section = document.get("project", {})
        project_id = (
            str(project_section.get("code") or "").strip()
            or config_path.stem
        )
        backend = str(document.get("storage_backend") or "local")
        remote_section = document.get("remote")
        projects.append({
            "project_id": project_id,
            "backend_mode": backend,
            "remote_config": remote_section,
            "config_path": str(config_path),
        })
    return projects


def _resolve_collection_names(
    project_id: str,
    config_path: str,
) -> dict[str, str]:
    """Read collection/graph names from project config env sections.

    Falls back to conventional ``{project_id}_code`` / ``{project_id}_doc``
    names when the project config does not override them. The lookup is
    forgiving: missing files, malformed JSON, or missing sections yield the
    default convention rather than an error.
    """
    defaults = {
        "code_collection": f"{project_id}_code",
        "doc_collection": f"{project_id}_doc",
        "code_graph": "hyper_graph",
        "doc_graph": f"{project_id}_doc",
    }
    try:
        document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return defaults
    if not isinstance(document, dict):
        return defaults
    code_env = document.get("code", {}).get("env", {}) or {}
    doc_env = document.get("doc", {}).get("env", {}) or {}
    code_collection = (
        str(code_env.get("QDRANT_COLLECTION") or "").strip()
        or str(code_env.get("QDRANT_COLLECTION_CODE") or "").strip()
        or defaults["code_collection"]
    )
    doc_collection = (
        str(doc_env.get("QDRANT_COLLECTION_DOC") or "").strip()
        or str(doc_env.get("QDRANT_COLLECTION") or "").strip()
        or defaults["doc_collection"]
    )
    code_graph = (
        str(code_env.get("FALKORDB_GRAPH") or "").strip()
        or defaults["code_graph"]
    )
    doc_graph = (
        str(doc_env.get("DOC_FALKORDB_GRAPH") or "").strip()
        or str(doc_env.get("FALKORDB_GRAPH") or "").strip()
        or defaults["doc_graph"]
    )
    return {
        "code_collection": code_collection,
        "doc_collection": doc_collection,
        "code_graph": code_graph,
        "doc_graph": doc_graph,
    }


def _provision_remote_project(
    project_id: str,
    remote_config,
    *,
    config_path: str = "",
) -> None:
    """Provision remote resources for one project."""
    from cortex_harness.storage.remote_probe import (
        provision_falkordb_graph,
        provision_qdrant_collection,
        render_provision_line,
        setup_remote_falkordb_schema,
    )

    if config_path:
        names = _resolve_collection_names(project_id, config_path)
    else:
        names = {
            "code_collection": f"{project_id}_code",
            "doc_collection": f"{project_id}_doc",
            "code_graph": "hyper_graph",
            "doc_graph": f"{project_id}_doc",
        }
    results = []
    if remote_config.qdrant_url:
        results.append(provision_qdrant_collection(remote_config, names["code_collection"]))
        results.append(provision_qdrant_collection(remote_config, names["doc_collection"]))
    if remote_config.falkordb_uri:
        results.append(provision_falkordb_graph(remote_config, names["code_graph"]))
        results.append(provision_falkordb_graph(remote_config, names["doc_graph"]))
        results.append(setup_remote_falkordb_schema(remote_config, names["code_graph"]))
        results.append(setup_remote_falkordb_schema(remote_config, names["doc_graph"]))
    for result in results:
        print(f"[infra-up]     {render_provision_line(result)}")


def invoke_infra_up(*, provision: bool = False) -> None:
    """Initialize storage for all registered projects.

    Local projects get their instance tree created (same as ``storage-init``).
    Remote projects get connectivity validated; with ``provision=True`` the
    required collections and graphs are created on the remote servers.
    """
    from cortex_harness.storage.config import validate_backend_config
    from cortex_harness.storage.remote_probe import probe_all

    # Always ensure local layout exists (backward compatible).
    resolved = _resolved_storage()
    from cortex_harness.storage.layout import ensure_layout
    ensure_layout(resolved)

    projects = _scan_project_backends()
    remote_projects = [project for project in projects if project["backend_mode"] == "remote"]
    local_count = len(projects) - len(remote_projects)

    if local_count:
        print(f"[infra-up] {local_count} local project(s) — storage initialized")
    print(f"[infra-up] data root: {resolved.data_root}")
    print(f"[infra-up] manifest : {resolved.manifest_path}")

    if not remote_projects:
        return

    failures = 0
    for project in remote_projects:
        project_id = str(project["project_id"])
        remote_section = project["remote_config"]
        try:
            _, remote_config = validate_backend_config("remote", remote_section)
        except ValueError as exc:
            print(f"[infra-up] [fail] {project_id}: {exc}")
            failures += 1
            continue

        print(f"[infra-up] {project_id} (remote):")
        results = probe_all(remote_config)
        for result in results:
            tag = "[ok]" if result.reachable else "[fail]"
            print(f"[infra-up]   {tag} {result.backend}: {result.message}")
            if not result.reachable:
                failures += 1

        if provision and all(result.reachable for result in results):
            _provision_remote_project(
                project_id,
                remote_config,
                config_path=str(project.get("config_path", "")),
            )

    if failures:
        print(f"[infra-up] {failures} check(s) failed")
        raise SystemExit(1)
    print("[infra-up] all remote projects reachable")


def invoke_infra_down() -> None:
    """Tear down storage lifecycle.

    Local projects: no-op (files persist on disk). Remote projects: close
    cached remote clients so subsequent processes reconnect cleanly.
    """
    from cortex_harness.storage.qdrant_remote import reset_remote_clients

    reset_remote_clients()
    print("[infra-down] remote client connections closed")


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


def doctor_remote_checks() -> int:
    """Check remote backend connectivity for all remote-mode projects.

    Returns the number of failed checks. Honors
    ``CORTEX_STORAGE_BACKEND_FORCE_LOCAL`` by reporting a passing
    bypass message instead of touching the network — this keeps the
    rollback path observable in ``make doctor`` output.
    """
    from cortex_harness.storage.remote_probe import force_local_active

    if force_local_active():
        return doctor_check(
            "remote backends",
            True,
            "bypassed (CORTEX_STORAGE_BACKEND_FORCE_LOCAL=1)",
            required=False,
        )

    from cortex_harness.storage.config import validate_backend_config
    from cortex_harness.storage.remote_probe import probe_all

    projects = _scan_project_backends()
    remote_projects = [
        project for project in projects if project["backend_mode"] == "remote"
    ]
    if not remote_projects:
        return doctor_check(
            "remote projects",
            True,
            "none configured",
            required=False,
        )

    failures = 0
    for project in remote_projects:
        project_id = str(project["project_id"])
        remote_section = project["remote_config"]
        try:
            _, remote_config = validate_backend_config("remote", remote_section)
        except ValueError as exc:
            failures += doctor_check(
                f"remote:{project_id}:config",
                False,
                str(exc),
            )
            continue

        for result in probe_all(remote_config):
            # ``probe_*`` returns a "skipped" message when no URL is set.
            if result.message.startswith("skipped"):
                continue
            failures += doctor_check(
                f"remote:{project_id}:{result.backend}",
                result.reachable,
                f"{result.url} — {result.message}",
            )
    return failures


def human_file_size(path_value: object) -> str:
    if not path_value:
        return "unknown"
    try:
        size = Path(str(path_value)).stat().st_size
    except FileNotFoundError:
        return "not created"
    except OSError:
        return "unknown"

    value = float(size)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def doctor_mcp_checks(instances: list[dict[str, object]] | None = None) -> None:
    instances = running_mcp_instances() if instances is None else instances
    if not instances:
        doctor_check("mcp instances", False, "none running", required=False)
        return

    doctor_check("mcp instances", True, f"{len(instances)} running", required=False)
    for index, instance in enumerate(instances):
        if index:
            print("[doctor]")
        endpoint = (
            f"http://{instance.get('host', '127.0.0.1')}:"
            f"{instance.get('port') or '?'}{instance.get('path', '/mcp')}"
        )
        print(f"[doctor]   {instance.get('name', 'mcp')} (pid={instance['pid']})")
        print(f"[doctor]     Endpoint : {endpoint}")
        print(f"[doctor]     Graph    : {instance.get('graph') or 'unknown'}")
        database_path = instance.get("database_path")
        if database_path:
            print(f"[doctor]     DB file  : {database_path}")
            print(f"[doctor]     DB size  : {human_file_size(database_path)}")
        else:
            print(f"[doctor]     Database : {instance.get('database') or 'unknown'}")
            print("[doctor]     DB size  : unavailable")
        print(f"[doctor]     Config   : {instance.get('config_path') or 'unknown'}")


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

    # ── Remote backend checks ─────────────────────────────────────────
    try:
        failures += doctor_remote_checks()
    except Exception as error:
        failures += doctor_check("remote backends", False, str(error))

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

    doctor_mcp_checks()

    if failures:
        raise RuntimeError(_color(f"Doctor found {failures} required check(s) failing.", "red"))
    print(_color("[doctor] Required checks passed.", "green"))


def process_table() -> dict[int, tuple[int, str]]:
    if os.name == "nt":
        try:
            import psutil

            return {
                int(process.info["pid"]): (
                    int(process.info.get("ppid") or 0),
                    subprocess.list2cmdline(process.info.get("cmdline") or []),
                )
                for process in psutil.process_iter(["pid", "ppid", "cmdline"])
            }
        except (ImportError, OSError):
            return {}
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


def record_pid(record: dict[str, object]) -> int:
    try:
        return int(record.get("pid", 0))
    except (TypeError, ValueError):
        return 0


def _read_runtime_metadata(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        if path.suffix.casefold() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return {
                key: str(value)
                for key, value in payload.items()
                if key in RUNTIME_METADATA_KEYS and value is not None
            }

        metadata: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, raw_value = line.partition("=")
            if not separator or key not in RUNTIME_METADATA_KEYS:
                continue
            values = shlex.split(raw_value, posix=True)
            if len(values) == 1:
                metadata[key] = values[0]
        return metadata
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def record_runtime_metadata(record: dict[str, object]) -> dict[str, str]:
    """Resolve graph/file metadata for both current and legacy PID records."""
    candidates: list[Path] = []
    for key in ("runtime_env_path", "RuntimeConfig"):
        value = str(record.get(key) or "").strip()
        if value:
            candidates.append(Path(value))

    name = str(record.get("name") or "").strip()
    instance = str(record.get("instance") or "").strip()
    if name and instance:
        candidates.extend(
            (
                STATE_DIR / f"{instance}-{name}.active.env",
                STATE_DIR / f"{instance}-{name}.active.json",
            )
        )
    if name:
        candidates.extend((STATE_DIR / f"{name}.active.env", STATE_DIR / f"{name}.active.json"))

    values: dict[str, str] = {}
    for candidate in dict.fromkeys(candidates):
        for key, value in _read_runtime_metadata(candidate).items():
            values.setdefault(key, value)
    graph = values.get("FALKORDB_GRAPH") or values.get("NEO4J_DB")
    database_path = (
        values.get("FALKORDB_PATH")
        or values.get("FALKORDB_CODE_PATH")
        or values.get("FALKORDB_DOC_PATH")
    )
    return {
        key: value
        for key, value in {
            "graph": graph,
            "database": values.get("NEO4J_DB"),
            "database_path": database_path,
            "config_path": values.get("CORTEX_HARNESS_CONFIG_PATH"),
        }.items()
        if value
    }


def _process_descendants(pid: int, processes: dict[int, tuple[int, str]]) -> set[int]:
    descendants: set[int] = set()
    pending = [pid]
    while pending:
        parent = pending.pop()
        children = [child for child, (ppid, _) in processes.items() if ppid == parent]
        descendants.update(children)
        pending.extend(children)
    return descendants


def _command_port(command: str) -> int | None:
    match = re.search(r"(?:^|\s)--port(?:=|\s+)(\d+)(?:\s|$)", command)
    return int(match.group(1)) if match else None


def _mcp_name_from_command(command: str) -> str:
    if "doc-tiny" in command or "mcp_graph_rag.py" in command:
        return "doc-tiny"
    if "code-tiny" in command or "mcp/unified_mcp.py" in command:
        return "code-tiny"
    return "mcp"


def running_mcp_instances(
    processes: dict[int, tuple[int, str]] | None = None,
    records: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Return all Cortex MCP processes, merging saved metadata when available."""
    processes = process_table() if processes is None else processes
    records = read_pid_records() if records is None else records
    instances: list[dict[str, object]] = []
    covered: set[int] = set()

    for record in records:
        pid = record_pid(record)
        command = processes.get(pid, (0, ""))[1]
        if pid <= 1 or not command or not any(marker in command for marker in MCP_PROCESS_MARKERS):
            continue
        item = dict(record)
        for key, value in record_runtime_metadata(record).items():
            item.setdefault(key, value)
        item["pid"] = pid
        item.setdefault("port", _command_port(command))
        item.setdefault("host", "127.0.0.1")
        item.setdefault("path", "/mcp")
        instances.append(item)
        covered.add(pid)
        covered.update(_process_descendants(pid, processes))

    untracked = {
        pid
        for pid, (_, command) in processes.items()
        if pid not in covered
        and pid != os.getpid()
        and any(marker in command for marker in MCP_PROCESS_MARKERS)
    }
    for pid in sorted(untracked):
        command = processes[pid][1]
        ancestor = processes.get(pid, (0, ""))[0]
        while ancestor in processes and ancestor not in untracked:
            ancestor = processes[ancestor][0]
        if ancestor in untracked:
            continue
        instances.append(
            {
                "name": _mcp_name_from_command(command),
                "pid": pid,
                "port": _command_port(command),
                "host": "127.0.0.1",
                "path": "/mcp",
            }
        )

    return sorted(instances, key=lambda item: (int(item.get("port") or 65536), int(item["pid"])))


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


def resolve_start_config(start: Path | None = None) -> tuple[Path, Path]:
    """Find the nearest project dev.json, then fall back to the install config."""
    current = (start or Path.cwd()).absolute()
    if current.is_file():
        current = current.parent
    for candidate_root in (current, *current.parents):
        config_path = candidate_root / DEV_CONFIG
        if config_path.is_file():
            return candidate_root, config_path
    return ROOT, ROOT / DEV_CONFIG


def config_instance(environment: dict[str, str]) -> str:
    raw = (
        environment.get("PROJECT_ID")
        or environment.get("CORTEX_STORAGE_INSTANCE")
        or environment.get("FALKORDB_GRAPH")
        or "cortext"
    )
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.")[:64]
    return validate_instance_name(normalized or "cortext")


def next_available_port(host: str, preferred: int, reserved: set[int]) -> int:
    port = preferred
    while port in reserved or tcp_port_open(host, port):
        port += 1
        if port > 65535:
            raise RuntimeError(f"No available MCP port at or above {preferred}.")
    reserved.add(port)
    return port


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
    options = options or start_options([])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    config_root, config_path = resolve_start_config()
    if not config_path.is_file():
        raise RuntimeError(f"MCP config not found: {config_path}")

    servers = selected_servers(options)
    runtime_environments: dict[str, dict[str, str]] = {}
    for server in servers:
        name = str(server["name"])
        environment = runtime_environment(config_root, name, config_path)
        runtime_environments[name] = environment

    if custom:
        instance = validate_instance_name(options.name or options.project or options.database or options.server)
        invoke_stop(instance)
        records = read_pid_records()
        for server in servers:
            if tcp_port_open(options.host, int(server["port"])):
                raise RuntimeError(f"Port already in use: {options.host}:{server['port']}")
    else:
        instance = config_instance(runtime_environments[str(servers[0]["name"])])
        records = read_pid_records()

    processes = process_table() if records else {}
    reserved_ports: set[int] = set()

    for server in servers:
        script = Path(server["script"])
        if not script.is_file():
            raise RuntimeError(f"MCP script not found: {script}")
        runtime_env = runtime_environments[str(server["name"])]
        if custom:
            runtime_env.update(runtime_overrides(options, str(server["name"]), instance, len(servers) > 1))

        graph = runtime_env.get("FALKORDB_GRAPH") or runtime_env.get("NEO4J_DB") or ""
        existing = next(
            (
                record
                for record in records
                if not custom
                and record.get("name") == server["name"]
                and record.get("graph") == graph
                and record_pid(record) in processes
                and any(
                    marker in processes[record_pid(record)][1]
                    for marker in MCP_PROCESS_MARKERS
                )
            ),
            None,
        )
        if existing is not None:
            print(
                f"[start] Reusing {instance}/{server['name']} on "
                f"{existing.get('host', options.host)}:{existing.get('port')} (graph={graph})"
            )
            continue

        if not custom:
            server["port"] = next_available_port(options.host, int(server["port"]), reserved_ports)

        state_name = f"{instance}-{server['name']}"
        wrapper = STATE_DIR / f"start-{state_name}.command"
        pid_path = STATE_DIR / f"{state_name}.pid"
        runtime_env_path = STATE_DIR / f"{state_name}.active.env"
        from cortex_harness.storage import resolve_storage, storage_overlay

        owner = "code" if server["name"] == "code-tiny" else "doc"
        storage_config = resolve_storage(
            config_root,
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
            + " "
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
        record = {
            "name": server["name"],
            "instance": instance,
            "pid": pid,
            "script": str(script),
            "port": server["port"],
            "host": options.host,
            "path": options.path,
            "endpoint": f"http://{options.host}:{server['port']}{options.path}",
            "graph": graph,
            "config_path": str(config_path),
            "runtime_env_path": str(runtime_env_path),
            "project_id": runtime_env.get("PROJECT_ID", ""),
        }
        records.append(record)
        label = f"{instance}/{server['name']}"
        print(f"[start] Started {label} in terminal PID {pid} on {server['port']} (graph={graph})")

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


def infra_up_options(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp-lifecycle.py infra-up")
    parser.add_argument(
        "--provision", action="store_true",
        help="Create collections/graphs on remote servers.",
    )
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
        elif action == "infra-up":
            options = infra_up_options(arguments[1:])
            invoke_infra_up(provision=options.provision)
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
