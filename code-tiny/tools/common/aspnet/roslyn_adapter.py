from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Any, Dict, Iterable, Optional, Tuple

from .identity import normalize_relative_path, resolve_inside_root
from .models import ASPNET_PROTOCOL_VERSION


_BUILD_LOCK = threading.Lock()
_BUILD_CACHE: Dict[str, str] = {}


def default_worker_project() -> str:
    return os.path.join(os.path.dirname(__file__), "roslyn_worker", "AspNetRoslynWorker.csproj")


def _runtime_majors() -> set[str]:
    try:
        result = subprocess.run(
            ["dotnet", "--list-runtimes"], capture_output=True, text=True, check=False
        )
    except OSError:
        return set()
    return {
        match.group(1)
        for line in result.stdout.splitlines()
        if (match := re.match(r"Microsoft\.NETCore\.App\s+(\d+)", line))
    }


def _worker_dll(project_path: str) -> str:
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    release = os.path.join(os.path.dirname(project_path), "bin", "Release")
    declared_targets: set[str] = set()
    try:
        with open(project_path, "r", encoding="utf-8") as handle:
            project_text = handle.read(64 * 1024)
        for value in re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", project_text):
            declared_targets.update(item.strip() for item in value.split(";") if item.strip())
    except OSError:
        pass
    candidates: list[tuple[str, str, str]] = []
    if os.path.isdir(release):
        for target in sorted(os.listdir(release), reverse=True):
            if declared_targets and target not in declared_targets:
                continue
            candidate = os.path.join(release, target, f"{project_name}.dll")
            if os.path.isfile(candidate):
                major = (re.match(r"net(\d+)", target) or [None, ""])[1]
                candidates.append((major, target, candidate))
    candidates.sort(key=lambda item: os.path.getmtime(item[2]), reverse=True)
    runtimes = _runtime_majors()
    for major, _, candidate in candidates:
        if major in runtimes:
            return candidate
    if candidates:
        return candidates[0][2]
    fallback_target = sorted(declared_targets)[-1] if declared_targets else "net8.0"
    return os.path.join(release, fallback_target, f"{project_name}.dll")


def ensure_worker_built(project_path: Optional[str] = None, *, verbose: bool = False) -> str:
    project = os.path.realpath(os.path.abspath(project_path or default_worker_project()))
    with _BUILD_LOCK:
        cached = _BUILD_CACHE.get(project)
        if cached and os.path.isfile(cached):
            return cached
        try:
            result = subprocess.run(
                ["dotnet", "build", project, "-c", "Release"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
            )
        except OSError as exc:
            raise RuntimeError(f"dotnet is unavailable: {exc}") from exc
        if result.returncode != 0:
            tail = "\n".join(result.stdout.splitlines()[-80:])
            raise RuntimeError(f"ASP.NET Roslyn worker build failed ({result.returncode})\n{tail}")
        dll = _worker_dll(project)
        if not os.path.isfile(dll):
            raise RuntimeError(f"ASP.NET Roslyn worker DLL was not produced: {dll}")
        _BUILD_CACHE[project] = dll
        if verbose:
            print(f"[aspnet][roslyn] worker={dll}", flush=True)
        return dll


def analyze_csharp_files(
    *,
    root: str,
    files: Iterable[str],
    semantic_mode: str = "auto",
    project_path: str = "",
    worker_project_path: Optional[str] = None,
    timeout_sec: float = 600.0,
    workspace_timeout_ms: int = 120_000,
    file_timeout_ms: int = 60_000,
    max_file_bytes: int = 2 * 1024 * 1024,
    verbose: bool = False,
) -> Dict[str, Any]:
    if semantic_mode not in {"auto", "on", "off"}:
        raise ValueError("semantic_mode must be auto, on, or off")
    root_abs = os.path.realpath(os.path.abspath(root))
    relative_files: list[str] = []
    for path in files:
        _, relative = resolve_inside_root(root_abs, path, require_exists=True)
        if relative.lower().endswith(".cs"):
            relative_files.append(relative)
    relative_files = sorted(set(relative_files))
    if project_path:
        _, project_path = resolve_inside_root(root_abs, project_path, require_exists=True)
    if not relative_files:
        return {
            "protocol_version": ASPNET_PROTOCOL_VERSION,
            "coverage_status": "empty",
            "workspace_kind": "none",
            "semantic_enabled": False,
            "results": [],
            "diagnostics": [],
        }
    dll = ensure_worker_built(worker_project_path, verbose=verbose)
    request = {
        "protocol_version": ASPNET_PROTOCOL_VERSION,
        "root": root_abs,
        "files": relative_files,
        "semantic_mode": semantic_mode,
        "project_path": normalize_relative_path(project_path),
        "workspace_timeout_ms": max(5_000, int(workspace_timeout_ms)),
        "file_timeout_ms": max(5_000, int(file_timeout_ms)),
        "max_file_bytes": max(1, int(max_file_bytes)),
    }
    manifest = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
            manifest = handle.name
            json.dump(request, handle, ensure_ascii=True, sort_keys=True)
        result = subprocess.run(
            ["dotnet", dll, "--manifest", manifest],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=max(1.0, float(timeout_sec)), check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ASP.NET Roslyn worker failed ({result.returncode})\n"
                + "\n".join((result.stderr or result.stdout).splitlines()[-80:])
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid ASP.NET Roslyn worker JSON: {exc}") from exc
        if payload.get("protocol_version") != ASPNET_PROTOCOL_VERSION:
            raise RuntimeError(
                f"ASP.NET Roslyn protocol mismatch: {payload.get('protocol_version')!r}"
            )
        if not isinstance(payload.get("results"), list):
            raise RuntimeError("ASP.NET Roslyn response is missing results")
        return payload
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ASP.NET Roslyn worker timed out after {timeout_sec}s") from exc
    finally:
        if manifest:
            try:
                os.remove(manifest)
            except OSError:
                pass
