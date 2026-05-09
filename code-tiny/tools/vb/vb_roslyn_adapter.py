from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Dict, List, Optional, Tuple

_BUILD_LOCK = threading.Lock()
_BUILD_CACHE: Dict[str, str] = {}


def _normalize_rel(path: str) -> str:
    return (path or "").replace("\\", "/")


def _default_worker_project() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(here, "roslyn_worker", "RoslynVbWorker.csproj")


def _worker_dll_path(project_path: str) -> str:
    project_dir = os.path.dirname(project_path)
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    release_dir = os.path.join(project_dir, "bin", "Release")
    preferred = [
        os.path.join(release_dir, "net9.0", f"{project_name}.dll"),
        os.path.join(release_dir, "net8.0", f"{project_name}.dll"),
    ]
    existing = [path for path in preferred if os.path.exists(path)]
    if not existing and os.path.isdir(release_dir):
        for tfm in sorted(os.listdir(release_dir), reverse=True):
            if not tfm.startswith("net"):
                continue
            candidate = os.path.join(release_dir, tfm, f"{project_name}.dll")
            if os.path.exists(candidate):
                existing.append(candidate)
    if not existing:
        return preferred[0]
    installed_majors = _installed_dotnet_runtime_majors()
    for path in existing:
        tfm = os.path.basename(os.path.dirname(path))
        major = _tfm_major(tfm)
        if major and major in installed_majors:
            return path
    return existing[0]


def _tfm_major(tfm: str) -> Optional[str]:
    match = re.match(r"net(\d+)", tfm)
    return match.group(1) if match else None


def _installed_dotnet_runtime_majors() -> set[str]:
    try:
        proc = subprocess.run(
            ["dotnet", "--list-runtimes"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except Exception:
        return set()
    majors: set[str] = set()
    for line in (proc.stdout or "").splitlines():
        if not line.startswith("Microsoft.NETCore.App "):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        major = parts[1].split(".", 1)[0]
        if major.isdigit():
            majors.add(major)
    return majors


def _ensure_worker_built(project_path: str, *, verbose: bool = False) -> str:
    project_path = os.path.abspath(project_path)
    with _BUILD_LOCK:
        cached = _BUILD_CACHE.get(project_path)
        if cached and os.path.exists(cached):
            return cached

        cmd = ["dotnet", "build", project_path, "-c", "Release"]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout or "").splitlines()[-50:])
            raise RuntimeError(f"roslyn worker build failed ({proc.returncode})\n{tail}")

        dll = _worker_dll_path(project_path)
        if not os.path.exists(dll):
            raise RuntimeError(f"roslyn worker dll not found after build: {dll}")

        _BUILD_CACHE[project_path] = dll
        if verbose:
            print(f"[roslyn] worker built: {dll}", flush=True)
        return dll


def parse_vbnet_files_with_roslyn(
    *,
    root: str,
    files: List[str],
    semantic_mode: str = "auto",
    worker_project_path: Optional[str] = None,
    timeout_sec: float = 600.0,
    workspace_timeout_ms: int = 120000,
    file_timeout_ms: int = 60000,
    parse_cache_version: str = "vb-family-v2026-04-03-2",
    verbose: bool = False,
) -> Tuple[Dict[str, Dict], Dict[str, str], Dict[str, object]]:
    """Parse VB.NET files via Roslyn worker.

    Returns:
        (success_payloads_by_relpath, errors_by_relpath, worker_meta)
    """

    if not files:
        return {}, {}, {
            "workspace_kind": "none",
            "solution_or_project_path": "",
            "semantic_enabled": False,
            "semantic_errors": [],
        }

    root_abs = os.path.realpath(os.path.abspath(root))
    rel_files: List[str] = []
    for path in files:
        abs_path = os.path.realpath(os.path.abspath(path))
        if abs_path.startswith(root_abs + os.sep):
            rel = os.path.relpath(abs_path, root_abs)
        else:
            rel = abs_path
        rel_files.append(_normalize_rel(rel))

    project_path = worker_project_path or _default_worker_project()
    dll_path = _ensure_worker_built(project_path, verbose=verbose)

    manifest_file = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            manifest_file = handle.name
            json.dump({"files": rel_files}, handle, ensure_ascii=True)

        cmd = [
            "dotnet",
            dll_path,
            "--root",
            root_abs,
            "--files-manifest",
            manifest_file,
            "--semantic",
            semantic_mode,
            "--workspace-timeout-ms",
            str(max(5000, int(workspace_timeout_ms))),
            "--file-timeout-ms",
            str(max(5000, int(file_timeout_ms))),
            "--parse-cache-version",
            parse_cache_version,
        ]

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )

        if proc.returncode != 0:
            stderr_tail = "\n".join((proc.stderr or "").splitlines()[-80:])
            stdout_tail = "\n".join((proc.stdout or "").splitlines()[-80:])
            raise RuntimeError(
                "roslyn worker execution failed "
                f"(code={proc.returncode})\nSTDERR:\n{stderr_tail}\nSTDOUT:\n{stdout_tail}"
            )

        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            snippet = (proc.stdout or "")[:2000]
            raise RuntimeError(f"invalid roslyn worker json output: {exc}\n{snippet}") from exc

        success: Dict[str, Dict] = {}
        errors: Dict[str, str] = {}

        for item in data.get("results", []) or []:
            rel = _normalize_rel(str(item.get("file_path") or "").strip())
            ok = bool(item.get("ok", False))
            payload = item.get("payload")
            if ok and isinstance(payload, dict):
                success[rel] = payload
            elif rel:
                errors[rel] = str(item.get("error") or "roslyn parse failed")

        meta = {
            "workspace_kind": data.get("workspace_kind") or "none",
            "solution_or_project_path": data.get("solution_or_project_path") or "",
            "semantic_enabled": bool(data.get("semantic_enabled", False)),
            "semantic_errors": data.get("semantic_errors") or [],
        }
        return success, errors, meta
    finally:
        if manifest_file and os.path.exists(manifest_file):
            try:
                os.remove(manifest_file)
            except OSError:
                pass
