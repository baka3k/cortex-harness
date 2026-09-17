"""VB6 ANTLR worker adapter (plan 260917-1200, phase 03).

Mirrors ``vb_roslyn_adapter.py``: thread-locked build cache over a Maven
build of the vendored ProLeap worker, a JSON manifest protocol, and a single
subprocess run whose stdout is one JSON document with per-file payloads.

Design notes (plan AD-02/Q5/AD-07):
- ``.frm``/``.ctl``/``.pag`` are materialized into temp ``.cls`` files by
  THIS adapter (not the worker) because ProLeap only creates ASG modules for
  ``.bas``/``.cls`` (upstream issue #20). Designer blocks are replaced with
  blank lines so worker line numbers match the ORIGINAL file numbers.
- The worker always receives the WHOLE project file list, even in
  incremental syncs, so cross-module resolution sees the complete program.
  The parse cache only filters OUTPUT (which payloads to re-embed/re-write),
  never the worker input.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

_BUILD_LOCK = threading.Lock()
_BUILD_CACHE: Dict[str, str] = {}

_WORKER_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "antlr_worker")
_WORKER_JAR = os.path.join(_WORKER_DIR, "worker", "target", "vb6-antlr-worker.jar")

_VB_NAME_LINE_RE = re.compile(r"^\s*Attribute\s+VB_Name\b", re.IGNORECASE)

#: extensions ProLeap's ASG accepts as real modules
_DIRECT_EXTS = {".bas", ".cls"}
#: extensions that must be materialized to temp .cls before parsing
_MATERIALIZED_EXTS = {".frm", ".ctl", ".pag"}


def _normalize_rel(path: str) -> str:
    return (path or "").replace("\\", "/")


def materializeDesignerModule(source_path: str, target_path: str) -> bool:
    """Copy a .frm/.ctl/.pag file to ``target_path`` as a .cls-compatible module.

    Everything before the first ``Attribute VB_Name`` line (the designer
    block, which ProLeap cannot turn into an ASG module) is replaced with
    blank lines so line numbers in the worker payload still refer to the
    ORIGINAL file. Returns True when a designer block was stripped.
    """

    with open(source_path, "r", encoding="utf-8", errors="ignore") as handle:
        lines = handle.read().split("\n")
    out_lines: List[str] = []
    seen_name = False
    stripped = 0
    for line in lines:
        if not seen_name:
            if _VB_NAME_LINE_RE.match(line):
                seen_name = True
                out_lines.append(line)
            else:
                stripped += 1
                out_lines.append("")
        else:
            out_lines.append(line)
    if not seen_name:
        # no Attribute VB_Name at all: hand the file over unchanged and let
        # the worker's module-name fallback deal with it
        shutil.copyfile(source_path, target_path)
        return False
    with open(target_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out_lines))
    return stripped > 0


def _worker_jar_path() -> str:
    return _WORKER_JAR


def _jar_stamp(jar_path: str) -> Optional[float]:
    try:
        return os.path.getmtime(jar_path)
    except OSError:
        return None


def _newest_source_mtime() -> float:
    """Newest mtime among poms and worker/vendor sources (stamp inputs)."""

    newest = 0.0
    for base in (_WORKER_DIR, os.path.join(_WORKER_DIR, "vendor", "proleap-vb6-parser")):
        pom = os.path.join(base, "pom.xml")
        if os.path.isfile(pom):
            newest = max(newest, os.path.getmtime(pom))
    for dirpath, dirnames, filenames in os.walk(_WORKER_DIR):
        dirnames[:] = [name for name in dirnames if name not in {"target", ".git"}]
        for name in filenames:
            if name.endswith((".java", ".g4")):
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
    return newest


def ensure_worker_built(*, verbose: bool = False) -> str:
    """Build the vb6-antlr-worker shaded jar if needed; return its path.

    Raises RuntimeError with the build tail when Maven or Java are missing or
    the build fails. Results are cached per-process under a lock (mirrors the
    Roslyn adapter pattern).
    """

    jar_path = _worker_jar_path()
    with _BUILD_LOCK:
        cached = _BUILD_CACHE.get(jar_path)
        if cached and os.path.exists(cached):
            return cached

        # stamp check: an existing fresh jar is reused without Maven (a
        # prebuilt jar keeps engine=antlr usable on machines without mvn)
        stamp = _jar_stamp(jar_path)
        if stamp is not None and stamp >= _newest_source_mtime():
            _BUILD_CACHE[jar_path] = jar_path
            return jar_path

        if shutil.which("java") is None or shutil.which("mvn") is None:
            raise RuntimeError(
                "vb6 antlr worker jar not built and java/mvn unavailable "
                "(engine=regex is available without them)"
            )

        proc = subprocess.run(
            ["mvn", "-q", "-f", os.path.join(_WORKER_DIR, "pom.xml"), "-DskipTests", "package"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not os.path.exists(jar_path):
            tail = "\n".join((proc.stdout or "").splitlines()[-50:])
            raise RuntimeError(f"vb6 antlr worker build failed ({proc.returncode})\n{tail}")

        _BUILD_CACHE[jar_path] = jar_path
        if verbose:
            print(f"[vb6][engine] antlr worker built: {jar_path}", flush=True)
        return jar_path


def find_vbp(root: str, files: List[str]) -> str:
    """Locate the .vbp governing the given files (nearest to root)."""

    candidates: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(".vbp"):
                candidates.append(os.path.join(dirpath, name))
    if not candidates:
        return ""
    candidates.sort(key=lambda path: (path.count(os.sep), len(path)))
    return candidates[0]


def parse_vb6_files_with_antlr(
    *,
    root: str,
    files: List[str],
    timeout_sec: float = 600.0,
    workspace_timeout_ms: int = 120000,
    parse_cache_version: str = "",
    verbose: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, Any]]:
    """Parse VB6 files through the ANTLR whole-program worker.

    Returns ``(payloads_by_relpath, errors_by_relpath, worker_meta)``.

    ``files`` must be the COMPLETE project file list (AD-02): cross-module
    resolution in the worker's Program requires every module, so callers
    must not shrink this to cache-miss files.
    """

    if not files:
        return {}, {}, {
            "workspace_kind": "none",
            "solution_or_project_path": "",
            "implements_map": {},
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

    jar_path = ensure_worker_built(verbose=verbose)

    manifest_file = None
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="vb6_antlr_")
        entries: List[Dict[str, str]] = []
        materialized = 0
        for rel in rel_files:
            entry = {"file_path": rel}
            ext = os.path.splitext(rel)[1].lower()
            if ext in _MATERIALIZED_EXTS:
                target = os.path.join(temp_dir, rel.replace("/", "__") + ".cls")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                materializeDesignerModule(os.path.join(root_abs, rel), target)
                entry["parse_path"] = target
                materialized += 1
            entries.append(entry)

        project_path = find_vbp(root_abs, files)
        project_rel = (
            _normalize_rel(os.path.relpath(project_path, root_abs))
            if project_path
            else ""
        )

        manifest = {
            "root": root_abs,
            "project": project_rel,
            "files": entries,
            "parse_cache_version": parse_cache_version,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            manifest_file = handle.name
            json.dump(manifest, handle, ensure_ascii=True)

        cmd = [
            "java", "-jar", jar_path,
            "--manifest", manifest_file,
            "--workspace-timeout-ms", str(max(5000, int(workspace_timeout_ms))),
        ]
        if parse_cache_version:
            cmd += ["--parse-cache-version", parse_cache_version]

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
                "vb6 antlr worker execution failed "
                f"(code={proc.returncode})\nSTDERR:\n{stderr_tail}\nSTDOUT:\n{stdout_tail}"
            )

        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            snippet = (proc.stdout or "")[:2000]
            raise RuntimeError(f"invalid vb6 antlr worker json output: {exc}\n{snippet}") from exc

        payloads: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        for item in data.get("files", []) or []:
            rel = _normalize_rel(str(item.get("file_path") or "").strip())
            ok = bool(item.get("ok", False))
            payload = item.get("payload")
            if ok and isinstance(payload, dict):
                payloads[rel] = payload
            elif rel:
                errors[rel] = str(item.get("error") or "vb6 antlr parse failed")

        meta: Dict[str, Any] = dict(data.get("worker_meta") or {})
        meta.setdefault("workspace_kind", "vbp")
        meta["materialized_files"] = materialized
        return payloads, errors, meta
    finally:
        if manifest_file and os.path.exists(manifest_file):
            try:
                os.remove(manifest_file)
            except OSError:
                pass
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
