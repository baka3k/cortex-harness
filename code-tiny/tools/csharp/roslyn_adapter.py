"""Python orchestrator adapter for the CSharpRoslynWorker .NET subprocess.

Modeled on `tools/common/aspnet/roslyn_adapter.py`. Subprocess protocol:
- `dotnet <dll> --manifest <request.json>` -> JSON on stdout.
- Returns evidence with types, members, fields, events, delegates, parameters,
  usings, attributes, calls, project metadata, diagnostics, parse_meta.
- Roslyn-first strategy: Tree-sitter fallback in `csharp_analyzer.py` if subprocess
  or build fails.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple


CSHARP_ROSLYN_PROTOCOL_VERSION = "csharp-v1"
CSHARP_ROSLYN_MODEL_VERSION = "csharp-primary-v1"
CSHARP_ROSLYN_CACHE_VERSION = "csharp-v2026-09-10-1"


_BUILD_LOCK = threading.Lock()
_BUILD_CACHE: Dict[str, str] = {}


def default_worker_project() -> str:
    return os.path.join(
        os.path.dirname(__file__), "roslyn_worker", "CSharpRoslynWorker.csproj"
    )


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
    candidates: List[Tuple[str, str, str]] = []
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


def ensure_worker_built(
    project_path: Optional[str] = None, *, verbose: bool = False
) -> str:
    project = os.path.realpath(os.path.abspath(project_path or default_worker_project()))
    with _BUILD_LOCK:
        cached = _BUILD_CACHE.get(project)
        if cached and os.path.isfile(cached):
            return cached
        try:
            result = subprocess.run(
                ["dotnet", "build", project, "-c", "Release"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(f"dotnet is unavailable: {exc}") from exc
        if result.returncode != 0:
            tail = "\n".join(result.stdout.splitlines()[-80:])
            raise RuntimeError(
                f"C# Roslyn worker build failed ({result.returncode})\n{tail}"
            )
        dll = _worker_dll(project)
        if not os.path.isfile(dll):
            raise RuntimeError(f"C# Roslyn worker DLL was not produced: {dll}")
        _BUILD_CACHE[project] = dll
        if verbose:
            print(f"[csharp][roslyn] worker={dll}", flush=True)
        return dll


def is_roslyn_available() -> bool:
    """Lightweight probe: dotnet CLI present and worker builds.

    Used by orchestrators that want to decide between Roslyn and Tree-sitter
    before they ever call into the worker.
    """
    try:
        ensure_worker_built(verbose=False)
        return True
    except (RuntimeError, OSError):
        return False


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
    extract_members: bool = True,
    extract_semantic: bool = True,
    extract_attributes: bool = True,
    extract_xml_docs: bool = True,
    extract_calls_resolved: bool = True,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Invoke the Roslyn worker and return the raw JSON response.

    Raises RuntimeError if the worker fails or times out; callers should treat
    that as a fallback signal.
    """
    if semantic_mode not in {"auto", "on", "off"}:
        raise ValueError("semantic_mode must be auto, on, or off")

    root_abs = os.path.realpath(os.path.abspath(root))
    relative_files: List[str] = []
    for path in files:
        candidate_abs = os.path.realpath(os.path.abspath(path))
        try:
            common = os.path.commonpath((root_abs, candidate_abs))
        except ValueError:
            continue
        if common != root_abs or candidate_abs == root_abs:
            # File is outside the project root — skip (worker will reject it).
            continue
        rel = os.path.relpath(candidate_abs, root_abs).replace("\\", "/")
        if not rel.lower().endswith(".cs"):
            continue
        if not os.path.isfile(candidate_abs):
            continue
        relative_files.append(rel)
    relative_files = sorted(set(relative_files))

    project_rel = ""
    if project_path:
        project_abs = (
            project_path
            if os.path.isabs(project_path)
            else os.path.join(root_abs, project_path)
        )
        if os.path.isfile(project_abs):
            project_rel = os.path.relpath(project_abs, root_abs).replace("\\", "/")

    if not relative_files and not project_rel:
        return {
            "protocol_version": CSHARP_ROSLYN_PROTOCOL_VERSION,
            "coverage_status": "empty",
            "workspace_kind": "none",
            "semantic_enabled": False,
            "workspace_requested": False,
            "project_path": "",
            "results": [],
            "diagnostics": [],
            "project": None,
        }

    dll = ensure_worker_built(worker_project_path, verbose=verbose)
    request = {
        "protocol_version": CSHARP_ROSLYN_PROTOCOL_VERSION,
        "root": root_abs,
        "files": relative_files,
        "semantic_mode": semantic_mode,
        "project_path": project_rel,
        "workspace_timeout_ms": max(5_000, int(workspace_timeout_ms)),
        "file_timeout_ms": max(5_000, int(file_timeout_ms)),
        "max_file_bytes": max(1, int(max_file_bytes)),
        "extract_members": bool(extract_members),
        "extract_semantic": bool(extract_semantic),
        "extract_attributes": bool(extract_attributes),
        "extract_xml_docs": bool(extract_xml_docs),
        "extract_calls_resolved": bool(extract_calls_resolved),
    }
    manifest = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", encoding="utf-8", delete=False
        ) as handle:
            manifest = handle.name
            json.dump(request, handle, ensure_ascii=True, sort_keys=True)
        result = subprocess.run(
            ["dotnet", dll, "--manifest", manifest],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"C# Roslyn worker failed ({result.returncode})\n"
                + "\n".join((result.stderr or result.stdout).splitlines()[-80:])
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid C# Roslyn worker JSON: {exc}") from exc
        if payload.get("protocol_version") != CSHARP_ROSLYN_PROTOCOL_VERSION:
            raise RuntimeError(
                f"C# Roslyn protocol mismatch: {payload.get('protocol_version')!r}"
            )
        if not isinstance(payload.get("results"), list):
            raise RuntimeError("C# Roslyn response is missing results")
        return payload
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"C# Roslyn worker timed out after {timeout_sec}s"
        ) from exc
    finally:
        if manifest:
            try:
                os.remove(manifest)
            except OSError:
                pass


def parse_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map worker response metadata to the csharp_analyzer parse_meta shape."""
    coverage = payload.get("coverage_status", "empty")
    semantic_enabled = bool(payload.get("semantic_enabled"))
    workspace_kind = payload.get("workspace_kind", "none")
    if semantic_enabled and coverage == "full":
        parser_language = "csharp_roslyn_workspace"
    elif semantic_enabled:
        parser_language = "csharp_roslyn_syntax"
    else:
        parser_language = "csharp_roslyn_syntax"
    return {
        "parser_language": parser_language,
        "parser_available": True,
        "semantic_enabled": semantic_enabled,
        "has_error": False,
        "error_nodes": 0,
        "coverage_status": coverage,
        "roslyn_workspace_kind": workspace_kind,
        "worker_protocol": payload.get("protocol_version", ""),
    }
